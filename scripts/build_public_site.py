#!/usr/bin/env python3
"""Build the intentionally public Observatory site artifact.

The public site contains aggregate outputs, methodology, report assets, and
public-facing pages. It deliberately excludes scripts, migrations, raw data,
classification review pages, prompts, thresholds, and private QA artifacts.
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "_site"

ROOT_FILES = [
    "index.html",
    "site.css",
    "site.js",
    "analytics.js",
    "analytics-consent.css",
    ".nojekyll",
    "CNAME",
]

PUBLIC_DIRS = [
    "edu",
    "pro",
    "report",
    "reports",
    "methodology",
    "status",
    "privacy",
]

REQUIRED_DATA_FILES = [
    "data/lenses/latest.json",
    "data/events/latest.json",
    "data/methodology/latest.json",
    "data/status/latest.json",
    "data/site-config.json",
]

OPTIONAL_DATA_FILES = [
    "data/reports/latest.json",
    "data/public-config.json",
    "data/insights/latest.json",
    "data/history/releases.json",
]


def copy_required_file(source: Path, destination: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"Required public file is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def copy_optional_file(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def main() -> None:
    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir(parents=True)

    for name in ROOT_FILES:
        copy_required_file(ROOT / name, SITE / name)

    for name in PUBLIC_DIRS:
        source = ROOT / name
        if not source.exists():
            raise FileNotFoundError(f"Required public directory is missing: {source}")
        shutil.copytree(source, SITE / name)

    for relative in REQUIRED_DATA_FILES:
        copy_required_file(ROOT / relative, SITE / relative)

    for relative in OPTIONAL_DATA_FILES:
        copy_optional_file(ROOT / relative, SITE / relative)

    print("Built public Pages artifact at", SITE)
    print(
        "Excluded private paths: scripts/, config/, supabase/, validation/, "
        "review/, data/raw/, data/review/, prompts and internal QA artifacts."
    )


if __name__ == "__main__":
    main()
