#!/usr/bin/env python3
"""Build the intentionally public Observatory site artifact.

The public site contains aggregate outputs, methodology, report assets, and
public-facing pages. It deliberately excludes scripts, migrations, raw data,
classification review pages, prompts, thresholds, and private QA artifacts.

Public release snapshots under ``data/releases/`` are copied recursively, but
only JSON files are included. This makes weekly, monthly, quarterly and annual
layer available through GitHub Pages without exposing private pipeline files.
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
    "globe.js",
    "globe.css",
    "analytics.js",
    "analytics-consent.css",
    ".nojekyll",
    "CNAME",
    "favicon.svg",
    "favicon-96x96.png",
    "favicon.ico",
    "apple-touch-icon.png",
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

# Every JSON file in this directory is an intentionally public release asset.
# Copying the complete JSON tree automatically includes current.json, index.json,
# weekly archives, future monthly releases, and deliberate release revisions.
OPTIONAL_PUBLIC_JSON_DIRS = [
    "data/releases",
    "data/symbiosis",
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


def copy_optional_json_tree(source: Path, destination: Path) -> list[Path]:
    """Copy every JSON file below an optional intentionally public directory."""
    if not source.exists():
        return []

    copied: list[Path] = []
    for item in sorted(source.rglob("*.json")):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        copied.append(relative)

    return copied


def verify_json_tree(source: Path, destination: Path) -> None:
    """Fail the build if a source release JSON was omitted from the artifact."""
    if not source.exists():
        return

    source_files = {
        item.relative_to(source)
        for item in source.rglob("*.json")
        if item.is_file()
    }
    destination_files = {
        item.relative_to(destination)
        for item in destination.rglob("*.json")
        if item.is_file()
    } if destination.exists() else set()

    missing = sorted(source_files - destination_files)
    if missing:
        formatted = ", ".join(str(item) for item in missing)
        raise RuntimeError(
            "Public release JSON files were omitted from _site: " + formatted
        )


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

    copied_release_files: list[Path] = []
    for relative in OPTIONAL_PUBLIC_JSON_DIRS:
        source = ROOT / relative
        destination = SITE / relative
        copied_release_files.extend(copy_optional_json_tree(source, destination))
        verify_json_tree(source, destination)

    print("Built public Pages artifact at", SITE)
    if copied_release_files:
        print(
            "Included public release JSON files:",
            ", ".join(str(path) for path in copied_release_files),
        )
        print("Public release artifact check: PASS")
    else:
        print("No public release JSON files were present; release copy step skipped.")

    print(
        "Excluded private paths: scripts/, config/, supabase/, validation/, "
        "review/, data/raw/, data/review/, data/lenses/latest.json, "
        "data/events/latest.json, prompts and internal QA artifacts."
    )


if __name__ == "__main__":
    main()
