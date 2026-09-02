#!/usr/bin/env python3
"""Add the plain-language multi-signal layer to an existing weekly artifact.

This is a one-time, deterministic compatibility migration. It never changes a
legacy relationship classification. Older single-label rows are translated
into the public signals that can be known from those labels; signals that need
the new multi-label review remain explicitly unavailable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from symbiosis_common import (
    PUBLIC_SIGNAL_SCHEMA_VERSION,
    RELATIONSHIP_PATTERN_KEYS,
    normalize_distribution_signal,
    normalize_relationship_patterns,
    public_signals_from_patterns,
)

ROOT = Path(__file__).resolve().parents[1]
SYMBIOSIS_DIR = ROOT / "data" / "symbiosis"
RELEASES_DIR = ROOT / "data" / "releases"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected JSON object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def normalized_hash(payload: dict[str, Any]) -> str:
    comparable = json.loads(json.dumps(payload))
    comparable.pop("generated_at", None)
    comparable.pop("content_sha256", None)
    comparable.pop("revision", None)
    return hashlib.sha256(
        json.dumps(comparable, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def build_summary(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    pattern_counts = Counter()
    signal_counts = Counter()
    explicit_multi = 0
    distribution_coded = 0
    for row in evidence:
        patterns = row.get("relationship_patterns") or {}
        signals = row.get("public_signals") or {}
        explicit_multi += int(bool(row.get("multi_label_available")))
        distribution_coded += int(bool(row.get("distribution_coded")))
        for key in RELATIONSHIP_PATTERN_KEYS:
            pattern_counts[key] += int(bool(patterns.get(key)))
        for key in (
            "people_gaining",
            "people_losing_ground",
            "mixed_picture",
            "not_everyone_benefits",
            "not_clear_yet",
        ):
            signal_counts[key] += int(bool(signals.get(key)))
    total = len(evidence)
    return {
        "schema_version": PUBLIC_SIGNAL_SCHEMA_VERSION,
        "expected_units": total,
        "classified_units": total,
        "relationship_pattern_counts": {
            key: int(pattern_counts[key]) for key in RELATIONSHIP_PATTERN_KEYS
        },
        "people_signal_counts": {
            key: int(signal_counts[key])
            for key in (
                "people_gaining",
                "people_losing_ground",
                "mixed_picture",
                "not_everyone_benefits",
                "not_clear_yet",
            )
        },
        "availability": {
            "people_gaining": total > 0,
            "people_losing_ground": total > 0,
            "mixed_picture": explicit_multi == total and total > 0,
            "not_everyone_benefits": distribution_coded == total and total > 0,
            "not_clear_yet": total > 0,
        },
        "explicit_multi_label_units": explicit_multi,
        "distribution_coded_units": distribution_coded,
        "overlap_note": (
            "A development may contain more than one signal, so these counts "
            "do not have to add up to the weekly total."
        ),
        "legacy_compatibility": True,
    }


def migrate(payload: dict[str, Any]) -> dict[str, Any]:
    evidence = [row for row in (payload.get("evidence") or []) if isinstance(row, dict)]
    for row in evidence:
        configuration = str(row.get("configuration") or "")
        patterns, explicit = normalize_relationship_patterns(
            row.get("relationship_patterns"),
            fallback_configuration=configuration,
        )
        distribution, distribution_explicit = normalize_distribution_signal(
            row.get("distribution_signal")
        )
        signals = public_signals_from_patterns(
            patterns,
            configuration=configuration,
            human_direction=str(row.get("human_direction") or ""),
            evidence_status=str(row.get("evidence_status") or ""),
            distribution_signal=distribution,
        )
        row.update(
            {
                "relationship_patterns": patterns,
                "public_signals": signals,
                "distribution_signal": distribution,
                "public_takeaway": str(row.get("public_takeaway") or "").strip(),
                "multi_label_available": explicit,
                "distribution_coded": distribution_explicit,
            }
        )
    payload["schema_version"] = "aieo_symbiosis_public_v1.1"
    payload["people_signals"] = build_summary(evidence)
    payload["signal_migration"] = {
        "version": PUBLIC_SIGNAL_SCHEMA_VERSION,
        "mode": "legacy_single_label_compatibility",
        "note": (
            "Existing labels were preserved. Mixed-picture and unequal-benefit "
            "counts remain unavailable until multi-label review is complete."
        ),
    }
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", default="", help="Blank means the canonical current release")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    canonical = str(read_json(RELEASES_DIR / "current.json").get("release_id") or "").strip()
    release_id = str(args.release_id or canonical).strip()
    if not release_id:
        raise SystemExit("Could not resolve a release ID.")
    target = SYMBIOSIS_DIR / "weekly" / f"{release_id}.json"
    if not target.exists():
        raise SystemExit(f"Missing weekly relationship artifact: {target.relative_to(ROOT)}")
    original = read_json(target)
    if original.get("people_signals") and str(original.get("schema_version") or "").endswith("v1.1"):
        print(f"{release_id} already has the public people-signal layer; no changes made.")
        return 0

    old_revision = int(original.get("revision") or 1)
    archive = SYMBIOSIS_DIR / "weekly" / "archive" / release_id / f"revision-{old_revision}.json"
    if archive.exists():
        raise SystemExit(f"Archive already exists; refusing to overwrite {archive.relative_to(ROOT)}")
    archive.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, archive)

    migrated = migrate(json.loads(json.dumps(original)))
    migrated["revision"] = old_revision + 1
    migrated["generated_at"] = now_iso()
    migrated["content_sha256"] = normalized_hash(migrated)
    write_json(target, migrated)
    if release_id == canonical:
        write_json(SYMBIOSIS_DIR / "current.json", migrated)

    index_path = SYMBIOSIS_DIR / "index.json"
    index = read_json(index_path)
    for row in index.get("weekly") or []:
        if isinstance(row, dict) and str(row.get("release_id") or "") == release_id:
            row["revision"] = migrated["revision"]
    index["updated_at"] = now_iso()
    index["current_release_id"] = canonical
    write_json(index_path, index)

    print(
        json.dumps(
            {
                "release_id": release_id,
                "revision": migrated["revision"],
                "promoted_to_current": release_id == canonical,
                "people_signal_counts": migrated["people_signals"]["people_signal_counts"],
                "availability": migrated["people_signals"]["availability"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
