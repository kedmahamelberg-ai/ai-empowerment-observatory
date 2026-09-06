"""Versioned, independent readings of human and AI/operator directions.

Directions describe source representations (including attributed claims and
risks), not verified causal impacts. Mixed directions are substantive. Missing
evidence and failed model execution are separate states. This JSON contract
lives in raw_output, so older database enums need no destructive migration.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

AXIS_SCHEMA = "aieo_independent_directions_v2"
AXIS_POLICY = "source_representations_independent_axes_2026_09"
DIRECTIONS = ("gain", "loss", "mixed", "none", "unresolved")
PATTERNS = ("mutualism", "ai_benefiting_parasitism", "human_benefiting_parasitism", "competition")
PATTERN_SIDES = {
    "mutualism": ("gain", "gain"),
    "ai_benefiting_parasitism": ("loss", "gain"),
    "human_benefiting_parasitism": ("gain", "loss"),
    "competition": ("loss", "loss"),
}
PEOPLE_OUTCOMES = {
    "gain": "benefit_shown", "loss": "downside_shown",
    "mixed": "benefit_and_downside", "none": "no_clear_people_change",
    "unresolved": "too_little_evidence",
}


def direction(value: Any) -> str:
    value = str(value or "").strip()
    if value not in DIRECTIONS:
        raise ValueError(f"Invalid independent direction: {value!r}")
    return value


def has_side(value: str, side: str) -> bool:
    return value == side or value == "mixed"


def make_axes(human: str, ai: str, *, human_evidence: str = "", ai_evidence: str = "",
              complete_evidence: bool = True, evidence_types: list[str] | None = None,
              evidence_scope: str = "written_source", unresolved_reason: str = "") -> dict[str, Any]:
    return {
        "schema_version": AXIS_SCHEMA,
        "policy_version": AXIS_POLICY,
        "human": {"direction": direction(human), "evidence": human_evidence.strip()},
        "ai": {"direction": direction(ai), "evidence": ai_evidence.strip()},
        "evidence_complete": bool(complete_evidence),
        "evidence_scope": evidence_scope,
        "evidence_types": list(dict.fromkeys(evidence_types or [])),
        "unresolved_reason": unresolved_reason,
    }


def validate_axes(axes: Any) -> dict[str, Any]:
    if not isinstance(axes, dict) or axes.get("schema_version") != AXIS_SCHEMA:
        raise ValueError("Missing independent-direction schema")
    if axes.get("policy_version") != AXIS_POLICY:
        raise ValueError("Independent-direction policy version is missing or unsupported")
    for side in ("human", "ai"):
        part = axes.get(side)
        if not isinstance(part, dict):
            raise ValueError(f"Missing {side} axis")
        d = direction(part.get("direction"))
        if d in {"gain", "loss", "mixed"} and not str(part.get("evidence") or "").strip():
            raise ValueError(f"Directional {side} result has no supporting evidence statement")
    if not isinstance(axes.get("evidence_complete"), bool):
        raise ValueError("Missing evidence completeness state")
    if not axes["evidence_complete"] and any(axes[s]["direction"] == "none" for s in ("human", "ai")):
        raise ValueError("Incomplete evidence cannot support an exhaustive no-direction result")
    return axes


def model_axes(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Read the v2 model response without deriving one axis from the other."""
    values = payload.get("axis_directions")
    if not isinstance(values, dict):
        axes = payload.get("axes")
        return validate_axes(axes) if axes is not None else None
    return validate_axes(make_axes(
        direction(values.get("human")), direction(values.get("ai")),
        human_evidence=str(payload.get("human_reasoning") or payload.get("people_evidence") or ""),
        ai_evidence=str(payload.get("ai_reasoning") or ""),
        complete_evidence=True,  # Model calls receive validated written evidence; uncertainty is an axis state.
        evidence_types=payload.get("evidence_types") or ["source_representation"],
    ))


def axes_in_raw(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("axes"), dict):
        return validate_axes(raw["axes"])
    nested = raw.get("model_response")
    return model_axes(nested) if isinstance(nested, dict) else model_axes(raw)


def signals_from_axes(axes: dict[str, Any], *, distribution: str = "not_shown") -> dict[str, bool]:
    human = validate_axes(axes)["human"]["direction"]
    gain, loss = has_side(human, "gain"), has_side(human, "loss")
    return {
        "people_gaining": gain, "people_losing_ground": loss,
        "mixed_picture": gain and loss, "not_everyone_benefits": distribution == "unequal",
        "not_clear_yet": not gain and not loss,
    }


def supported_patterns(axes: dict[str, Any], patterns: dict[str, Any],
                       linked_evidence: dict[str, Any] | None = None) -> dict[str, bool]:
    """An axis cross-product is not evidence of a linked relationship."""
    linked = linked_evidence or {}
    return {
        key: bool(patterns.get(key)) and bool(str(linked.get(key) or "").strip())
        and has_side(axes["human"]["direction"], sides[0])
        and has_side(axes["ai"]["direction"], sides[1])
        for key, sides in PATTERN_SIDES.items()
    }


def summarize_axes(rows: list[dict[str, Any]], *, id_key: str = "event_id") -> dict[str, Any]:
    ids = [str(row.get(id_key) or "") for row in rows]
    if any(not key for key in ids) or len(ids) != len(set(ids)):
        raise ValueError("Directional summary needs one unique stable ID per record")
    counts = {side: Counter() for side in ("human", "ai")}
    for row in rows:
        axes = validate_axes(row.get("axes"))
        for side in counts:
            counts[side][axes[side]["direction"]] += 1
    return {
        "schema_version": AXIS_SCHEMA, "policy_version": AXIS_POLICY,
        "total": len(rows),
        "human": {key: counts["human"][key] for key in DIRECTIONS},
        "ai": {key: counts["ai"][key] for key in DIRECTIONS},
        "evidence_complete": sum(row["axes"]["evidence_complete"] for row in rows),
        "counting_rule": "Each axis partitions the same records. Mixed is one category. The two axes are not added together.",
    }


def merge_chunk_axes(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Union supported directions across every source segment, retaining mixed.

    The caller must finish every segment before saving a unit. Any unresolved
    segment prevents a no-direction conclusion; explicit findings can remain.
    """
    axes_list = [validate_axes(result["axes"]) for result in results]
    merged: dict[str, str] = {}
    explanations: dict[str, str] = {}
    for side in ("human", "ai"):
        values = [axes[side]["direction"] for axes in axes_list]
        gain = any(has_side(value, "gain") for value in values)
        loss = any(has_side(value, "loss") for value in values)
        merged[side] = "mixed" if gain and loss else "gain" if gain else "loss" if loss else "unresolved" if "unresolved" in values else "none"
        explanations[side] = " ".join(dict.fromkeys(
            axes[side]["evidence"] for axes in axes_list
            if axes[side]["evidence"] and (axes[side]["direction"] not in {"none", "unresolved"} or merged[side] in {"none", "unresolved"})
        ))
    return make_axes(merged["human"], merged["ai"],
                     human_evidence=explanations["human"], ai_evidence=explanations["ai"],
                     evidence_types=list(dict.fromkeys(t for axes in axes_list for t in axes["evidence_types"])))
