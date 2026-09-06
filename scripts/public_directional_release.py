"""One publication boundary for corrections, independent axes and public totals."""
from __future__ import annotations

import copy
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from independent_axes import (
    AXIS_SCHEMA, AXIS_POLICY, PATTERNS, PATTERN_SIDES, make_axes, validate_axes,
    summarize_axes, signals_from_axes, has_side,
)

ROOT = Path(__file__).resolve().parents[1]


def release_corrections(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    path = ROOT / "validation" / "corrections" / f"{release['release_id']}-independent-directions.json"
    if not path.exists():
        return {}
    manifest = json.loads(path.read_text())
    for key in ("release_id", "period_start", "period_end"):
        if manifest.get(key) != release.get(key):
            raise ValueError(f"Correction manifest {key} differs from the selected release")
    if manifest.get("source_release_sha256") != release.get("content_sha256"):
        raise ValueError("Correction manifest belongs to a different source-release revision")
    source_rows = {str(row.get("effective_event_id") or row.get("event_id")): row for row in release["evidence"]
                   if row.get("classification", {}).get("ai_relevant") is not False}
    rows = manifest.get("records") or []
    ids = [row["event_id"] for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(source_rows):
        raise ValueError("Correction manifest must cover the exact frozen development IDs once")
    for row in rows:
        source_ids = {source["article_id"] for source in source_rows[row["event_id"]]["sources"]}
        if set(row.get("article_ids") or []) != source_ids:
            raise ValueError("Correction source membership changed: " + row["event_id"])
        validate_axes(row.get("axes"))
    return {row["event_id"]: row for row in rows}


def legacy_configuration(axes: dict[str, Any]) -> tuple[str, str, str]:
    """Retain the old single-category export as an explicitly legacy field."""
    h, a = (axes[side]["direction"] for side in ("human", "ai"))
    mapping = {"gain": "enabling", "loss": "constraining", "none": "neutral", "mixed": "mixed", "unresolved": "unclear"}
    if "unresolved" in (h, a):
        category = "insufficient_evidence" if not axes["evidence_complete"] else "ambiguous_relational_signal"
    elif "mixed" in (h, a):
        category = "ambiguous_relational_signal"
    else:
        category = {
            ("gain", "gain"): "mutualism", ("loss", "gain"): "ai_benefiting_parasitism",
            ("gain", "loss"): "human_benefiting_parasitism", ("loss", "loss"): "competition",
            ("gain", "none"): "human_enabling_only", ("loss", "none"): "human_constraining_only",
            ("none", "gain"): "ai_enabling_only", ("none", "loss"): "ai_constraining_only",
            ("none", "none"): "no_clear_relational_signal",
        }[(h, a)]
    return category, mapping[h], mapping[a]


def apply_owner_axes(row: dict[str, Any], owner: dict[str, Any]) -> None:
    final = owner.get("final") or {}
    if isinstance(final.get("axes"), dict):
        row["axes"] = validate_axes(final["axes"])
    else:
        mapping = {"enabling": "gain", "constraining": "loss", "neutral": "none", "mixed": "mixed", "unclear": "unresolved"}
        h = mapping.get(str(final.get("human_direction")), "unresolved")
        a = mapping.get(str(final.get("ai_direction")), "unresolved")
        complete = final.get("evidence_status") != "insufficient"
        explanation = str(final.get("public_takeaway") or owner.get("review_reasoning") or "Accepted source reading")
        row["axes"] = make_axes(h, a, human_evidence=explanation, ai_evidence=explanation, complete_evidence=complete)
    row["reviewed"] = True
    row["review_status"] = "owner_manual_qc"


def build_directional_release(release: dict[str, Any], payload: dict[str, Any], *,
                              corrections: dict[str, dict[str, Any]] | None = None,
                              owner_gold: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    result = copy.deepcopy(payload)
    corrections = corrections or {}
    owner_gold = owner_gold or {}
    if result.get("release_id") != release["release_id"] or result.get("source_release_sha256") != release["content_sha256"]:
        raise ValueError("Relationship input does not match the selected source release")
    for row in result["evidence"]:
        event_id = row["event_id"]
        correction = corrections.get(event_id)
        # Existing accepted decisions and owner gold have priority over the
        # assistant source audit. The original audit never becomes owner gold.
        if correction and not row.get("reviewed"):
            row["axes"] = copy.deepcopy(correction["axes"])
            row["evidence_summary"] = row["axes"]["human"]["evidence"]
            row["public_takeaway"] = row["evidence_summary"]
            row["reasoning"] = correction.get("source_note") or ""
            row["source_note"] = correction.get("source_note") or ""
            row["display_scope"] = correction.get("display_scope") or ""
            if correction.get("display_title"):
                row["event_title"] = correction["display_title"]
                if len(row.get("sources") or []) == 1:
                    row["sources"][0]["headline"] = correction["display_title"]
            row["relationship_patterns"] = {key: False for key in PATTERNS}
            row["relationship_evidence"] = {}
            row["relationship_pattern_status"] = correction["relationship_pattern_status"]
            row["reviewed"] = False
            row["review_status"] = "source_audit_correction"
            row["signal_provenance"] = "source_audit_correction"
            row["correction_provenance"] = correction["provenance"]
            complete = row["axes"]["evidence_complete"]
            row["content_basis"] = "full_text" if complete else "not_available"
            row["evidence_basis_summary"] = {
                "source_count": len(correction["article_ids"]),
                "full_text_sources": len(correction["article_ids"]) if complete else 0,
                "body_coverage": "complete_written_source" if complete else "incomplete_written_source",
                "input_policy": AXIS_POLICY,
            }
        if event_id in owner_gold:
            apply_owner_axes(row, owner_gold[event_id])
        axes = validate_axes(row.get("axes"))
        row["configuration"], row["human_direction"], row["ai_direction"] = legacy_configuration(axes)
        row["evidence_status"] = "sufficient" if axes["evidence_complete"] else "insufficient"
        row["public_signals"] = signals_from_axes(axes, distribution=row.get("distribution_signal", "not_shown"))
        row["multi_label_available"] = True
        row["public_takeaway"] = axes["human"]["evidence"]
        row["classification_audit"] = {
            "schema_version": "aieo_directional_evidence_audit_v2",
            "source_reading_complete": axes["evidence_complete"],
            "people_evidence": axes["human"]["evidence"],
            "ai_evidence": axes["ai"]["evidence"],
            "flags": [] if axes["evidence_complete"] else ["incomplete_written_source"],
        }
    summary = summarize_axes(result["evidence"])
    result["directional_summary"] = summary
    result["schema_version"] = "aieo_symbiosis_public_v2.0"
    result["direction_policy_version"] = AXIS_POLICY
    result["source_release_sha256"] = release["content_sha256"]
    result["public_status"] = "current_evidence_reading"
    result["scope_note"] = "Readings of what sources report, claim or anticipate about people and AI. These are not measures of verified real-world impact."
    result["classification_scope"] = {
        "unit": "development", "total": summary["total"],
        "complete_written_sources": summary["evidence_complete"],
        "incomplete_written_sources": summary["total"] - summary["evidence_complete"],
        "legacy_configuration_note": "Legacy single-label fields are retained for compatibility; independent axis categories are authoritative under the current policy.",
    }
    event = result["event"]
    categories = Counter(row["configuration"] for row in result["evidence"])
    all_categories = list(event.get("configuration_counts") or {})
    for name in ("configuration_counts", "display_configuration_counts"):
        event[name] = {key: categories[key] for key in dict.fromkeys(all_categories + list(categories))}
    for prefix in ("", "display_"):
        event[prefix + "classified_units"] = summary["total"]
        event[prefix + "complete_configuration_count"] = sum(categories[key] for key in PATTERNS)
        event[prefix + "partial_signal_count"] = sum(categories[key] for key in ("human_enabling_only", "human_constraining_only", "ai_enabling_only", "ai_constraining_only"))
        for name in ("no_clear_relational_signal", "ambiguous_relational_signal", "insufficient_evidence"):
            event[prefix + name + "_count"] = categories[name]
    reviewed_categories = Counter(row["configuration"] for row in result["evidence"] if row.get("reviewed"))
    for prefix, counts in (("", reviewed_categories), ("display_", categories)):
        core_total = sum(counts[key] for key in PATTERNS)
        event[prefix + "configuration_counts"] = {key: counts[key] for key in dict.fromkeys(all_categories + list(categories))}
        event[prefix + "complete_configuration_count"] = core_total
        event[prefix + "partial_signal_count"] = sum(counts[key] for key in ("human_enabling_only","human_constraining_only","ai_enabling_only","ai_constraining_only"))
        event[prefix + "core_four_distribution"] = {key: round(counts[key]/core_total,6) if core_total else 0.0 for key in PATTERNS}
        for name in ("no_clear_relational_signal","ambiguous_relational_signal","insufficient_evidence"):
            event[prefix + name + "_count"] = counts[name]
    event["reviewed_units"] = sum(bool(row.get("reviewed")) for row in result["evidence"])
    event["unreviewed_units"] = summary["total"] - event["reviewed_units"]
    people = result["people_signals"]
    people["classified_units"] = summary["total"]
    people["explicit_multi_label_units"] = summary["total"]
    people["people_signal_counts"] = {key: sum(row["public_signals"][key] for row in result["evidence"])
                                      for key in ("people_gaining", "people_losing_ground", "mixed_picture", "not_everyone_benefits", "not_clear_yet")}
    people["relationship_pattern_counts"] = {key: sum(bool(row["relationship_patterns"].get(key)) for row in result["evidence"]) for key in PATTERNS}
    people["not_clear_breakdown"] = {"not_enough_evidence": summary["human"]["unresolved"], "no_directional_people_change": summary["human"]["none"]}
    people["availability"]["mixed_picture"] = True
    people["relationship_patterns_assessed"] = sum(row.get("relationship_pattern_status") == "assessed" for row in result["evidence"])
    people["body_coverage_counts"] = {"complete_written_source": summary["evidence_complete"], "incomplete_written_source": summary["total"] - summary["evidence_complete"]}
    result["review"]["event_reviewed"] = sum(bool(row.get("reviewed")) for row in result["evidence"])
    for prefix in ("", "display_"):
        event[prefix + "human_direction_counts"] = dict(Counter(row["human_direction"] for row in result["evidence"]))
        event[prefix + "ai_direction_counts"] = dict(Counter(row["ai_direction"] for row in result["evidence"]))
    return result


def validate_directional_release(release: dict[str, Any], payload: dict[str, Any]) -> None:
    for key in ("release_id", "period_start", "period_end"):
        if payload.get(key) != release.get(key):
            raise ValueError(f"Directional {key} differs from the selected release")
    if payload.get("source_release_sha256") != release.get("content_sha256"):
        raise ValueError("Directional data belongs to another source revision")
    expected = {str(row.get("effective_event_id") or row.get("event_id")) for row in release["evidence"]
                if row.get("classification", {}).get("ai_relevant") is not False}
    rows = payload.get("evidence") or []
    if {row.get("event_id") for row in rows} != expected:
        raise ValueError("Directional data has missing or foreign developments")
    actual = summarize_axes(rows)
    if actual != payload.get("directional_summary"):
        raise ValueError("Declared directional totals differ from the source rows")
    if actual["total"] != release["counts"]["ai_relevant_event_records"]:
        raise ValueError("Directional denominator differs from the weekly release")
    for row in rows:
        if row["public_signals"] != signals_from_axes(row["axes"], distribution=row.get("distribution_signal", "not_shown")):
            raise ValueError("Public signals disagree with independent axes: " + row["event_id"])
        for key, sides in PATTERN_SIDES.items():
            if row["relationship_patterns"].get(key):
                if not all(has_side(row["axes"][axis]["direction"], side) for axis, side in zip(("human", "ai"), sides)):
                    raise ValueError("Linked pattern contradicts axis directions")
                if not str((row.get("relationship_evidence") or {}).get(key) or "").strip():
                    raise ValueError("Linked pattern has no linked evidence statement")


def export_public_csv(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["release_id", "event_id", "development", "human_direction", "ai_direction", "complete_written_source", "human_evidence", "ai_evidence", "source_urls"])
        for row in payload["evidence"]:
            axes = row["axes"]
            values = [payload["release_id"], row["event_id"], row["event_title"], axes["human"]["direction"], axes["ai"]["direction"], axes["evidence_complete"], axes["human"]["evidence"], axes["ai"]["evidence"], " | ".join(s.get("url", "") for s in row.get("sources", []))]
            writer.writerow(["'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v for v in values])
