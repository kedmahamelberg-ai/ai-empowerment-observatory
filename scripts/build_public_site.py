#!/usr/bin/env python3
"""Build the intentionally public GitHub Pages artifact."""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "_site"

ROOT_FILES = ["index.html", "site.css", ".nojekyll", "CNAME"]
PUBLIC_DIRS = ["edu", "pro", "review"]
PUBLIC_DATA_FILES = [
    (ROOT / "data" / "review" / "latest.json", SITE / "data" / "review" / "latest.json"),
    (ROOT / "data" / "review" / "latest.csv", SITE / "data" / "review" / "latest.csv"),
    (ROOT / "data" / "collection_status.json", SITE / "data" / "collection_status.json"),
]


def copy_required_file(source: Path, destination: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"Required public file is missing: {source}")
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

    for source, destination in PUBLIC_DATA_FILES:
        copy_required_file(source, destination)

    print("Built public Pages artifact at", SITE)
    print("Excluded private paths: scripts/, config/, supabase/, data/raw/, data/review/history/")


if __name__ == "__main__":
    main()
