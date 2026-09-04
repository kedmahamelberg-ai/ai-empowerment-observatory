#!/usr/bin/env python3
"""Fail publication unless the relationship layer is complete and reconciled.

Release construction may create an internal 0-row placeholder while relationship
classification is running.  A public Pages deployment must never accept that
placeholder or present missing classifications as substantive results.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from symbiosis_common import evidence_basis_strength, release_full_text_requirements

ROOT = Path(__file__).resolve().parents[1]
RELEASE_PATH = ROOT / "data" / "releases" / "current.json"
SYMBIOSIS_PATH = ROOT / "data" / "symbiosis" / "current.json"

CORE = (
    "mutualism",
    "ai_benefiting_parasitism",
    "human_benefiting_parasitism",
    "competition",
)
PARTIAL = (
    "human_enabling_only",
    "human_constraining_only",
    "ai_enabling_only",
    "ai_constraining_only",
)
OTHER = (
    "no_clear_relational_signal",
    "ambiguous_relational_signal",
    "insufficient_evidence",
)
ALL = CORE + PARTIAL + OTHER
PEOPLE_SIGNALS = (
    "people_gaining",
    "people_losing_ground",
    "mixed_picture",
    "not_everyone_benefits",
    "not_clear_yet",
)


def load(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"Missing required public artifact: {path.relative_to(ROOT)}")
    return json.loads(path.read_text(encoding="utf-8"))


def fail(message: str) -> None:
    raise SystemExit(f"RELATIONSHIP CONSISTENCY ERROR: {message}")


def main() -> int:
    release = load(RELEASE_PATH)
    sym = load(SYMBIOSIS_PATH)
    release_id = str(release.get("release_id") or "")
    if str(sym.get("release_id") or "") != release_id:
        fail(f"symbiosis release {sym.get('release_id')} != current release {release_id}")

    release_hash = str(release.get("content_sha256") or "")
    relationship_hash = str(sym.get("source_release_sha256") or "")
    if release_hash and relationship_hash and relationship_hash != release_hash:
        fail(
            "relationship artifact was generated from a different weekly release "
            "revision; run the release-bound relationship placeholder/publication "
            "step after rebuilding the weekly release"
        )

    weekly_total = int((release.get("counts") or {}).get("ai_relevant_event_records") or 0)
    event = sym.get("event") or {}
    expected = int(event.get("expected_units") or 0)
    if expected != weekly_total:
        fail(f"relationship expected_units={expected} but current weekly developments={weekly_total}")

    classified = int(event.get("display_classified_units", event.get("classified_units", 0)) or 0)
    if classified == 0:
        fail(
            f"relationship artifact is still a 0/{expected} placeholder; "
            "publish the saved relationship classifications before deploying Pages"
        )
    if classified != expected:
        fail(f"display classified units={classified} but expected_units={expected}")

    coverage = sym.get("coverage") or {}
    coverage_expected = int(coverage.get("expected_units") or 0)
    weekly_coverage = int((release.get("counts") or {}).get("ai_relevant_articles") or 0)
    if coverage_expected != weekly_coverage:
        fail(
            f"relationship coverage expected_units={coverage_expected} "
            f"but current weekly articles={weekly_coverage}"
        )
    coverage_classified = int(
        coverage.get("display_classified_units", coverage.get("classified_units", 0)) or 0
    )
    if coverage_classified != coverage_expected:
        fail(
            f"relationship coverage classified units={coverage_classified} "
            f"but expected_units={coverage_expected}"
        )
    if str(sym.get("public_status") or "") == "classification_in_progress":
        fail("relationship artifact is marked classification_in_progress")

    counts = event.get("display_configuration_counts") or event.get("configuration_counts") or {}
    values = {key: int(counts.get(key) or 0) for key in ALL}
    total_from_buckets = sum(values.values())
    if total_from_buckets != classified:
        fail(f"configuration buckets sum to {total_from_buckets}, not {classified}")

    complete = int(event.get("display_complete_configuration_count", event.get("complete_configuration_count", 0)) or 0)
    core_sum = sum(values[key] for key in CORE)
    if complete != core_sum:
        fail(f"two-sided denominator={complete}, but four core patterns sum to {core_sum}")

    partial = int(event.get("display_partial_signal_count", event.get("partial_signal_count", 0)) or 0)
    partial_sum = sum(values[key] for key in PARTIAL)
    if partial != partial_sum:
        fail(f"one-sided count={partial}, but one-sided buckets sum to {partial_sum}")

    no_clear = int(event.get("display_no_clear_relational_signal_count", event.get("no_clear_relational_signal_count", 0)) or 0)
    ambiguous = int(event.get("display_ambiguous_relational_signal_count", event.get("ambiguous_relational_signal_count", 0)) or 0)
    insufficient = int(event.get("display_insufficient_evidence_count", event.get("insufficient_evidence_count", 0)) or 0)
    if no_clear != values["no_clear_relational_signal"]:
        fail("no-clear summary does not equal its configuration bucket")
    if ambiguous != values["ambiguous_relational_signal"]:
        fail("ambiguous summary does not equal its configuration bucket")
    if insufficient != values["insufficient_evidence"]:
        fail("insufficient-evidence summary does not equal its configuration bucket")

    enabling = values["mutualism"] + values["human_benefiting_parasitism"] + values["human_enabling_only"]
    constraining = values["ai_benefiting_parasitism"] + values["competition"] + values["human_constraining_only"]
    no_direct = values["ai_enabling_only"] + values["ai_constraining_only"] + values["no_clear_relational_signal"]
    uncertain = values["ambiguous_relational_signal"] + values["insufficient_evidence"]
    human_sum = enabling + constraining + no_direct + uncertain
    if human_sum != expected:
        fail(f"people-side marginal sums to {human_sum}, not {expected}")

    people = sym.get("people_signals")
    if not isinstance(people, dict):
        fail(
            "data/symbiosis/current.json is missing the people_signals block; "
            "upload the matching people-first current and weekly symbiosis artifacts"
        )
    people_expected = int(people.get("expected_units") or 0)
    if people_expected != expected:
        fail(
            "data/symbiosis/current.json has "
            f"people_signals.expected_units={people_expected}, but the current weekly "
            f"relationship denominator is {expected}"
        )
    people_classified = int(people.get("classified_units") or 0)
    if people_classified != classified:
        fail(
            "data/symbiosis/current.json has "
            f"people_signals.classified_units={people_classified}, but the relationship "
            f"layer contains {classified} classified developments"
        )

    evidence = [row for row in (sym.get("evidence") or []) if isinstance(row, dict)]
    if len(evidence) != expected:
        fail(f"relationship evidence contains {len(evidence)} rows, not {expected}")
    _, event_full_text_requirements = release_full_text_requirements(release)
    evidence_by_id = {
        str(row.get("event_id") or ""): row
        for row in evidence
        if row.get("event_id")
    }
    stale_full_text_events = []
    for event_id, required_full_text_sources in event_full_text_requirements.items():
        row = evidence_by_id.get(event_id) or {}
        used_full_text_sources = evidence_basis_strength(
            row.get("content_basis"),
            row.get("evidence_basis_summary"),
        )[0]
        if used_full_text_sources < required_full_text_sources:
            stale_full_text_events.append(event_id)
    if stale_full_text_events:
        fail(
            "relationship evidence is stale: "
            f"{len(stale_full_text_events)}/{len(event_full_text_requirements)} developments "
            "with available full article bodies were classified from weaker evidence"
        )
    aggregate_patterns = Counter()
    aggregate_signals = Counter()
    explicit_multi = 0
    distribution_coded = 0
    for row in evidence:
        patterns = row.get("relationship_patterns")
        signals = row.get("public_signals")
        if not isinstance(patterns, dict) or not isinstance(signals, dict):
            fail(f"event {row.get('event_id')} lacks the public multi-signal fields")
        explicit_multi += int(bool(row.get("multi_label_available")))
        distribution_coded += int(bool(row.get("distribution_coded")))
        for key in CORE:
            aggregate_patterns[key] += int(bool(patterns.get(key)))
        for key in PEOPLE_SIGNALS:
            aggregate_signals[key] += int(bool(signals.get(key)))
        gaining = bool(signals.get("people_gaining"))
        losing = bool(signals.get("people_losing_ground"))
        if bool(signals.get("mixed_picture")) != (gaining and losing):
            fail(f"event {row.get('event_id')} has an inconsistent mixed-picture signal")
        if bool(signals.get("not_clear_yet")) != (not gaining and not losing):
            fail(f"event {row.get('event_id')} has an inconsistent not-clear-yet signal")

    declared_patterns = people.get("relationship_pattern_counts") or {}
    declared_signals = people.get("people_signal_counts") or {}
    for key in CORE:
        value = int(declared_patterns.get(key) or 0)
        if value != aggregate_patterns[key]:
            fail(f"plain relationship pattern {key}={value}, but evidence rows produce {aggregate_patterns[key]}")
        if not 0 <= value <= expected:
            fail(f"plain relationship pattern {key} is outside 0..{expected}")
    for key in PEOPLE_SIGNALS:
        value = int(declared_signals.get(key) or 0)
        if value != aggregate_signals[key]:
            fail(f"plain people signal {key}={value}, but evidence rows produce {aggregate_signals[key]}")
        if not 0 <= value <= expected:
            fail(f"plain people signal {key} is outside 0..{expected}")
    if int(people.get("explicit_multi_label_units") or 0) != explicit_multi:
        fail("explicit multi-label unit count does not match the evidence rows")
    if int(people.get("distribution_coded_units") or 0) != distribution_coded:
        fail("distribution-coded unit count does not match the evidence rows")

    availability = people.get("availability") or {}
    for key in PEOPLE_SIGNALS:
        if not isinstance(availability.get(key), bool):
            fail(f"availability for {key} must be true or false")
    if not availability.get("people_gaining") or not availability.get("people_losing_ground") or not availability.get("not_clear_yet"):
        fail("the three legacy-compatible people signals must be available for a complete weekly release")
    if bool(availability.get("mixed_picture")) != (explicit_multi == expected):
        fail("mixed-picture availability does not match multi-label coverage")
    if bool(availability.get("not_everyone_benefits")) != (distribution_coded == expected):
        fail("not-everyone-benefits availability does not match distribution coding coverage")

    print(f"Relationship arithmetic OK for {release_id}.")
    print(f"Two-sided: {complete}/{expected}.")
    print(f"People-side: enabling={enabling}, constraining={constraining}, no_direct={no_direct}, insufficient_or_unclear={uncertain}.")
    print(
        "Public signals: "
        + ", ".join(f"{key}={aggregate_signals[key]}" for key in PEOPLE_SIGNALS)
        + "."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
