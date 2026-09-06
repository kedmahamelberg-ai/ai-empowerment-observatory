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
import json
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "_site"

ROOT_FILES = [
    "index.html",
    "site.css",
    "site.js",
    "public-data.js",
    "editorial.css",
    "globe.js",
    "vendor/globe-geometry.js",
    "vendor/globe-geometry.LICENSE.txt",
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
    "data/geography",
]


# Publication strips execution traces and private decision provenance. The
# source content hash remains the identity of the versioned source artifact.
PRIVATE_JSON_KEYS = {
    "raw_output", "raw_model_output", "_raw_model_output", "prompt_text", "prompt",
    "correction_provenance", "signal_provenance", "reviewed", "review_status",
    "review_reason", "review_reasoning", "reviewer", "reviewer_id", "reviewed_at",
    "owner_gold", "owner_qc", "classification_audit", "review", "requires_review",
    "source_body_qc", "provenance", "audit_selection", "audit_status", "governance",
    "display_basis", "reviewed_units", "unreviewed_units", "review_queue_count",
    "human_audited", "release_status", "review_required_count",
}

def public_json(value):
    if isinstance(value, dict):
        if value.get("schema_version") == "aieo_symbiosis_public_v2.0":
            value = dict(value)
            value["event"] = {"expected_units": value["directional_summary"]["total"]}
            value["coverage"] = {"expected_units": value["coverage"]["expected_units"], "scope": "source_page_inventory"}
            value.pop("definitions", None)
            value.pop("secondary_empowerment", None)
            value["classification_scope"] = {key:item for key,item in value["classification_scope"].items() if not key.startswith("legacy_")}
            legacy_fields = {"configuration","plain_label","technical_label","human_experience_type","ai_expressive_role","human_direction","ai_direction","empowerment_secondary"}
            value["evidence"] = [{key:item for key,item in row.items() if key not in legacy_fields} for row in value["evidence"]]
        return {key: public_json(item) for key,item in value.items() if key not in PRIVATE_JSON_KEYS}
    if isinstance(value, list):
        return [public_json(item) for item in value]
    return value


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

    for source in (ROOT / "data/symbiosis").rglob("*.csv"):
        copy_optional_file(source, SITE / source.relative_to(ROOT))
    for target in SITE.rglob("*.json"):
        cleaned = public_json(json.loads(target.read_text(encoding="utf-8")))
        if isinstance(cleaned, dict) and cleaned.get("release_id"):
            cleaned["public_projection_version"] = "aieo_public_projection_v2"
            cleaned.pop("public_content_sha256", None)
            cleaned["public_content_sha256"] = hashlib.sha256(json.dumps(cleaned,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()
        target.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
