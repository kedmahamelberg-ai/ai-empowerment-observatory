#!/usr/bin/env python3
"""Apply versioned human symbiosis decisions to Supabase."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supabase import Client, create_client

from symbiosis_common import (
    AI_ROLES,
    CODEBOOK_VERSION,
    EMPOWERMENT_STATUSES,
    EVIDENCE_STATUSES,
    HUMAN_TYPES,
    RELATIONSHIP_PATTERN_KEYS,
    TECHNICAL_LABELS,
    derive_configuration,
    normalize_ai_role,
    normalize_distribution_signal,
    normalize_evidence_status,
    normalize_human_type,
    normalize_relationship_patterns,
    public_signals_from_patterns,
)

ROOT = Path(__file__).resolve().parents[1]
DECISIONS_PATH = ROOT / "validation" / "symbiosis-reviewed-decisions.json"
SUMMARY_PATH = ROOT / "review" / "symbiosis" / "apply-summary.json"


class ReviewApplyError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise ReviewApplyError(f"{name} is missing.")
    return value


def load_decisions(path: Path = DECISIONS_PATH) -> dict[str, Any]:
    if not path.exists():
        raise ReviewApplyError(f"Missing decisions file: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("codebook_version") != CODEBOOK_VERSION:
        raise ReviewApplyError(
            f"Decision codebook {payload.get('codebook_version')} does not match {CODEBOOK_VERSION}."
        )
    if not isinstance(payload.get("decisions"), list):
        raise ReviewApplyError("Decision file must contain a decisions array.")
    return payload


def latest_classification(client: Client, decision: dict[str, Any]) -> dict[str, Any] | None:
    lens = str(decision.get("lens") or "").strip()
    if lens not in {"coverage", "event"}:
        raise ReviewApplyError(f"Invalid lens in {decision.get('decision_id')}: {lens}")

    query = (
        client.table("symbiosis_classifications")
        .select("*")
        .eq("codebook_version", CODEBOOK_VERSION)
        .eq("lens", lens)
    )

    # Release-specific unit_key is the authoritative identity. The same article
    # or event may be represented differently in another weekly release.
    unit_key = str(decision.get("unit_key") or "").strip()
    if unit_key:
        query = query.eq("unit_key", unit_key)
    else:
        release_id = str(decision.get("release_id") or "").strip()
        if release_id:
            query = query.eq("release_id", release_id)
        if lens == "coverage":
            article_id = str(decision.get("article_id") or "").strip()
            if not article_id:
                raise ReviewApplyError(
                    f"Coverage decision {decision.get('decision_id')} lacks unit_key and article_id."
                )
            query = query.eq("article_id", article_id)
        else:
            event_id = str(decision.get("event_id") or "").strip()
            if not event_id:
                raise ReviewApplyError(
                    f"Event decision {decision.get('decision_id')} lacks unit_key and event_id."
                )
            query = query.eq("event_id", event_id)

    response = query.order("created_at", desc=True).limit(1).execute()
    rows = getattr(response, "data", None) or []
    return rows[0] if rows else None


def normalize_final(decision: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    status = str(decision.get("review_status") or "").strip()
    if status not in {"accepted", "corrected", "insufficient_evidence", "rejected"}:
        raise ReviewApplyError(f"Invalid review_status in {decision.get('decision_id')}: {status}")

    supplied_final = decision.get("final") if isinstance(decision.get("final"), dict) else {}

    if status == "accepted":
        human_type = normalize_human_type(row["model_human_experience_type"])
        ai_role = normalize_ai_role(row["model_ai_expressive_role"])
        evidence_status = normalize_evidence_status(row["evidence_status"])
        evidence_summary = str(supplied_final.get("evidence_summary") or row.get("model_summary") or "").strip()
        reasoning = str(supplied_final.get("reasoning") or evidence_summary).strip()
        empowerment_status = str(supplied_final.get("empowerment_status") or "unclear")
        empowerment_degree = int(supplied_final.get("empowerment_degree") or 0)
        empowerment_reasoning = str(supplied_final.get("empowerment_reasoning") or "").strip()
        story_countries = [
            str(value).strip().upper()
            for value in (supplied_final.get("story_country_iso3s") or row.get("country_iso3s") or [])
            if str(value).strip()
        ]
    else:
        final = supplied_final
        if not final:
            raise ReviewApplyError(f"Decision {decision.get('decision_id')} lacks final values.")
        human_type = normalize_human_type(final.get("human_experience_type"))
        ai_role = normalize_ai_role(final.get("ai_expressive_role"))
        evidence_status = normalize_evidence_status(final.get("evidence_status"))
        evidence_summary = str(final.get("evidence_summary") or "").strip()
        reasoning = str(final.get("reasoning") or evidence_summary).strip()
        empowerment_status = str(final.get("empowerment_status") or "unclear")
        empowerment_degree = int(final.get("empowerment_degree") or 0)
        empowerment_reasoning = str(final.get("empowerment_reasoning") or "").strip()
        story_countries = [
            str(value).strip().upper()
            for value in (final.get("story_country_iso3s") or [])
            if str(value).strip()
        ]

    if status == "insufficient_evidence":
        human_type = "unclear"
        ai_role = "unclear"
        evidence_status = "insufficient"

    if human_type not in HUMAN_TYPES:
        raise ReviewApplyError(f"Invalid human type in {decision.get('decision_id')}: {human_type}")
    if ai_role not in AI_ROLES:
        raise ReviewApplyError(f"Invalid AI role in {decision.get('decision_id')}: {ai_role}")
    if evidence_status not in EVIDENCE_STATUSES:
        raise ReviewApplyError(f"Invalid evidence status in {decision.get('decision_id')}: {evidence_status}")
    if empowerment_status is not None and empowerment_status not in EMPOWERMENT_STATUSES:
        raise ReviewApplyError(f"Invalid empowerment status in {decision.get('decision_id')}: {empowerment_status}")
    if empowerment_degree is not None and not 0 <= empowerment_degree <= 3:
        raise ReviewApplyError(f"Invalid empowerment degree in {decision.get('decision_id')}: {empowerment_degree}")

    configuration, human_direction, ai_direction, plain_label = derive_configuration(
        human_type,
        ai_role,
        evidence_status,
    )
    patterns, _ = normalize_relationship_patterns(
        supplied_final.get("relationship_patterns"),
        fallback_configuration=configuration,
    )
    if evidence_status == "insufficient":
        patterns = {key: False for key in RELATIONSHIP_PATTERN_KEYS}
    distribution_signal, _ = normalize_distribution_signal(
        supplied_final.get("distribution_signal")
    )
    public_signals = public_signals_from_patterns(
        patterns,
        configuration=configuration,
        human_direction=human_direction,
        evidence_status=evidence_status,
        distribution_signal=distribution_signal,
    )
    return {
        "review_status": status,
        "human_experience_type": human_type,
        "ai_expressive_role": ai_role,
        "human_direction": human_direction,
        "ai_direction": ai_direction,
        "configuration": configuration,
        "plain_label": plain_label,
        "evidence_status": evidence_status,
        "story_country_iso3s": story_countries,
        "evidence_summary": evidence_summary,
        "reasoning": reasoning,
        "empowerment_status": empowerment_status,
        "empowerment_degree": empowerment_degree,
        "empowerment_reasoning": empowerment_reasoning,
        "relationship_patterns": patterns,
        "public_signals": public_signals,
        "distribution_signal": distribution_signal,
        "public_takeaway": str(
            supplied_final.get("public_takeaway")
            or evidence_summary
            or reasoning
            or ""
        ).strip(),
    }


def update_article_evidence(
    client: Client,
    article_id: str,
    final: dict[str, Any],
    *,
    source_urls: list[str],
    reviewer_name: str,
) -> None:
    response = (
        client.table("articles")
        .select("article_id,source_metadata")
        .eq("article_id", article_id)
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    if not rows:
        return
    metadata = rows[0].get("source_metadata") if isinstance(rows[0].get("source_metadata"), dict) else {}
    metadata = dict(metadata)
    if final.get("evidence_summary"):
        metadata["human_evidence_summary"] = final["evidence_summary"]
        metadata["human_evidence_reviewed_at"] = now_iso()
    if final.get("story_country_iso3s"):
        metadata["human_story_country_iso3s"] = final["story_country_iso3s"]
    if source_urls:
        metadata["human_evidence_source_urls"] = sorted({str(url).strip() for url in source_urls if str(url).strip()})
    metadata["human_evidence_reviewer"] = reviewer_name
    client.table("articles").update({"source_metadata": metadata, "updated_at": now_iso()}).eq("article_id", article_id).execute()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Fail when any decision cannot be matched or the exported queue is incomplete")
    parser.add_argument(
        "--decisions-path",
        default=str(DECISIONS_PATH),
        help="Review decisions JSON. Defaults to the historical reviewed-decisions manifest.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_decisions(Path(args.decisions_path))
    decisions = manifest["decisions"]
    expected = int(manifest.get("expected_unit_count") or len(decisions))
    reviewed = int(manifest.get("reviewed_unit_count") or len(decisions))
    if args.strict and reviewed != expected:
        raise ReviewApplyError(
            f"Strict mode requires a complete review export: {reviewed} of {expected} units reviewed."
        )

    client: Client = create_client(required_env("SUPABASE_URL"), required_env("SUPABASE_SECRET_KEY"))
    summary = {
        "dry_run": args.dry_run,
        "strict": args.strict,
        "decision_count": len(decisions),
        "matched": 0,
        "applied": 0,
        "skipped": 0,
        "errors": [],
        "by_status": {},
    }

    for decision in decisions:
        decision_id = str(decision.get("decision_id") or "").strip()
        if not decision_id:
            summary["errors"].append("A decision lacks decision_id")
            continue
        try:
            row = latest_classification(client, decision)
            if row is None:
                summary["skipped"] += 1
                message = f"No current classification matched {decision_id}"
                summary["errors"].append(message)
                if args.strict:
                    raise ReviewApplyError(message)
                continue
            summary["matched"] += 1
            final = normalize_final(decision, row)
            summary["by_status"][final["review_status"]] = summary["by_status"].get(final["review_status"], 0) + 1
            if args.dry_run:
                continue

            update_payload = {
                "review_status": final["review_status"],
                "reviewer_name": str(decision.get("reviewer_name") or manifest.get("reviewer_name") or "Kedma Hamelberg"),
                "reviewed_at": now_iso(),
                "final_human_experience_type": final["human_experience_type"],
                "final_ai_expressive_role": final["ai_expressive_role"],
                "final_human_direction": final["human_direction"],
                "final_ai_direction": final["ai_direction"],
                "final_configuration": final["configuration"],
                "final_plain_label": final["plain_label"],
                "final_evidence_status": final["evidence_status"],
                "final_story_country_iso3s": final["story_country_iso3s"],
                "final_evidence_summary": final["evidence_summary"],
                "final_reasoning": final["reasoning"],
                "final_empowerment_status": final["empowerment_status"],
                "final_empowerment_degree": final["empowerment_degree"],
                "final_empowerment_reasoning": final["empowerment_reasoning"],
                "updated_at": now_iso(),
            }
            (
                client.table("symbiosis_classifications")
                .update(update_payload)
                .eq("symbiosis_classification_id", row["symbiosis_classification_id"])
                .execute()
            )
            final_payload = {**final, "technical_label": TECHNICAL_LABELS.get(final["configuration"], final["plain_label"])}
            review_row = {
                "decision_id": decision_id,
                "symbiosis_classification_id": row["symbiosis_classification_id"],
                "codebook_version": CODEBOOK_VERSION,
                "lens": row["lens"],
                "unit_key": row["unit_key"],
                "reviewer_name": update_payload["reviewer_name"],
                "review_status": final["review_status"],
                "final_payload": final_payload,
                "evidence_summary": final["evidence_summary"],
                "source_urls": decision.get("source_urls") or [],
                "notes": str(decision.get("notes") or ""),
                "reviewed_at": update_payload["reviewed_at"],
            }
            (
                client.table("symbiosis_reviews")
                .upsert(review_row, on_conflict="decision_id")
                .execute()
            )
            if row["lens"] == "coverage" and row.get("article_id"):
                update_article_evidence(
                    client,
                    str(row["article_id"]),
                    final,
                    source_urls=[str(url) for url in (decision.get("source_urls") or [])],
                    reviewer_name=update_payload["reviewer_name"],
                )
            summary["applied"] += 1
        except Exception as exc:
            summary["errors"].append(f"{decision_id}: {exc}")
            if args.strict:
                raise

    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    if args.strict and summary["errors"]:
        raise ReviewApplyError("Strict review application produced errors.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReviewApplyError as exc:
        import sys
        print(f"Symbiosis review application failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
