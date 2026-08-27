#!/usr/bin/env python3
"""Validate that symbiosis backfill inputs have item-level evidence.

Aggregate historical references are allowed and disclosed, but only releases with
article or event identifiers can enter the item-level classification and review
queue.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from symbiosis_common import release_identifier, release_review_scope

ROOT = Path(__file__).resolve().parents[1]
RELEASES_DIR = ROOT / "data" / "releases"


class InputValidationError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise InputValidationError(f"Expected a JSON object: {path}")
    return payload


def paths_for_scope(scope: str) -> list[Path]:
    if scope == "latest":
        return [RELEASES_DIR / "current.json"]
    paths: list[Path] = []
    for root in (RELEASES_DIR / "baselines", RELEASES_DIR / "weekly"):
        if root.exists():
            paths.extend(path for path in root.glob("*.json") if path.is_file())
    return sorted(paths, key=lambda path: (path.parent.name, path.name))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=["latest", "history"], default="history")
    args = parser.parse_args()

    reviewable: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    errors: list[str] = []

    for path in paths_for_scope(args.scope):
        if not path.exists():
            errors.append(f"Missing publication file: {path}")
            continue
        payload = read_json(path)
        source_path = str(path.relative_to(ROOT))
        release_id = release_identifier(payload, path)
        scope = release_review_scope(payload, source_path)
        if not release_id:
            errors.append(f"Publication has no stable release_id or snapshot_id: {source_path}")
            continue
        if scope["reviewable"]:
            reviewable.append(scope)
            continue
        if scope["aggregate_reference"]:
            excluded.append(scope)
            continue
        errors.append(
            "Publication has no item-level evidence and is not marked as an aggregate "
            f"historical reference: {source_path}"
        )

    if not reviewable:
        errors.append("No reviewable release with article or event identifiers was found.")

    result = {
        "status": "passed" if not errors else "failed",
        "scope": args.scope,
        "reviewable_release_count": len(reviewable),
        "excluded_aggregate_reference_count": len(excluded),
        "reviewable_releases": reviewable,
        "excluded_aggregate_references": excluded,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if errors:
        raise InputValidationError("; ".join(errors))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InputValidationError as exc:
        import sys

        print(f"Symbiosis input validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
