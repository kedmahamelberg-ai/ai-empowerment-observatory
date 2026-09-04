#!/usr/bin/env python3
"""Produce a reproducible explanation of public people-signal denominators.

The report is intentionally source-text-free.  It lets an owner see why the
visible people cards have the counts they do, including the separation between
"no clear change" and "too little evidence", without hard-coding one week.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RELEASES = ROOT / "data" / "releases"
SYMBIOSIS = ROOT / "data" / "symbiosis"

OUTCOME_KEYS = (
    "benefit_shown",
    "downside_shown",
    "benefit_and_downside",
    "no_clear_people_change",
    "too_little_evidence",
)
CORE_PATTERN_KEYS = (
    "mutualism",
    "ai_benefiting_parasitism",
    "human_benefiting_parasitism",
    "competition",
)


def load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"SIGNAL AUDIT ERROR: missing {path.relative_to(ROOT)}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"SIGNAL AUDIT ERROR: expected an object in {path.relative_to(ROOT)}")
    return payload


def release_for(release_id: str) -> dict[str, Any]:
    if not release_id:
        return load(RELEASES / "current.json")
    candidate = RELEASES / "weekly" / f"{release_id}.json"
    if candidate.is_file():
        return load(candidate)
    current = load(RELEASES / "current.json")
    if str(current.get("release_id") or "") == release_id:
        return current
    raise SystemExit(f"SIGNAL AUDIT ERROR: no weekly release {release_id}")


def symbiosis_for(release_id: str) -> dict[str, Any]:
    if not release_id:
        return load(SYMBIOSIS / "current.json")
    candidate = SYMBIOSIS / "weekly" / f"{release_id}.json"
    if candidate.is_file():
        return load(candidate)
    current = load(SYMBIOSIS / "current.json")
    if str(current.get("release_id") or "") == release_id:
        return current
    raise SystemExit(f"SIGNAL AUDIT ERROR: no relationship artifact for {release_id}")


def primary_outcome(row: dict[str, Any]) -> str:
    signals = row.get("public_signals") if isinstance(row.get("public_signals"), dict) else {}
    gaining = bool(signals.get("people_gaining"))
    losing = bool(signals.get("people_losing_ground"))
    if gaining and losing:
        return "benefit_and_downside"
    if gaining:
        return "benefit_shown"
    if losing:
        return "downside_shown"
    if str(row.get("evidence_status") or "") == "insufficient":
        return "too_little_evidence"
    return "no_clear_people_change"


def full_body_available(row: dict[str, Any]) -> bool:
    summary = row.get("evidence_basis_summary")
    if not isinstance(summary, dict):
        return False
    return int(summary.get("full_text_sources") or 0) > 0


def build_audit(release: dict[str, Any], symbiosis: dict[str, Any]) -> dict[str, Any]:
    release_id = str(release.get("release_id") or "")
    if not release_id or str(symbiosis.get("release_id") or "") != release_id:
        raise SystemExit("SIGNAL AUDIT ERROR: release and relationship artifact do not match")
    expected = int((release.get("counts") or {}).get("ai_relevant_event_records") or 0)
    evidence = [row for row in (symbiosis.get("evidence") or []) if isinstance(row, dict)]
    if expected <= 0:
        raise SystemExit("SIGNAL AUDIT ERROR: no event denominator is available")
    if len(evidence) != expected:
        raise SystemExit(
            f"SIGNAL AUDIT ERROR: {len(evidence)} evidence rows for a denominator of {expected}"
        )

    outcomes = Counter(primary_outcome(row) for row in evidence)
    counts = {key: int(outcomes[key]) for key in OUTCOME_KEYS}
    if sum(counts.values()) != expected:
        raise SystemExit("SIGNAL AUDIT ERROR: mutually exclusive people outcomes do not sum to the denominator")

    declared = (symbiosis.get("people_signals") or {}).get("not_clear_breakdown") or {}
    declared_insufficient = int(declared.get("not_enough_evidence") or 0)
    declared_no_change = int(declared.get("no_directional_people_change") or 0)
    if declared_insufficient != counts["too_little_evidence"] or declared_no_change != counts["no_clear_people_change"]:
        raise SystemExit(
            "SIGNAL AUDIT ERROR: stored not-clear breakdown disagrees with evidence rows"
        )

    # The public relationship cards use the explicit relationship_patterns on
    # each evidence row.  This is intentionally not the separate one-label
    # classifier-configuration summary, which answers a different question.
    pattern_counts = Counter()
    two_sided = 0
    for row in evidence:
        patterns = row.get("relationship_patterns") if isinstance(row.get("relationship_patterns"), dict) else {}
        present = False
        for key in CORE_PATTERN_KEYS:
            is_present = bool(patterns.get(key))
            pattern_counts[key] += int(is_present)
            present = present or is_present
        two_sided += int(present)
    declared_patterns = (symbiosis.get("people_signals") or {}).get("relationship_pattern_counts") or {}
    for key in CORE_PATTERN_KEYS:
        if int(declared_patterns.get(key) or 0) != pattern_counts[key]:
            raise SystemExit(
                f"SIGNAL AUDIT ERROR: displayed relationship pattern {key} disagrees with evidence rows"
            )
    if not 0 <= two_sided <= expected:
        raise SystemExit("SIGNAL AUDIT ERROR: two-sided count is outside the weekly denominator")

    full_body = sum(full_body_available(row) for row in evidence)
    return {
        "schema_version": "aieo_public_signal_audit_v1",
        "release_id": release_id,
        "period_start": release.get("period_start"),
        "period_end": release.get("period_end"),
        "event_denominator": expected,
        "people_outcomes": counts,
        "not_clear_explained": {
            "no_clear_change_reported": counts["no_clear_people_change"],
            "too_little_evidence": counts["too_little_evidence"],
            "combined": counts["no_clear_people_change"] + counts["too_little_evidence"],
        },
        "source_evidence": {
            "developments_with_at_least_one_full_article": full_body,
            "developments_without_a_full_article": expected - full_body,
        },
        "developments_with_an_explicit_two_sided_relationship_pattern": two_sided,
        "not_in_clear_two_sided_subset": expected - two_sided,
        "checks": {
            "mutually_exclusive_people_outcomes_sum_to_denominator": True,
            "not_clear_breakdown_matches_evidence_rows": True,
            "relationship_artifact_matches_release": True,
            "displayed_relationship_patterns_match_evidence_rows": True,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", default="", help="Weekly release ID; blank means current")
    parser.add_argument("--output", default="", help="Optional JSON output path, relative to the repository")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    release = release_for(args.release_id)
    artifact = build_audit(release, symbiosis_for(str(release.get("release_id") or "")))
    if args.output:
        output = (ROOT / args.output).resolve()
        if ROOT not in output.parents and output != ROOT:
            raise SystemExit("SIGNAL AUDIT ERROR: output must remain inside the repository")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {output.relative_to(ROOT)}")
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
