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
    checks = (
        ("brief_article_fetch_attempts", "fetch_attempt_id,article_id,outcome,attempted_at"),
        ("brief_article_content_snapshots", "snapshot_id,article_id,word_count,is_current,retrieved_at"),
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
        print(f"\nApply {MIGRATION} once in Supabase, then rerun this workflow.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
