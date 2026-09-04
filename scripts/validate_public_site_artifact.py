#!/usr/bin/env python3
"""Fail the build when a Pages artifact exposes retired wording or private paths."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SITE = ROOT / "_site"
REQUIRED = (
    "index.html",
    "site.js",
    "edu/index.html",
    "edu/dashboard.js",
    "report/index.html",
    "report/report.js",
    "reports/index.html",
    "data/releases/current.json",
    "data/symbiosis/current.json",
)
PRIVATE_TOP_LEVEL = {"scripts", "config", "supabase", "validation", "review"}
PRIVATE_DATA_PREFIXES = (
    Path("data/raw"),
    Path("data/review"),
    Path("data/lenses"),
    Path("data/events"),
)
FORBIDDEN = (
    "model-coded",
    "model coded",
    "accepted human corrections replace model outputs as review proceeds",
    "ai-benefiting parasitism",
    "human-benefiting parasitism",
    "competition or co-constraint",
    "who gained and who was constrained?",
)
TEXT_SUFFIXES = {".html", ".js", ".css", ".json", ".txt", ".md"}


def fail(message: str) -> None:
    raise SystemExit(f"PUBLIC ARTIFACT ERROR: {message}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default=str(DEFAULT_SITE), help="Built public artifact directory")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    site = Path(args.site).resolve()
    if not site.is_dir():
        fail(f"missing site directory: {site}")

    for relative in REQUIRED:
        if not (site / relative).is_file():
            fail(f"missing required public file: {relative}")

    for path in site.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(site)
        if relative.parts[0] in PRIVATE_TOP_LEVEL:
            fail(f"private path entered Pages artifact: {relative}")
        if any(relative.is_relative_to(prefix) for prefix in PRIVATE_DATA_PREFIXES):
            fail(f"private data entered Pages artifact: {relative}")
        if path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").casefold()
        for phrase in FORBIDDEN:
            if phrase in text:
                fail(f"retired internal wording entered Pages artifact: {relative}: {phrase}")

    release = json.loads((site / "data/releases/current.json").read_text(encoding="utf-8"))
    symbiosis = json.loads((site / "data/symbiosis/current.json").read_text(encoding="utf-8"))
    if str(release.get("release_id") or "") != str(symbiosis.get("release_id") or ""):
        fail("current release and relationship artifact disagree on release_id")

    print(f"Public Pages artifact checks passed for {release.get('release_id')}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
