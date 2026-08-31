#!/usr/bin/env python3
"""Create a release-bound relationship placeholder without querying Supabase.

The core weekly Observatory must never carry relationship numbers from an older
week, but the human-governed relationship review should not block publication of
the core Coverage/Event release. This script creates a same-release
``classification_in_progress`` artifact with the correct denominators and zero public
relationship findings. If an artifact for the same release already exists, it
is preserved so a rerun cannot erase review progress or reviewed findings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from symbiosis_common import CODEBOOK_VERSION, CORE_FOUR, PLAIN_LABELS, TECHNICAL_LABELS

ROOT = Path(__file__).resolve().parents[1]
RELEASE_PATH = ROOT / "data" / "releases" / "current.json"
OUTPUT_DIR = ROOT / "data" / "symbiosis"
CURRENT_PATH = OUTPUT_DIR / "current.json"
INDEX_PATH = OUTPUT_DIR / "index.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def hash_payload(payload: dict[str, Any]) -> str:
    clean = dict(payload)
    clean.pop("content_sha256", None)
    return hashlib.sha256(
        json.dumps(clean, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def empty_summary(expected: int) -> dict[str, Any]:
    return {
        "expected_units": expected,
        "classified_units": 0,
        "reviewed_units": 0,
        "unreviewed_units": expected,
        "configuration_counts": {key: 0 for key in PLAIN_LABELS},
        "complete_configuration_count": 0,
        "partial_signal_count": 0,
        "no_clear_relational_signal_count": 0,
        "ambiguous_relational_signal_count": 0,
        "insufficient_evidence_count": 0,
        "core_four_distribution": {key: 0.0 for key in sorted(CORE_FOUR)},
        "denominator_note": "Relationship classification is still running for this release.",
    }


def empty_empowerment() -> dict[str, Any]:
    statuses = ["expanding", "contracting", "mixed", "non_empowerment", "unclear"]
    return {
        "reviewed_units": 0,
        "scored_units": 0,
        "excluded_unclear": 0,
        "empowerment_index": None,
        "status_counts": {key: 0 for key in statuses},
        "status_distribution": {key: 0.0 for key in statuses},
        "note": "The core weekly empowerment lens is published separately; this relationship-review placeholder contains no secondary relationship-derived values yet.",
    }


def update_index(payload: dict[str, Any]) -> None:
    index = load(INDEX_PATH, {"schema_version": "aieo_symbiosis_index_v1.0", "weekly": []})
    if not isinstance(index, dict):
        index = {"schema_version": "aieo_symbiosis_index_v1.0", "weekly": []}
    rows = [
        row for row in (index.get("weekly") or [])
        if str(row.get("release_id") or "") != payload["release_id"]
    ]
    rows.append({
        "release_id": payload["release_id"],
        "revision": int(payload.get("revision") or 1),
        "period_start": payload.get("period_start"),
        "period_end": payload.get("period_end"),
        "public_status": payload.get("public_status"),
        "event_reviewed": 0,
        "event_total": payload["review"]["event_total"],
        "coverage_reviewed": 0,
        "coverage_total": payload["review"]["coverage_total"],
    })
    rows.sort(key=lambda row: str(row.get("period_start") or ""))
    index.update({
        "schema_version": "aieo_symbiosis_index_v1.0",
        "updated_at": now_iso(),
        "current_release_id": payload["release_id"],
        "weekly": rows,
    })
    write(INDEX_PATH, index)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    release_path = (
        ROOT / "data" / "releases" / "weekly" / f"{args.release_id}.json"
        if str(args.release_id or "").strip()
        else RELEASE_PATH
    )
    release = load(release_path)
    if not isinstance(release, dict):
        raise SystemExit(f"Missing or invalid canonical release: {release_path}")
    release_id = str(release.get("release_id") or "").strip()
    if not release_id:
        raise SystemExit("Canonical release has no release_id.")

    target = OUTPUT_DIR / "weekly" / f"{release_id}.json"
    existing = load(target)
    replacement_revision = 1
    if isinstance(existing, dict) and str(existing.get("release_id") or "") == release_id:
        same_source = str(existing.get("source_release_sha256") or "") == str(release.get("content_sha256") or "")
        if same_source:
            # Never erase model classifications, review progress, or reviewed findings
            # when they are already bound to the exact same canonical release revision.
            write(CURRENT_PATH, existing)
            update_index(existing)
            print(json.dumps({
                "release_id": release_id,
                "status": "preserved_existing_relationship_artifact",
                "public_status": existing.get("public_status"),
            }, indent=2))
            return 0
        replacement_revision = int(existing.get("revision") or 1) + 1
        archive = OUTPUT_DIR / "weekly" / "archive" / release_id / f"revision-{int(existing.get('revision') or 1)}.json"
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.exists():
            shutil.copy2(target, archive)

    counts = release.get("counts") or {}
    coverage_n = int(counts.get("ai_relevant_articles") or 0)
    event_n = int(counts.get("ai_relevant_event_records") or 0)
    if coverage_n <= 0 or event_n <= 0:
        raise SystemExit(
            f"Canonical release has invalid relationship denominators: coverage={coverage_n}, event={event_n}."
        )

    payload: dict[str, Any] = {
        "schema_version": "aieo_symbiosis_public_v1.0",
        "release_id": release_id,
        "release_type": "weekly_relationship_lens",
        "revision": replacement_revision,
        "period_start": release.get("period_start"),
        "period_end": release.get("period_end"),
        "generated_at": now_iso(),
        "codebook_version": CODEBOOK_VERSION,
        "public_status": "classification_in_progress",
        "source_release_sha256": release.get("content_sha256"),
        "scope_note": (
            "This lens classifies how source evidence represents human-AI relations. "
            "Relationship classification runs after the core weekly release. Once available, "
            "the live distribution is model-coded and versioned, with accepted human corrections incorporated later."
        ),
        "review": {
            "complete": False,
            "event_complete": False,
            "coverage_complete": False,
            "event_reviewed": 0,
            "event_total": event_n,
            "coverage_reviewed": 0,
            "coverage_total": coverage_n,
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
        "event": empty_summary(event_n),
        "coverage": empty_summary(coverage_n),
        "secondary_empowerment": {
            "event": empty_empowerment(),
            "coverage": empty_empowerment(),
        },
        "evidence": [],
        "technical_labels": TECHNICAL_LABELS,
    }
    payload["content_sha256"] = hash_payload(payload)

    write(target, payload)
    write(CURRENT_PATH, payload)
    update_index(payload)
    print(json.dumps({
        "release_id": release_id,
        "status": "created_review_placeholder",
        "event_total": event_n,
        "coverage_total": coverage_n,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
