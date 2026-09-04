#!/usr/bin/env python3
"""Build a metadata-only audit index for the current relationship reading.

The public site must be able to explain what was read without publishing a
private article body.  This script turns the current weekly release and its
relationship artifact into a small, inspectable audit file.  It is deliberately
deterministic: no model, network request, or database write is involved.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RELEASES_DIR = ROOT / "data" / "releases"
SYMBIOSIS_DIR = ROOT / "data" / "symbiosis"
DEFAULT_OUTPUT = ROOT / "review" / "relationship-audit" / "latest.json"
SCHEMA_VERSION = "aieo_relationship_evidence_audit_v1"

PRIMARY_OUTCOMES = {
    "benefit_shown": "Clear benefit shown",
    "downside_shown": "Clear downside shown",
    "benefit_and_downside": "Both benefit and downside shown",
    "no_clear_people_change": "No clear change for people",
    "too_little_evidence": "Too little evidence",
}


class AuditError(RuntimeError):
    """Raised when the published weekly files cannot support an audit."""


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise AuditError(f"Missing JSON file: {path.relative_to(ROOT)}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AuditError(f"Expected a JSON object: {path.relative_to(ROOT)}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def event_id(row: dict[str, Any]) -> str:
    return str(row.get("effective_event_id") or row.get("event_id") or "").strip()


def as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in (value or []) if isinstance(row, dict)]


def integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def primary_outcome(row: dict[str, Any]) -> str:
    """Return one mutually exclusive, plain-language people outcome."""
    signals = as_mapping(row.get("public_signals"))
    gaining = bool(signals.get("people_gaining"))
    losing = bool(signals.get("people_losing_ground"))
    if gaining and losing:
        return "benefit_and_downside"
    if gaining:
        return "benefit_shown"
    if losing:
        return "downside_shown"
    if str(row.get("evidence_status") or "") == "insufficient":
        return "too_little_evidence"
    return "no_clear_people_change"


def body_label(row: dict[str, Any]) -> str:
    summary = as_mapping(row.get("evidence_basis_summary"))
    full_sources = integer(summary.get("full_text_sources"))
    coverage = str(summary.get("body_coverage") or "")
    basis = str(row.get("content_basis") or "")
    if coverage == "owner_supplied_full_body" or basis == "full_text_supplied_by_owner":
        return "Full article supplied by the owner"
    if coverage == "all_sources":
        return "Full article used for every source"
    if full_sources > 0 or coverage == "some_sources" or basis == "full_text":
        return "Full article used with other source evidence"
    if basis == "article_summary":
        return "Summary or excerpt used"
    if basis == "headline_and_snippet":
        return "Headline and snippet used"
    return "Full article not available"


def coverage_markets(release: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, set[str]] = {}
    for row in as_rows(as_mapping(release.get("units")).get("coverage_articles")):
        if as_mapping(row.get("classification")).get("ai_relevant") is False:
            continue
        article_id = str(row.get("article_id") or "").strip()
        if not article_id:
            continue
        result.setdefault(article_id, set()).update(
            str(value).strip().upper()
            for value in (row.get("search_markets") or [])
            if str(value).strip()
        )
    return {article_id: sorted(markets) for article_id, markets in result.items()}


def source_rows(event: dict[str, Any], markets_by_article: dict[str, list[str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in as_rows(event.get("sources")):
        article_id = str(source.get("article_id") or "").strip()
        rows.append(
            {
                "article_id": article_id,
                "publisher": str(source.get("publisher") or "Unknown source"),
                "headline": str(source.get("headline") or ""),
                "url": str(source.get("url") or ""),
                "published_date": str(source.get("published_date") or ""),
                "source_language": str(source.get("source_language") or ""),
                "search_markets": markets_by_article.get(article_id, []),
            }
        )
    return rows


def review_flags(row: dict[str, Any], primary: str, source_markets: list[str]) -> list[str]:
    summary = as_mapping(row.get("evidence_basis_summary"))
    full_sources = integer(summary.get("full_text_sources"))
    flags: list[str] = []
    if primary in {"benefit_shown", "downside_shown", "benefit_and_downside"} and full_sources == 0:
        flags.append("directional_result_without_full_article_body")
    if primary == "too_little_evidence":
        flags.append("insufficient_evidence")
    if not source_markets:
        flags.append("source_market_not_recorded")
    return flags


def build_audit(release: dict[str, Any], symbiosis: dict[str, Any]) -> dict[str, Any]:
    release_id = str(release.get("release_id") or "").strip()
    symbiosis_release_id = str(symbiosis.get("release_id") or "").strip()
    if not release_id:
        raise AuditError("Current release has no release_id.")
    if symbiosis_release_id != release_id:
        raise AuditError(
            f"Relationship release {symbiosis_release_id or 'missing'} does not match current release {release_id}."
        )

    expected = integer(as_mapping(symbiosis.get("people_signals")).get("expected_units"))
    if not expected:
        expected = integer(as_mapping(release.get("counts")).get("ai_relevant_event_records"))
    markets_by_article = coverage_markets(release)
    event_by_id = {event_id(row): row for row in as_rows(release.get("evidence")) if event_id(row)}
    evidence_rows = as_rows(symbiosis.get("evidence"))
    results: list[dict[str, Any]] = []
    primary_counts: Counter[str] = Counter()
    unequal_count = 0
    body_counts: Counter[str] = Counter()
    flags: Counter[str] = Counter()

    for relationship in sorted(evidence_rows, key=lambda row: (str(row.get("event_title") or ""), event_id(row))):
        item_id = event_id(relationship)
        release_event = event_by_id.get(item_id, {})
        sources = source_rows(release_event, markets_by_article)
        source_markets = sorted({market for source in sources for market in source["search_markets"]})
        primary = primary_outcome(relationship)
        primary_counts[primary] += 1
        signals = as_mapping(relationship.get("public_signals"))
        unequal = bool(signals.get("not_everyone_benefits"))
        unequal_count += int(unequal)
        evidence_summary = as_mapping(relationship.get("evidence_basis_summary"))
        body_coverage = str(evidence_summary.get("body_coverage") or "not_recorded")
        body_counts[body_coverage] += 1
        item_flags = review_flags(relationship, primary, source_markets)
        flags.update(item_flags)
        results.append(
            {
                "event_id": item_id,
                "title": str(release_event.get("event_title") or relationship.get("event_title") or item_id),
                "date": str(release_event.get("event_date") or ""),
                "primary_outcome": primary,
                "primary_outcome_label": PRIMARY_OUTCOMES[primary],
                "uneven_effect_noted": unequal,
                "classification": {
                    "content_basis": str(relationship.get("content_basis") or "headline_only"),
                    "evidence_status": str(relationship.get("evidence_status") or ""),
                    "reviewed": bool(relationship.get("reviewed")),
                    "review_status": str(relationship.get("review_status") or "pending"),
                    "configuration": str(relationship.get("configuration") or ""),
                    "model_confidence": relationship.get("model_confidence"),
                    "classification_audit": as_mapping(relationship.get("classification_audit")),
                },
                "body_evidence": {
                    "label": body_label(relationship),
                    "source_count": integer(evidence_summary.get("source_count")),
                    "full_text_sources": integer(evidence_summary.get("full_text_sources")),
                    "summary_sources": integer(evidence_summary.get("article_summary_sources")),
                    "snippet_sources": integer(evidence_summary.get("snippet_sources")),
                    "headline_only_sources": integer(evidence_summary.get("headline_only_sources")),
                    "body_coverage": body_coverage,
                },
                "source_markets": source_markets,
                "story_country_iso3s": [
                    str(value).strip().upper()
                    for value in (relationship.get("story_country_iso3s") or [])
                    if str(value).strip()
                ],
                "sources": sources,
                "audit_flags": item_flags,
            }
        )

    if len(results) != expected:
        raise AuditError(f"Relationship evidence has {len(results)} rows, but the weekly total is {expected}.")
    if sum(primary_counts.values()) != expected:
        raise AuditError("Primary people outcomes do not add up to the weekly total.")

    people = as_mapping(symbiosis.get("people_signals"))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "release_id": release_id,
        "period_start": str(release.get("period_start") or ""),
        "period_end": str(release.get("period_end") or ""),
        "public_status": str(symbiosis.get("public_status") or ""),
        "review": as_mapping(symbiosis.get("review")),
        "summary": {
            "development_count": expected,
            "primary_outcome_counts": {key: primary_counts.get(key, 0) for key in PRIMARY_OUTCOMES},
            "uneven_effect_count": unequal_count,
            "body_coverage_counts": dict(sorted(body_counts.items())),
            "reported_body_coverage_counts": as_mapping(people.get("body_coverage_counts")),
            "audit_flag_counts": dict(sorted(flags.items())),
            "plain_language_note": (
                "The five primary outcome counts add up to the weekly total. "
                "An uneven effect is a separate detail that can overlap with any outcome."
            ),
        },
        "developments": results,
    }


def load_inputs(release_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if release_id:
        release_path = RELEASES_DIR / "weekly" / f"{release_id}.json"
        symbiosis_path = SYMBIOSIS_DIR / "weekly" / f"{release_id}.json"
    else:
        release_path = RELEASES_DIR / "current.json"
        symbiosis_path = SYMBIOSIS_DIR / "current.json"
    return read_json(release_path), read_json(symbiosis_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", default="", help="Weekly release ID; blank means the current week")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Where to write the JSON audit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    release, symbiosis = load_inputs(str(args.release_id or "").strip())
    payload = build_audit(release, symbiosis)
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    write_json(output, payload)
    print(
        json.dumps(
            {
                "release_id": payload["release_id"],
                "development_count": payload["summary"]["development_count"],
                "primary_outcome_counts": payload["summary"]["primary_outcome_counts"],
                "output": str(output.relative_to(ROOT)),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as error:
        raise SystemExit(f"RELATIONSHIP AUDIT ERROR: {error}") from error
