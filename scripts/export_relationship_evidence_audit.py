#!/usr/bin/env python3
"""Export a private, source-by-source full-body and classification audit.

The export deliberately contains source metadata and collection diagnostics,
but never the private article body text. It is designed for the Observatory
owner to inspect why each current-week source was or was not available to the
relationship classifier before deciding whether a new classification run is
warranted.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from supabase import Client, create_client

from build_relationship_audit import (
    ROOT,
    as_mapping,
    as_rows,
    build_audit,
    coverage_markets,
    event_id,
    load_inputs,
    write_json,
)


DEFAULT_OUTPUT_DIR = ROOT / "review" / "relationship-audit" / "export"
SCHEMA_VERSION = "aieo_private_full_body_audit_v2"
FULL_BODY_STAGE7C_VERSION = "7C.5_full_body_required"

POLICY_OR_ACCESS_OUTCOMES = {
    "blocked_paywall_or_login",
    "blocked_robots",
    "blocked_tdm_reserved",
    "blocked_access_control",
    "blocked_bot_challenge",
}
TECHNICAL_OUTCOMES = {
    "exception",
    "http_error",
    "robots_unavailable",
    "source_timeout",
    "tdm_unavailable",
    "too_little_extractable_text",
}


class ExportError(RuntimeError):
    """Raised when the private metadata audit cannot be exported safely."""


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise ExportError(f"{name} is missing.")
    return value


def chunks(values: list[str], size: int = 150) -> list[list[str]]:
    return [values[start : start + size] for start in range(0, len(values), size)]


def article_catalog(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return public source metadata and Stage 7C provenance for each source."""
    result: dict[str, dict[str, Any]] = {}
    markets = coverage_markets(release)
    classifier_version = str(
        as_mapping(release.get("lineage")).get("classifier_version") or ""
    )

    def stage7c_audit(classification: dict[str, Any]) -> dict[str, Any]:
        basis = str(classification.get("content_basis") or "not_recorded")
        if classifier_version == FULL_BODY_STAGE7C_VERSION:
            input_status = (
                "full_article_body_supplied"
                if basis == "full_text"
                else "not_run_no_full_article_body"
            )
        else:
            input_status = "legacy_or_unknown_input_policy"
        return {
            "classifier_version": classifier_version or "not_recorded",
            "content_basis": basis,
            "input_status": input_status,
            "empowerment_status": str(classification.get("empowerment_status") or ""),
            "requires_review": bool(classification.get("requires_review")),
        }

    for row in as_rows(as_mapping(release.get("units")).get("coverage_articles")):
        classification = as_mapping(row.get("classification"))
        article_id = str(row.get("article_id") or "").strip()
        if not article_id:
            continue
        result[article_id] = {
            "article_id": article_id,
            "publisher": str(row.get("publisher") or "Unknown source"),
            "headline": str(row.get("headline") or ""),
            "url": str(row.get("url") or row.get("canonical_url") or ""),
            "published_date": str(row.get("published_date") or row.get("displayed_date") or ""),
            "source_language": str(row.get("source_language") or row.get("language") or ""),
            "search_markets": sorted(
                {
                    str(value).strip().upper()
                    for value in (row.get("search_markets") or markets.get(article_id, []))
                    if str(value).strip()
                }
            ),
            "stage7c": stage7c_audit(classification),
        }
    for event in as_rows(release.get("evidence")):
        event_title = str(event.get("event_title") or event_id(event) or "Untitled development")
        for source in as_rows(event.get("sources")):
            article_id = str(source.get("article_id") or "").strip()
            if not article_id:
                continue
            result.setdefault(
                article_id,
                {
                    "article_id": article_id,
                    "publisher": str(source.get("publisher") or "Unknown source"),
                    "headline": str(source.get("headline") or ""),
                    "url": str(source.get("url") or ""),
                    "published_date": str(source.get("published_date") or ""),
                    "source_language": str(source.get("source_language") or ""),
                    "search_markets": markets.get(article_id, []),
                    "stage7c": stage7c_audit({}),
                },
            )
            result[article_id].setdefault("related_developments", []).append(event_title)
    for row in result.values():
        row["related_developments"] = sorted(set(row.get("related_developments") or []))
    return result


def current_body_snapshots(client: Client, article_ids: list[str]) -> dict[str, dict[str, Any]]:
    fields = (
        "article_id,source_url,source_domain,word_count,extraction_quality,"
        "retrieval_method,rights_status,rights_basis,robots_allowed,tdm_reservation,"
        "paywall_detected,retrieved_at,is_current"
    )
    result: dict[str, dict[str, Any]] = {}
    for batch in chunks(article_ids):
        response = (
            client.table("brief_article_content_snapshots")
            .select(fields)
            .eq("is_current", True)
            .in_("article_id", batch)
            .order("retrieved_at", desc=True)
            .execute()
        )
        for row in getattr(response, "data", None) or []:
            article_id = str(row.get("article_id") or "")
            if article_id and article_id not in result:
                result[article_id] = row
    return result


