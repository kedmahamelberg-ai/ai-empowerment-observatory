#!/usr/bin/env python3
"""Build the public, versioned relationship-pattern artifact for a weekly release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supabase import Client, create_client

from symbiosis_common import (
    CODEBOOK_VERSION,
    CORE_FOUR,
    PARTIALS,
    PLAIN_LABELS,
    TECHNICAL_LABELS,
    final_payload_from_classification,
)

ROOT = Path(__file__).resolve().parents[1]
RELEASES_DIR = ROOT / "data" / "releases"
OUTPUT_DIR = ROOT / "data" / "symbiosis"
CURRENT_PATH = OUTPUT_DIR / "current.json"
INDEX_PATH = OUTPUT_DIR / "index.json"


class PublishError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise PublishError(f"{name} is missing.")
    return value


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise PublishError(f"Missing JSON file: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PublishError(f"Expected a JSON object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def release_path(release_id: str | None) -> Path:
    if release_id:
        return RELEASES_DIR / "weekly" / f"{release_id}.json"
    return RELEASES_DIR / "current.json"


def unit_ids(release: dict[str, Any]) -> tuple[list[str], list[str], dict[str, dict[str, Any]]]:
    articles: set[str] = set()
    events: set[str] = set()
    evidence_by_event: dict[str, dict[str, Any]] = {}

    # Coverage expectations come from the weekly release Coverage Lens, not only
    # from event-source membership. This preserves an exact human-review gate.
    coverage_rows = release.get("units", {}).get("coverage_articles", []) or []
    for row in coverage_rows:
        if not isinstance(row, dict) or not row.get("article_id"):
            continue
        if row.get("classification", {}).get("ai_relevant") is False:
            continue
        articles.add(str(row["article_id"]))

    for event in release.get("evidence") or []:
        if not isinstance(event, dict):
            continue
        if event.get("classification", {}).get("ai_relevant") is False:
            continue
        event_id = str(event.get("effective_event_id") or event.get("event_id") or "").strip()
        if event_id:
            events.add(event_id)
            evidence_by_event[event_id] = event
        if not coverage_rows:
            for article_id in event.get("member_article_ids") or []:
                if article_id:
                    articles.add(str(article_id))
            for source in event.get("sources") or []:
                if isinstance(source, dict) and source.get("article_id"):
                    articles.add(str(source["article_id"]))
    return sorted(articles), sorted(events), evidence_by_event


def latest_rows(
    client: Client,
    *,
    release_id: str,
    lens: str,
    ids: list[str],
) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    column = "article_id" if lens == "coverage" else "event_id"
    rows: list[dict[str, Any]] = []
    for start in range(0, len(ids), 100):
        response = (
            client.table("symbiosis_classifications")
            .select("*,symbiosis_classification_runs!inner(status)")
            .eq("codebook_version", CODEBOOK_VERSION)
            .eq("symbiosis_classification_runs.status", "success")
            .eq("release_id", release_id)
            .eq("lens", lens)
            .in_(column, ids[start:start + 100])
            .execute()
        )
        rows.extend(getattr(response, "data", None) or [])
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get(column) or "")
        if key:
            latest.setdefault(key, row)
    return latest


def configuration_summary(rows: dict[str, dict[str, Any]], expected_ids: list[str]) -> dict[str, Any]:
    reviewed_rows = [row for unit_id, row in rows.items() if unit_id in expected_ids and final_payload_from_classification(row)["reviewed"]]
    configurations = [str(final_payload_from_classification(row)["configuration"]) for row in reviewed_rows]
    counts = Counter(configurations)
    complete_total = sum(counts[value] for value in CORE_FOUR)
    partial_total = sum(counts[value] for value in PARTIALS)
    core_distribution = {
        value: round(counts[value] / complete_total, 6) if complete_total else 0.0
        for value in sorted(CORE_FOUR)
    }
    return {
        "expected_units": len(expected_ids),
        "classified_units": sum(1 for unit_id in expected_ids if unit_id in rows),
        "reviewed_units": len(reviewed_rows),
        "unreviewed_units": len(expected_ids) - len(reviewed_rows),
        "configuration_counts": {key: counts.get(key, 0) for key in PLAIN_LABELS},
        "complete_configuration_count": complete_total,
        "partial_signal_count": partial_total,
        "no_clear_relational_signal_count": counts.get("no_clear_relational_signal", 0),
        "ambiguous_relational_signal_count": counts.get("ambiguous_relational_signal", 0),
        "insufficient_evidence_count": counts.get("insufficient_evidence", 0),
        "core_four_distribution": core_distribution,
        "denominator_note": (
            "Core-four percentages use only human-reviewed complete two-sided configurations. "
            "One-sided signals, no-clear-signal cases, and insufficient-evidence cases are reported separately."
        ),
    }


def empowerment_secondary_summary(rows: dict[str, dict[str, Any]], expected_ids: list[str]) -> dict[str, Any]:
    finals = [
        final_payload_from_classification(rows[unit_id])
        for unit_id in expected_ids
        if unit_id in rows and final_payload_from_classification(rows[unit_id])["reviewed"]
    ]
    counts = Counter(str(item.get("empowerment_status") or "unclear") for item in finals)
    scores: list[float] = []
    for item in finals:
        status = str(item.get("empowerment_status") or "unclear")
        degree = int(item.get("empowerment_degree") or 0)
        if status == "expanding":
            scores.append(degree / 3.0)
        elif status == "contracting":
            scores.append(-degree / 3.0)
        elif status in {"mixed", "non_empowerment"}:
            scores.append(0.0)
    total = len(finals)
    return {
        "reviewed_units": total,
        "scored_units": len(scores),
        "excluded_unclear": counts.get("unclear", 0),
        "empowerment_index": round((sum(scores) / len(scores)) * 100, 4) if scores else None,
        "status_counts": {
            key: counts.get(key, 0)
            for key in ["expanding", "contracting", "mixed", "non_empowerment", "unclear"]
        },
        "status_distribution": {
            key: round(counts.get(key, 0) / total, 6) if total else 0.0
            for key in ["expanding", "contracting", "mixed", "non_empowerment", "unclear"]
        },
        "note": "Secondary empowerment values use the same human review decisions as the relationship lens. Unclear units are excluded from the index denominator.",
    }


def event_public_rows(
    event_rows: dict[str, dict[str, Any]],
    event_ids: list[str],
    evidence: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for event_id in event_ids:
        source = evidence.get(event_id, {})
        row = event_rows.get(event_id)
        final = final_payload_from_classification(row) if row else {
            "reviewed": False,
            "review_status": "not_classified",
            "configuration": None,
            "plain_label": "Relationship review pending",
            "technical_label": "Relationship review pending",
            "human_experience_type": None,
            "ai_expressive_role": None,
            "human_direction": None,
            "ai_direction": None,
            "evidence_status": None,
            "story_country_iso3s": [],
            "evidence_summary": "",
            "reasoning": "",
            "empowerment_status": None,
            "empowerment_degree": None,
            "empowerment_reasoning": None,
        }
        result.append(
            {
                "event_id": event_id,
                "event_title": source.get("event_title") or "Untitled development",
                "event_date": source.get("event_date"),
                "novelty_status": source.get("novelty_status"),
                "sources": source.get("sources") or [],
                "reviewed": final["reviewed"],
                "review_status": final["review_status"],
                "configuration": final["configuration"],
                "plain_label": final["plain_label"],
                "technical_label": final["technical_label"],
                "human_experience_type": final["human_experience_type"],
                "ai_expressive_role": final["ai_expressive_role"],
                "human_direction": final["human_direction"],
                "ai_direction": final["ai_direction"],
                "evidence_status": final["evidence_status"],
                "story_country_iso3s": final["story_country_iso3s"],
                "evidence_summary": final["evidence_summary"],
                "reasoning": final["reasoning"],
                "empowerment_secondary": {
                    "status": final["empowerment_status"],
                    "degree": final["empowerment_degree"],
                    "reasoning": final["empowerment_reasoning"],
                },
            }
        )
    return result


def normalized_hash(payload: dict[str, Any]) -> str:
    copy = json.loads(json.dumps(payload))
    copy.pop("generated_at", None)
    copy.pop("content_sha256", None)
    copy.pop("revision", None)
    return hashlib.sha256(json.dumps(copy, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def archive_and_revision(target: Path, new_payload: dict[str, Any]) -> tuple[int, bool]:
    if not target.exists():
        return 1, True
    old = read_json(target)
    if normalized_hash(old) == normalized_hash(new_payload):
        return int(old.get("revision") or 1), False
    old_revision = int(old.get("revision") or 1)
    release_id = str(old.get("release_id") or new_payload["release_id"])
    archive = OUTPUT_DIR / "weekly" / "archive" / release_id / f"revision-{old_revision}.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, archive)
    return old_revision + 1, True


def update_index(payload: dict[str, Any]) -> None:
    index = read_json(INDEX_PATH) if INDEX_PATH.exists() else {
        "schema_version": "aieo_symbiosis_index_v1.0",
        "weekly": [],
    }
    rows = [row for row in index.get("weekly", []) if row.get("release_id") != payload["release_id"]]
    rows.append(
        {
            "release_id": payload["release_id"],
            "revision": payload["revision"],
            "period_start": payload["period_start"],
            "period_end": payload["period_end"],
            "public_status": payload["public_status"],
            "event_reviewed": payload["review"]["event_reviewed"],
            "event_total": payload["review"]["event_total"],
            "coverage_reviewed": payload["review"]["coverage_reviewed"],
            "coverage_total": payload["review"]["coverage_total"],
        }
    )
    rows.sort(key=lambda row: str(row.get("period_start") or ""))
    index.update(
        {
            "updated_at": now_iso(),
            "current_release_id": payload["release_id"],
            "weekly": rows,
        }
    )
    write_json(INDEX_PATH, index)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", default="")
    parser.add_argument("--require-complete", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    release = read_json(release_path(args.release_id or None))
    release_id = str(release.get("release_id") or "").strip()
    if not release_id:
        raise PublishError("Release JSON lacks release_id.")
    article_ids, event_ids, evidence = unit_ids(release)
    client: Client = create_client(required_env("SUPABASE_URL"), required_env("SUPABASE_SECRET_KEY"))
    coverage_rows = latest_rows(client, release_id=release_id, lens="coverage", ids=article_ids)
    event_rows = latest_rows(client, release_id=release_id, lens="event", ids=event_ids)
    coverage_summary = configuration_summary(coverage_rows, article_ids)
    event_summary = configuration_summary(event_rows, event_ids)
    coverage_empowerment = empowerment_secondary_summary(coverage_rows, article_ids)
    event_empowerment = empowerment_secondary_summary(event_rows, event_ids)
    event_complete = event_summary["reviewed_units"] == event_summary["expected_units"] and event_summary["expected_units"] > 0
    coverage_complete = coverage_summary["reviewed_units"] == coverage_summary["expected_units"] and coverage_summary["expected_units"] > 0
    complete = event_complete and coverage_complete
    if args.require_complete and not complete:
        raise PublishError(
            "Human review is incomplete: "
            f"events {event_summary['reviewed_units']}/{event_summary['expected_units']}; "
            f"coverage {coverage_summary['reviewed_units']}/{coverage_summary['expected_units']}."
        )

    payload: dict[str, Any] = {
        "schema_version": "aieo_symbiosis_public_v1.0",
        "release_id": release_id,
        "release_type": "weekly_relationship_lens",
        "revision": 1,
        "period_start": release.get("period_start"),
        "period_end": release.get("period_end"),
        "generated_at": now_iso(),
        "codebook_version": CODEBOOK_VERSION,
        "public_status": "human_reviewed" if complete else "review_in_progress",
        "scope_note": (
            "This lens classifies how source evidence represents human-AI relations. "
            "It does not claim objective system performance, consciousness, intentions, or biological fitness."
        ),
        "review": {
            "complete": complete,
            "event_complete": event_complete,
            "coverage_complete": coverage_complete,
            "event_reviewed": event_summary["reviewed_units"],
            "event_total": event_summary["expected_units"],
            "coverage_reviewed": coverage_summary["reviewed_units"],
            "coverage_total": coverage_summary["expected_units"],
        },
        "definitions": {
            "mutualism": PLAIN_LABELS["mutualism"],
            "ai_benefiting_parasitism": PLAIN_LABELS["ai_benefiting_parasitism"],
            "human_benefiting_parasitism": PLAIN_LABELS["human_benefiting_parasitism"],
            "competition": PLAIN_LABELS["competition"],
            "partial_signals": "Only one side of the relationship is established by the source evidence.",
            "no_clear_relational_signal": PLAIN_LABELS["no_clear_relational_signal"],
            "insufficient_evidence": PLAIN_LABELS["insufficient_evidence"],
        },
        "event": event_summary,
        "coverage": coverage_summary,
        "secondary_empowerment": {
            "event": event_empowerment,
            "coverage": coverage_empowerment,
        },
        "evidence": event_public_rows(event_rows, event_ids, evidence),
        "technical_labels": TECHNICAL_LABELS,
    }

    target = OUTPUT_DIR / "weekly" / f"{release_id}.json"
    revision, changed = archive_and_revision(target, payload)
    payload["revision"] = revision
    payload["content_sha256"] = normalized_hash(payload)
    if changed:
        write_json(target, payload)
        write_json(CURRENT_PATH, payload)
        update_index(payload)
    elif not CURRENT_PATH.exists() or read_json(CURRENT_PATH).get("release_id") == release_id:
        write_json(CURRENT_PATH, read_json(target))

    print(
        json.dumps(
            {
                "release_id": release_id,
                "revision": revision,
                "changed": changed,
                "public_status": payload["public_status"],
                "event_review": f"{event_summary['reviewed_units']}/{event_summary['expected_units']}",
                "coverage_review": f"{coverage_summary['reviewed_units']}/{coverage_summary['expected_units']}",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublishError as exc:
        import sys
        print(f"Symbiosis publication failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
