#!/usr/bin/env python3
"""Validate the private Supabase evidence contract without reading body text."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from supabase import create_client

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "supabase/migrations/20260902_observatory_body_evidence_contract.sql"


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise SystemExit(f"{name} is missing")
    return value


def probe(client: Any, relation: str, columns: str) -> dict[str, Any]:
    response = client.table(relation).select(columns).limit(1).execute()
    return {"relation": relation, "accessible": True, "sample_rows": len(response.data or [])}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    client = create_client(required_env("SUPABASE_URL"), required_env("SUPABASE_SECRET_KEY"))
    # Validate the operational contract, not an implementation-specific primary
    # key name. Older AIEO installations use attempt_id while fresh databases
    # use fetch_attempt_id; neither collector nor reporting code reads that key.
    # Probing every column that collection actually writes catches real schema
    # drift without rejecting a compatible legacy table.
    checks = (
        (
            "brief_article_fetch_attempts",
            "article_id,source_url,source_domain,workflow_run_id,retrieval_method,"
            "http_status,robots_allowed,tdm_reservation,tdm_policy_url,"
            "paywall_detected,outcome,response_content_type,response_bytes,"
            "elapsed_ms,metadata,attempted_at",
        ),
        (
            "brief_article_content_snapshots",
            "snapshot_id,article_id,source_url,source_domain,retrieval_method,"
            "http_status,mime_type,extracted_title,word_count,text_sha256,"
            "extraction_quality,content_basis,rights_status,rights_basis,"
            "robots_allowed,tdm_reservation,tdm_policy_url,paywall_detected,"
            "is_current,retrieved_at,created_at",
        ),
        ("brief_article_best_evidence", "article_id,evidence_basis,evidence_at,evidence_ref"),
        ("brief_event_source_evidence", "event_id,article_id,evidence_basis,evidence_at,evidence_ref"),
        (
            "brief_event_evidence_readiness",
            "event_id,source_count,full_source_count,headline_only_count,editorial_evidence_level",
        ),
    )
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for relation, columns in checks:
        try:
            results.append(probe(client, relation, columns))
        except Exception as exc:  # Supabase exceptions vary by client version.
            results.append({"relation": relation, "accessible": False})
            errors.append(f"{relation}: {type(exc).__name__}: {exc}")

    report = {
        "schema_version": "aieo_private_body_contract_check_v1",
        "valid": not errors,
        "checks": results,
        "body_text_read": False,
        "migration": MIGRATION,
    }
    if args.output:
        target = Path(args.output)
        if not target.is_absolute():
            target = ROOT / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if errors:
        print("\nPrivate body-evidence contract is not ready:")
        for error in errors:
            print(f"- {error}")
        print(
            "\nThe operational evidence schema is incomplete or inaccessible. "
            f"Run the current {MIGRATION} in Supabase, then start a fresh workflow run."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