def safe_attempt_metadata(value: Any) -> dict[str, Any]:
    """Whitelist diagnostics. Never copy a future raw-body field by mistake."""
    raw = value if isinstance(value, dict) else {}
    keys = (
        "recovery_strategy_version",
        "candidate_kind",
        "robots_url",
        "robots_detail",
        "tdmrep_url",
        "tdm_check_state",
        "final_url",
        "word_count",
        "extraction_method",
        "extraction_quality",
        "recovered_from_alternate",
        "request_attempts",
        "redirect_chain",
        "recovery_trace",
        "error_class",
        "error_message",
        "metadata_note",
    )
    return {key: raw.get(key) for key in keys if key in raw}


def fetch_attempt_history(client: Client, article_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    fields = (
        "article_id,source_url,source_domain,outcome,attempted_at,http_status,"
        "retrieval_method,robots_allowed,tdm_reservation,tdm_policy_url,"
        "paywall_detected,response_content_type,response_bytes,elapsed_ms,metadata"
    )
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for batch in chunks(article_ids):
        response = (
            client.table("brief_article_fetch_attempts")
            .select(fields)
            .in_("article_id", batch)
            .order("attempted_at", desc=True)
            .execute()
        )
        for row in getattr(response, "data", None) or []:
            article_id = str(row.get("article_id") or "")
            if not article_id:
                continue
            result[article_id].append(
                {
                    "outcome": str(row.get("outcome") or "unknown"),
                    "attempted_at": str(row.get("attempted_at") or ""),
                    "source_url": str(row.get("source_url") or ""),
                    "source_domain": str(row.get("source_domain") or ""),
                    "http_status": row.get("http_status"),
                    "retrieval_method": str(row.get("retrieval_method") or ""),
                    "robots_allowed": row.get("robots_allowed"),
                    "tdm_reservation": row.get("tdm_reservation"),
                    "tdm_policy_url": row.get("tdm_policy_url"),
                    "paywall_detected": bool(row.get("paywall_detected")),
                    "response_content_type": str(row.get("response_content_type") or ""),
                    "response_bytes": row.get("response_bytes"),
                    "elapsed_ms": row.get("elapsed_ms"),
                    "metadata": safe_attempt_metadata(row.get("metadata")),
                }
            )
    for attempts in result.values():
        attempts.sort(key=lambda item: item["attempted_at"], reverse=True)
    return dict(result)


def recovery_category(outcome: str) -> tuple[str, str]:
    if outcome in POLICY_OR_ACCESS_OUTCOMES:
        return (
            "publisher_policy_or_access",
            "Do not bypass this source. Recheck its published policy later or use a licensed or publisher-provided source.",
        )
    if outcome in TECHNICAL_OUTCOMES:
        return (
            "technical_or_extraction_recovery",
            "Eligible for the safe recovery workflow: bounded retry, public embedded-data extraction, and publisher-linked public alternate checks.",
        )
    if outcome == "stored":
        return ("full_body_stored", "No recovery is required.")
    if outcome == "non_article_media":
        return (
            "non_article_media",
            "Use a separate transcript or publisher-licensed media source. Do not treat a player page as a full article body.",
        )
    return ("not_checked_or_unknown", "Run the source audit and safe recovery workflow.")


def private_body_metadata(
    snapshot: dict[str, Any] | None,
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    latest = attempts[0] if attempts else {}
    outcome = str(latest.get("outcome") or "never_attempted")
    category, action = recovery_category(outcome)
    if snapshot:
        return {
            "state": "full_article_body_stored",
            "word_count": int(snapshot.get("word_count") or 0),
            "extraction_quality": snapshot.get("extraction_quality"),
            "retrieval_method": str(snapshot.get("retrieval_method") or ""),
            "rights_status": str(snapshot.get("rights_status") or ""),
            "rights_basis": str(snapshot.get("rights_basis") or ""),
            "robots_allowed": snapshot.get("robots_allowed"),
            "tdm_reservation": snapshot.get("tdm_reservation"),
            "paywall_detected": bool(snapshot.get("paywall_detected")),
            "retrieved_at": str(snapshot.get("retrieved_at") or ""),
            "recovery_category": "full_body_stored",
            "recommended_next_action": "No recovery is required.",
            "body_text_exported": False,
            "attempt_history": attempts,
        }
    return {
        "state": "no_current_full_article_body",
        "latest_attempt_outcome": outcome,
        "latest_attempted_at": str(latest.get("attempted_at") or ""),
        "latest_http_status": latest.get("http_status"),
        "latest_retrieval_method": str(latest.get("retrieval_method") or ""),
        "latest_robots_allowed": latest.get("robots_allowed"),
        "latest_tdm_reservation": latest.get("tdm_reservation"),
        "paywall_detected": bool(latest.get("paywall_detected")),
        "recovery_category": category,
        "recommended_next_action": action,
        "body_text_exported": False,
        "attempt_history": attempts,
    }


def relationship_by_article(audit: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = defaultdict(list)
    for development in as_rows(audit.get("developments")):
        assessment = as_mapping(development.get("classification"))
        record = {
            "event_id": str(development.get("event_id") or ""),
            "event_title": str(development.get("title") or ""),
            "people_outcome": str(development.get("primary_outcome_label") or ""),
            "relationship_content_basis": str(assessment.get("content_basis") or ""),
            "evidence_status": str(assessment.get("evidence_status") or ""),
            "configuration": str(assessment.get("configuration") or ""),
        }
        for source in as_rows(development.get("sources")):
            article_id = str(source.get("article_id") or "").strip()
            if article_id:
                result[article_id].append(record)
    return {article_id: values for article_id, values in result.items()}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "article_id",
        "publisher",
        "headline",
        "url",
        "published_date",
        "source_language",
        "source_markets",
        "stage7c_classifier_version",
        "stage7c_content_basis",
        "stage7c_input_status",
        "stage7c_empowerment_status",
        "full_body_state",
        "latest_attempt_outcome",
        "recovery_category",
        "recommended_next_action",
        "word_count",
        "extraction_quality",
        "retrieval_method",
        "latest_http_status",
        "latest_attempted_at",
        "related_developments",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_classification_csv(path: Path, developments: list[dict[str, Any]]) -> None:
    """Write a one-row-per-development audit view for the owner.

    It intentionally provides classification provenance and public source
    metadata, not the stored body text. The owner can open the linked source
    pages while auditing any classification they choose.
    """
    fields = [
        "event_id",
        "title",
        "date",
        "people_outcome",
        "content_basis",
        "evidence_status",
        "body_coverage",
        "full_text_sources",
        "source_count",
        "source_markets",
        "story_country_iso3s",
        "audit_flags",
        "source_headlines",
        "source_urls",
    ]
    rows: list[dict[str, str]] = []
    for development in developments:
        classification = as_mapping(development.get("classification"))
        body = as_mapping(development.get("body_evidence"))
        sources = as_rows(development.get("sources"))
        rows.append(
            {
                "event_id": str(development.get("event_id") or ""),
                "title": str(development.get("title") or ""),
                "date": str(development.get("date") or ""),
                "people_outcome": str(development.get("primary_outcome_label") or ""),
                "content_basis": str(classification.get("content_basis") or ""),
                "evidence_status": str(classification.get("evidence_status") or ""),
                "body_coverage": str(body.get("body_coverage") or ""),
                "full_text_sources": str(body.get("full_text_sources") or 0),
                "source_count": str(body.get("source_count") or len(sources)),
                "source_markets": ", ".join(str(value) for value in development.get("source_markets") or []),
                "story_country_iso3s": ", ".join(str(value) for value in development.get("story_country_iso3s") or []),
                "audit_flags": " | ".join(str(value) for value in development.get("audit_flags") or []),
                "source_headlines": " | ".join(str(source.get("headline") or "") for source in sources),
                "source_urls": " | ".join(str(source.get("url") or "") for source in sources),
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", default="", help="Weekly release ID; blank means the current week")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for JSON and CSV exports")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    release, symbiosis = load_inputs(str(args.release_id or "").strip())
    relationship_audit = build_audit(release, symbiosis)
    catalog = article_catalog(release)
    article_ids = sorted(catalog)
    client: Client = create_client(required_env("SUPABASE_URL"), required_env("SUPABASE_SECRET_KEY"))
    snapshots = current_body_snapshots(client, article_ids)
    attempts_by_article = fetch_attempt_history(client, article_ids)
    relationships = relationship_by_article(relationship_audit)

    source_rows: list[dict[str, Any]] = []
    for article_id in article_ids:
        source = catalog[article_id]
        body = private_body_metadata(snapshots.get(article_id), attempts_by_article.get(article_id, []))
        source_rows.append(
            {
                **source,
                "private_body": body,
                "relationship_assessments": relationships.get(article_id, []),
            }
        )

    category_counts = Counter(
        str(row["private_body"].get("recovery_category") or "unknown")
        for row in source_rows
    )
    latest_outcome_counts = Counter(
        str(row["private_body"].get("latest_attempt_outcome") or "stored")
        for row in source_rows
        if row["private_body"].get("state") != "full_article_body_stored"
    )
    missing_sources = [row for row in source_rows if row["private_body"].get("state") != "full_article_body_stored"]

    csv_rows = []
    for row in source_rows:
        body = as_mapping(row.get("private_body"))
        csv_rows.append(
            {
                "article_id": row["article_id"],
                "publisher": row["publisher"],
                "headline": row["headline"],
                "url": row["url"],
                "published_date": row["published_date"],
                "source_language": row["source_language"],
                "source_markets": ", ".join(row.get("search_markets") or []),
                "stage7c_classifier_version": as_mapping(row.get("stage7c")).get("classifier_version", ""),
                "stage7c_content_basis": as_mapping(row.get("stage7c")).get("content_basis", ""),
                "stage7c_input_status": as_mapping(row.get("stage7c")).get("input_status", ""),
                "stage7c_empowerment_status": as_mapping(row.get("stage7c")).get("empowerment_status", ""),
                "full_body_state": body.get("state", ""),
                "latest_attempt_outcome": body.get("latest_attempt_outcome", "stored"),
                "recovery_category": body.get("recovery_category", ""),
                "recommended_next_action": body.get("recommended_next_action", ""),
                "word_count": body.get("word_count", ""),
                "extraction_quality": body.get("extraction_quality", ""),
                "retrieval_method": body.get("retrieval_method", body.get("latest_retrieval_method", "")),
                "latest_http_status": body.get("latest_http_status", ""),
                "latest_attempted_at": body.get("latest_attempted_at", ""),
                "related_developments": " | ".join(row.get("related_developments") or []),
            }
        )

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "release_id": relationship_audit["release_id"],
        "period_start": relationship_audit["period_start"],
        "period_end": relationship_audit["period_end"],
        "purpose": (
            "Private source-by-source audit of full article body availability, safe recovery status, and the evidence basis used by current classifications."
        ),
        "privacy": {
            "body_text_exported": False,
            "note": "This export never includes private article body text or raw HTML.",
        },
        "summary": {
            "source_article_count": len(source_rows),
            "stored_full_article_body_count": len(source_rows) - len(missing_sources),
            "missing_full_article_body_count": len(missing_sources),
            "recovery_category_counts": dict(sorted(category_counts.items())),
            "latest_missing_body_outcome_counts": dict(sorted(latest_outcome_counts.items())),
            "classification_audit_note": (
                "A source with no current full article body should be recovered or marked unavailable before a new full-body-only relationship classification run."
            ),
        },
        "sources": source_rows,
        "relationship_audit_summary": relationship_audit.get("summary") or {},
    }
    json_path = output_dir / "full-body-recovery-audit.json"
    csv_path = output_dir / "full-body-recovery-audit.csv"
    classification_csv_path = output_dir / "classification-audit.csv"
    developments = as_rows(relationship_audit.get("developments"))
    primary_outcome_counts = Counter(
        str(development.get("primary_outcome") or "unknown")
        for development in developments
    )
    payload["classification_audit"] = {
        "development_count": len(developments),
        "primary_outcome_counts": dict(sorted(primary_outcome_counts.items())),
        "developments": developments,
    }
    write_json(json_path, payload)
    write_csv(csv_path, csv_rows)
    write_classification_csv(classification_csv_path, developments)

    summary = {
        "release_id": payload["release_id"],
        "source_articles": len(source_rows),
        "stored_full_article_bodies": payload["summary"]["stored_full_article_body_count"],
        "missing_full_article_bodies": payload["summary"]["missing_full_article_body_count"],
        "recovery_category_counts": payload["summary"]["recovery_category_counts"],
        "json": str(json_path.relative_to(ROOT)),
        "csv": str(csv_path.relative_to(ROOT)),
        "classification_csv": str(classification_csv_path.relative_to(ROOT)),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if github_summary:
        with Path(github_summary).open("a", encoding="utf-8") as handle:
            handle.write("## Full article body recovery audit\n\n")
            handle.write(f"- Current-week source articles: **{summary['source_articles']}**\n")
            handle.write(f"- Full article bodies stored: **{summary['stored_full_article_bodies']}**\n")
            handle.write(f"- Sources without a full body: **{summary['missing_full_article_bodies']}**\n")
            handle.write("- The download includes a source audit and a development-level classification audit.\n")
            handle.write("- It contains metadata only, never private article text or raw HTML.\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExportError as error:
        raise SystemExit(f"FULL BODY AUDIT EXPORT ERROR: {error}") from error
