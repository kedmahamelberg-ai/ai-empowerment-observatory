#!/usr/bin/env python3
"""Fast regression check for the published relationship audit index."""

from __future__ import annotations

from pathlib import Path

from build_relationship_audit import (
    AuditError,
    DEFAULT_OUTPUT,
    PRIMARY_OUTCOMES,
    build_audit,
    load_inputs,
    read_json,
)


ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise SystemExit(f"RELATIONSHIP AUDIT VALIDATION ERROR: {message}")


def main() -> int:
    try:
        release, symbiosis = load_inputs("")
        if str(symbiosis.get("public_status") or "") == "classification_in_progress":
            print("Relationship audit deferred while the current relationship classification is in progress.")
            return 0
        expected = build_audit(release, symbiosis)
    except AuditError as error:
        fail(str(error))
    actual = read_json(DEFAULT_OUTPUT)
    if actual.get("schema_version") != expected.get("schema_version"):
        fail("audit schema version does not match the audit builder")
    if actual.get("release_id") != expected.get("release_id"):
        fail("audit release does not match the current release")
    expected_summary = expected.get("summary") or {}
    actual_summary = actual.get("summary") or {}
    if actual_summary.get("development_count") != expected_summary.get("development_count"):
        fail("audit development count does not match current relationship evidence")
    if actual_summary.get("primary_outcome_counts") != expected_summary.get("primary_outcome_counts"):
        fail("primary people outcomes are stale or do not reconcile")
    primary = actual_summary.get("primary_outcome_counts") or {}
    if sum(int(primary.get(key) or 0) for key in PRIMARY_OUTCOMES) != int(actual_summary.get("development_count") or 0):
        fail("primary people outcomes do not add up to the development total")
    if actual_summary.get("body_coverage_counts") != expected_summary.get("body_coverage_counts"):
        fail("body coverage summary is stale or does not reconcile")
    if len(actual.get("developments") or []) != int(actual_summary.get("development_count") or 0):
        fail("audit development rows do not match the summary")
    print(
        "Relationship audit OK for "
        f"{actual['release_id']}: {actual_summary['development_count']} developments."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
