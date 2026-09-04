#!/usr/bin/env python3
"""Normalize retired public wording across every published JSON snapshot.

This is deliberately repository-wide rather than release-specific.  It updates
current, weekly, archived, monthly, quarterly, annual, status, insight, and
relationship JSON so an old phrase or internal-label field cannot reappear
when a visitor opens an earlier public release. It never changes counts,
classifications, source URLs, or private data.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_JSON_PATHS = (
    ROOT / "data" / "releases",
    ROOT / "data" / "symbiosis",
    ROOT / "data" / "insights",
    ROOT / "data" / "reports",
    ROOT / "data" / "history",
    ROOT / "data" / "status",
    ROOT / "data" / "methodology",
)

EXACT_REPLACEMENTS = {
    "Model-coded, human-governed pilot release": "Evidence-based pilot release",
    "Current weekly model-coded brief": "Current weekly evidence brief",
    "model_coded_provisional": "current_evidence_reading",
    "model_coded_with_reviewed_corrections": "current_evidence_reading_with_reviewed_corrections",
    (
        "This lens classifies how source evidence represents human-AI relations. "
        "The weekly display is model-coded and versioned, with accepted human corrections "
        "incorporated when available. It does not claim objective system performance, "
        "consciousness, intentions, or biological fitness."
    ): (
        "This lens describes how current source evidence represents human-AI relations. "
        "Each people outcome is tied to the evidence available for that development. "
        "It does not claim objective system performance, consciousness, intentions, "
        "or biological fitness."
    ),
    (
        "This lens classifies how source evidence represents human-AI relations. "
        "Relationship classification runs after the core weekly release. Once available, "
        "the live distribution is model-coded and versioned, with accepted human corrections "
        "incorporated later."
    ): (
        "This lens describes how source evidence represents human-AI relations. "
        "Relationship classifications are published with the weekly release and remain "
        "traceable to the source evidence for each development."
    ),
    (
        "The live weekly display is model-coded and incorporates accepted human corrections "
        "as they arrive. The finalized human-reviewed fields remain separate until every "
        "required review is complete."
    ): (
        "The weekly display reflects the source evidence available for this release. "
        "Any completed review is recorded against the same release and its source links."
    ),
}
FORBIDDEN_PUBLIC_TEXT = (
    "model-coded",
    "model coded",
    "accepted human corrections replace model outputs as review proceeds",
    "ai-benefiting parasitism",
    "human-benefiting parasitism",
    "competition or co-constraint",
    "who gained and who was constrained?",
)
PRIVATE_PRESENTATION_KEYS = {
    "technical_label",
    "technical_labels",
}


def transform(value: Any) -> tuple[Any, int]:
    if isinstance(value, dict):
        changed = 0
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in PRIVATE_PRESENTATION_KEYS:
                changed += 1
                continue
            transformed, count = transform(item)
            result[key] = transformed
            changed += count
        return result, changed
    if isinstance(value, list):
        changed = 0
        result: list[Any] = []
        for item in value:
            transformed, count = transform(item)
            result.append(transformed)
            changed += count
        return result, changed
    if isinstance(value, str) and value in EXACT_REPLACEMENTS:
        return EXACT_REPLACEMENTS[value], 1
    return value, 0


def json_files() -> list[Path]:
    files: list[Path] = []
    for root in PUBLIC_JSON_PATHS:
        if root.is_file() and root.suffix == ".json":
            files.append(root)
        elif root.is_dir():
            files.extend(sorted(path for path in root.rglob("*.json") if path.is_file()))
    return sorted(dict.fromkeys(files))


def verify(path: Path) -> None:
    text = path.read_text(encoding="utf-8").casefold()
    for phrase in FORBIDDEN_PUBLIC_TEXT:
        if phrase in text:
            raise SystemExit(
                f"PUBLIC COPY ERROR: retired wording remains in {path.relative_to(ROOT)}: {phrase}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Verify only; do not write files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    changed_files = 0
    changed_values = 0
    for path in json_files():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise SystemExit(f"PUBLIC COPY ERROR: invalid JSON in {path.relative_to(ROOT)}: {error}")
        transformed, count = transform(payload)
        if count and args.check:
            raise SystemExit(
                f"PUBLIC COPY ERROR: {path.relative_to(ROOT)} still needs {count} public-copy replacements"
            )
        if count:
            path.write_text(
                json.dumps(transformed, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        if count:
            changed_files += 1
            changed_values += count
        target = json.loads(path.read_text(encoding="utf-8"))
        text = json.dumps(target, ensure_ascii=False).casefold()
        for phrase in FORBIDDEN_PUBLIC_TEXT:
            if phrase in text:
                raise SystemExit(
                    f"PUBLIC COPY ERROR: retired wording remains in {path.relative_to(ROOT)}: {phrase}"
                )

    mode = "checked" if args.check else "normalized"
    print(f"Public JSON wording {mode}: {changed_values} replacements across {changed_files} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
