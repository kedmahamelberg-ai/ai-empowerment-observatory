#!/usr/bin/env python3
"""Audit whether every published historical AIEO unit has explicit human review."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from supabase import Client, create_client

from publish_symbiosis_release import latest_rows, read_json, unit_ids
from symbiosis_common import final_payload_from_classification

ROOT = Path(__file__).resolve().parents[1]
RELEASES_DIR = ROOT / "data" / "releases"
OUTPUT_PATH = ROOT / "review" / "symbiosis" / "completion-audit.json"


class CompletionAuditError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise CompletionAuditError(f"{name} is missing.")
    return value


def release_paths() -> list[Path]:
    paths: list[Path] = []
    for root in (RELEASES_DIR / "baselines", RELEASES_DIR / "weekly"):
        if not root.exists():
            continue
        paths.extend(path for path in root.glob("*.json") if path.is_file())
    return sorted(paths, key=lambda path: (str(read_json(path).get("period_start") or ""), path.name))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-complete", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client: Client = create_client(required_env("SUPABASE_URL"), required_env("SUPABASE_SECRET_KEY"))
    releases = []
    total_expected = 0
    total_reviewed = 0
    for path in release_paths():
        release = read_json(path)
        release_id = str(release.get("release_id") or path.stem)
        article_ids, event_ids, _ = unit_ids(release)
        coverage_rows = latest_rows(client, release_id=release_id, lens="coverage", ids=article_ids)
        event_rows = latest_rows(client, release_id=release_id, lens="event", ids=event_ids)
        coverage_reviewed = sum(
            1 for unit_id in article_ids
            if unit_id in coverage_rows and final_payload_from_classification(coverage_rows[unit_id])["reviewed"]
        )
        event_reviewed = sum(
            1 for unit_id in event_ids
            if unit_id in event_rows and final_payload_from_classification(event_rows[unit_id])["reviewed"]
        )
        expected = len(article_ids) + len(event_ids)
        reviewed = coverage_reviewed + event_reviewed
        total_expected += expected
        total_reviewed += reviewed
        releases.append(
            {
                "release_id": release_id,
                "source_path": str(path.relative_to(ROOT)),
                "period_start": release.get("period_start"),
                "period_end": release.get("period_end"),
                "coverage_expected": len(article_ids),
                "coverage_classified": sum(1 for unit_id in article_ids if unit_id in coverage_rows),
                "coverage_reviewed": coverage_reviewed,
                "event_expected": len(event_ids),
                "event_classified": sum(1 for unit_id in event_ids if unit_id in event_rows),
                "event_reviewed": event_reviewed,
                "complete": reviewed == expected and expected > 0,
            }
        )

    payload = {
        "schema_version": "aieo_symbiosis_review_completion_v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "release_count": len(releases),
        "expected_units": total_expected,
        "reviewed_units": total_reviewed,
        "pending_units": total_expected - total_reviewed,
        "complete": total_expected > 0 and total_reviewed == total_expected,
        "releases": releases,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    if args.require_complete and not payload["complete"]:
        raise CompletionAuditError(
            f"Historical review is incomplete: {total_reviewed} of {total_expected} units reviewed."
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CompletionAuditError as exc:
        import sys
        print(f"Symbiosis completion audit failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
