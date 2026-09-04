#!/usr/bin/env python3
"""Verify that the current full-body relationship run can be resumed safely."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from supabase import Client, create_client

from symbiosis_common import (
    CLASSIFIER_VERSION,
    CODEBOOK_VERSION,
    classification_input_evidence,
    evidence_basis_strength,
    release_full_text_requirements,
    release_unit_ids,
)

ROOT = Path(__file__).resolve().parents[1]
RELEASE_PATH = ROOT / "data" / "releases" / "current.json"


class ResumeCheckError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise ResumeCheckError(f"{name} is missing.")
    return value


def first_row(response: Any) -> dict[str, Any]:
    rows = getattr(response, "data", None) or []
    if not rows:
        raise ResumeCheckError(
            "No interrupted relationship run matches the current release and its "
            "full-body classification lineage. Nothing was started."
        )
    return rows[0]


def main() -> int:
    release = json.loads(RELEASE_PATH.read_text(encoding="utf-8"))
    release_id = str(release.get("release_id") or "").strip()
    lineage = release.get("lineage") if isinstance(release.get("lineage"), dict) else {}
    empowerment_run_id = str(lineage.get("classification_run_id") or "").strip()
    collection_run_id = str(lineage.get("collection_run_id") or "").strip()
    if not release_id or not empowerment_run_id:
        raise ResumeCheckError("Current release is missing its full-body classification lineage.")

    client: Client = create_client(
        required_env("SUPABASE_URL"),
        required_env("SUPABASE_SECRET_KEY"),
    )
    query = (
        client.table("symbiosis_classification_runs")
        .select("symbiosis_run_id,run_key,status,started_at")
        .eq("scope", "latest_release")
        .eq("target_release_id", release_id)
        .eq("classifier_version", CLASSIFIER_VERSION)
        .eq("codebook_version", CODEBOOK_VERSION)
        .in_("status", ["running", "failed"])
    )
    if collection_run_id:
        query = query.eq("collection_run_id", collection_run_id)
    run = first_row(query.order("started_at", desc=True).limit(1).execute())
    run_id = str(run["symbiosis_run_id"])
    saved = (
        client.table("symbiosis_classifications")
        .select("symbiosis_classification_id,unit_key,lens,article_id,event_id,content_basis,raw_output")
        .eq("symbiosis_run_id", run_id)
        .range(0, 999)
        .execute()
    )
    saved_rows = getattr(saved, "data", None) or []
    article_ids, event_ids = release_unit_ids(release)
    current_keys = {
        *(f"coverage:{release_id}:{article_id}" for article_id in article_ids),
        *(f"event:{release_id}:{event_id}" for event_id in event_ids),
    }
    relevant_rows = [row for row in saved_rows if str(row.get("unit_key") or "") in current_keys]
    saved_keys = {str(row.get("unit_key") or "") for row in relevant_rows if row.get("unit_key")}
    if not saved_keys:
        raise ResumeCheckError(
            f"Interrupted run {run.get('run_key')} has no saved classifications; refusing a full restart."
        )

    coverage_required, event_required = release_full_text_requirements(release)
    stale_full_body_rows = []
    for row in relevant_rows:
        required = (
            coverage_required.get(str(row.get("article_id") or ""), 0)
            if row.get("lens") == "coverage"
            else event_required.get(str(row.get("event_id") or ""), 0)
        )
        if not required:
            continue
        content_basis, evidence_summary = classification_input_evidence(row)
        if evidence_basis_strength(content_basis, evidence_summary)[0] < required:
            stale_full_body_rows.append(str(row.get("unit_key") or ""))
    if stale_full_body_rows:
        raise ResumeCheckError(
            f"Interrupted run {run.get('run_key')} contains {len(stale_full_body_rows)} "
            "saved rows from weaker evidence; refusing to reuse or restart them."
        )

    expected = len(article_ids) + len(event_ids)
    remaining = max(0, expected - len(saved_keys))
    output = {
        "release_id": release_id,
        "run_key": run.get("run_key"),
        "status": run.get("status"),
        "saved_units": len(saved_keys),
        "expected_units": expected,
        "remaining_units": remaining,
        "message": "Safe to resume; body collection and Stage 7C will not run.",
    }
    print(json.dumps(output, indent=2))
    github_output = str(os.environ.get("GITHUB_OUTPUT") or "").strip()
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"release_id={release_id}\n")
            handle.write(f"saved_units={len(saved_keys)}\n")
            handle.write(f"remaining_units={remaining}\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ResumeCheckError as exc:
        print(f"SAFE RESUME CHECK FAILED: {exc}")
        raise SystemExit(1)
