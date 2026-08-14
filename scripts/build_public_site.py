#!/usr/bin/env python3
"""Build the intentionally public Observatory site artifact.

Public:
- public-facing pages
- aggregate Observatory outputs
- public methodology and status
- generated public reports

Private/internal paths are not copied into _site.
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
]


def preflight() -> None:
    missing = []

    for name in ROOT_FILES:
        if not (ROOT / name).is_file():
            missing.append(f"FILE  {name}")

    for name in PUBLIC_DIRS:
        path = ROOT / name
        if not path.is_dir():
            missing.append(f"DIR   {name}/")
        elif not any(path.iterdir()):
            missing.append(f"DIR   {name}/ (empty)")

    for relative in REQUIRED_DATA_FILES:
        if not (ROOT / relative).is_file():
            missing.append(f"DATA  {relative}")

    if missing:
        details = "\n".join(f"  - {item}" for item in missing)
        raise FileNotFoundError(
            "Public-site preflight failed. ALL missing requirements:\n"
            f"{details}\n\n"
            "Fix every item above, commit to main, then start a NEW workflow run."
        )


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def main() -> None:
    preflight()

    if SITE.exists():
        shutil.rmtree(SITE)

    SITE.mkdir(parents=True)

    for name in ROOT_FILES:
        copy_file(ROOT / name, SITE / name)

    for name in PUBLIC_DIRS:
        shutil.copytree(ROOT / name, SITE / name)

    for relative in REQUIRED_DATA_FILES:
        copy_file(ROOT / relative, SITE / relative)

    for relative in OPTIONAL_DATA_FILES:
        source = ROOT / relative
        if source.is_file():
            copy_file(source, SITE / relative)

    print("Built public Pages artifact:", SITE)
    print("Public directories:", ", ".join(PUBLIC_DIRS))
    print(
        "Private/internal paths are excluded from _site: "
        "scripts/, config/, supabase/, validation/, review/, "
        "data/raw/, data/review/, prompts and internal QA artifacts."
    )


if __name__ == "__main__":
    main()
