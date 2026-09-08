#!/usr/bin/env python3
"""Fail the build when a Pages artifact exposes retired wording or private paths."""

from __future__ import annotations

import argparse
import json
import hashlib
import csv
from build_public_site import PRIVATE_JSON_KEYS
from public_directional_release import validate_directional_release
from complete_content import build_cohort, brief_export
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
    "data/analysis/current.json",
    "data/analysis/current.csv",
    "data/analysis/audit.csv",
    "data/brief/current.json",
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

    validate_directional_release(release, symbiosis)
    expected = build_cohort(release, symbiosis)
    cohort = json.loads((site / "data/analysis/current.json").read_text(encoding="utf-8"))
    def without_projection(payload):
        return {key:value for key,value in payload.items() if key not in ("public_projection_version", "public_content_sha256")}
    if without_projection(cohort) != expected or symbiosis.get("complete_content") != expected:
        fail("The complete-content cohort differs from the saved source lineage")
    exported = json.loads((site / "data/brief/current.json").read_text(encoding="utf-8"))
    if without_projection(exported) != brief_export(release, symbiosis, expected):
        fail("The Brief export does not contain exactly the complete-content cohort")
    def csv_rows(relative):
        with (site / relative).open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    included = {row["event_id"] for row in expected["records"] if row["eligible"]}
    readings_csv = csv_rows("data/analysis/current.csv")
    audit_csv = csv_rows("data/analysis/audit.csv")
    if len(readings_csv) != len(included) or {row["event_id"] for row in readings_csv} != included:
        fail("The included-readings CSV differs from the analytical denominator")
    if len(audit_csv) != len(expected["records"]) or {row["event_id"] for row in audit_csv} != {row["event_id"] for row in expected["records"]} or {row["event_id"] for row in audit_csv if row["included_in_analysis"] == "true"} != included:
        fail("The audit CSV does not reconcile all collected developments")
    def inspect_private(value, path):
        if isinstance(value, dict):
            if set(value) & PRIVATE_JSON_KEYS:
                fail(f"Private decision provenance in {path}")
            for item in value.values(): inspect_private(item, path)
        elif isinstance(value, list):
            for item in value: inspect_private(item, path)
    for path in site.rglob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        inspect_private(payload, path.relative_to(site))
        if isinstance(payload, dict) and payload.get("release_id"):
            declared = payload.pop("public_content_sha256", None)
            actual = hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()
            if actual != declared: fail(f"Public download checksum mismatch: {path.relative_to(site)}")
    if not (site / "data/symbiosis/current.csv").is_file():
        fail("The public classification CSV is missing")
    print(f"Public Pages artifact checks passed for {release.get('release_id')}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
