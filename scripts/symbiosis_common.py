#!/usr/bin/env python3
"""Shared AIEO symbiosis definitions.

The codebook adapts the user's human-GenAI configuration ecology to AI-news
coverage. It classifies how a source represents the relationship. It does not
claim objective system performance, consciousness, or biological fitness.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

CODEBOOK_VERSION = "aieo_news_symbiosis_v0.1"
CLASSIFIER_VERSION = "symbiosis_news_v0.1"
EVIDENCE_POLICY_VERSION = "aieo_evidence_basis_v2"

HUMAN_TYPES = {
    "extension",
    "expansion",
    "restriction",
    "reduction",
    "neutral",
    "unclear",
}
AI_ROLES = {
    "ai_extension",
    "ai_expansion",
    "ai_restriction",
    "ai_reduction",
    "neutral",
    "unclear",
}
EVIDENCE_STATUSES = {"sufficient", "partial", "insufficient"}
RELATIONAL_SIGNALS = {"complete", "human_only", "ai_only", "none", "unclear"}
EMPOWERMENT_STATUSES = {
    "expanding",
    "contracting",
    "mixed",
    "non_empowerment",
    "unclear",
}

def release_identifier(payload: dict[str, Any], source_path: str | Path | None = None) -> str:
    """Return a stable publication identifier for weekly releases and references."""
    for key in ("release_id", "snapshot_id"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    if source_path is not None:
        return Path(source_path).stem
    return ""


def release_unit_ids(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return public coverage and event IDs available for item-level review."""
    articles: set[str] = set()
    events: set[str] = set()

    coverage_rows = payload.get("units", {}).get("coverage_articles", []) or []
    for row in coverage_rows:
        if not isinstance(row, dict) or not row.get("article_id"):
            continue
        if row.get("classification", {}).get("ai_relevant") is False:
            continue
        articles.add(str(row["article_id"]))

    for event in payload.get("evidence") or []:
        if not isinstance(event, dict):
            continue
        if event.get("classification", {}).get("ai_relevant") is False:
            continue
        event_id = str(event.get("effective_event_id") or event.get("event_id") or "").strip()
        if event_id:
            events.add(event_id)
        if not coverage_rows:
            for article_id in event.get("member_article_ids") or []:
                if article_id:
                    articles.add(str(article_id))
            for source in event.get("sources") or []:
                if isinstance(source, dict) and source.get("article_id"):
                    articles.add(str(source["article_id"]))

    return sorted(articles), sorted(events)


def release_review_scope(
    payload: dict[str, Any],
    source_path: str | Path | None = None,
) -> dict[str, Any]:
    """Describe whether a publication contains evidence that can be reviewed."""
    release_id = release_identifier(payload, source_path)
    article_ids, event_ids = release_unit_ids(payload)
    reviewable = bool(article_ids or event_ids)
    schema_version = str(payload.get("schema_version") or "")
    snapshot_type = str(payload.get("snapshot_type") or "")
    aggregate_reference = bool(
        not reviewable
        and (
            schema_version.startswith("aieo_historical_snapshot_")
            or snapshot_type
            or payload.get("review_scope") == "aggregate_reference_only"
        )
    )
    if reviewable:
        reason = "unit_level_evidence_available"
    elif aggregate_reference:
        reason = "aggregate_reference_without_unit_level_evidence"
    else:
        reason = "publication_without_reviewable_unit_ids"
    return {
        "release_id": release_id,
        "reviewable": reviewable,
        "aggregate_reference": aggregate_reference,
        "reason": reason,
        "coverage_units": len(article_ids),
        "event_units": len(event_ids),
        "source_path": str(source_path or ""),
    }


HUMAN_DIRECTION = {
    "extension": "enabling",
    "expansion": "enabling",
    "restriction": "constraining",
    "reduction": "constraining",
    "neutral": "neutral",
    "unclear": "unclear",
}
AI_DIRECTION = {
    "ai_extension": "enabling",
    "ai_expansion": "enabling",
    "ai_restriction": "constraining",
    "ai_reduction": "constraining",
    "neutral": "neutral",
    "unclear": "unclear",
}

PLAIN_LABELS = {
    "mutualism": "Both people and the AI side are represented as gaining",
    "ai_benefiting_parasitism": "The AI or operator side gains while people are constrained",
    "human_benefiting_parasitism": "People gain while the AI system is constrained",
    "competition": "People and the AI side are both constrained",
    "human_enabling_only": "A human-side gain is visible; the AI side is not established",
    "human_constraining_only": "A human-side cost is visible; the AI side is not established",
    "ai_enabling_only": "An AI-side gain is visible; the human side is not established",
    "ai_constraining_only": "An AI-side constraint is visible; the human side is not established",
    "no_clear_relational_signal": "No clear human-AI relationship is described",
    "ambiguous_relational_signal": "A relationship is suggested, but its direction is unclear",
    "insufficient_evidence": "There is not enough source evidence to classify the relationship",
}

TECHNICAL_LABELS = {
    "mutualism": "Mutualism",
    "ai_benefiting_parasitism": "AI-benefiting parasitism",
    "human_benefiting_parasitism": "Human-benefiting parasitism",
    "competition": "Competition or co-constraint",
    "human_enabling_only": "Human-side enabling signal only",
    "human_constraining_only": "Human-side constraining signal only",
    "ai_enabling_only": "AI-side enabling signal only",
    "ai_constraining_only": "AI-side constraining signal only",
    "no_clear_relational_signal": "No clear relational signal",
    "ambiguous_relational_signal": "Ambiguous relational signal",
    "insufficient_evidence": "Insufficient evidence",
}

CORE_FOUR = {
    "mutualism",
    "ai_benefiting_parasitism",
    "human_benefiting_parasitism",
    "competition",
}
PARTIALS = {
    "human_enabling_only",
    "human_constraining_only",
    "ai_enabling_only",
    "ai_constraining_only",
}


def derive_configuration(
    human_type: str,
    ai_role: str,
    evidence_status: str,
) -> tuple[str, str, str, str]:
    """Return configuration, human direction, AI direction, and plain label."""
    if human_type not in HUMAN_TYPES:
        raise ValueError(f"Unknown human experience type: {human_type}")
    if ai_role not in AI_ROLES:
        raise ValueError(f"Unknown AI expressive role: {ai_role}")
    if evidence_status not in EVIDENCE_STATUSES:
        raise ValueError(f"Unknown evidence status: {evidence_status}")

    human_direction = HUMAN_DIRECTION[human_type]
    ai_direction = AI_DIRECTION[ai_role]

    if evidence_status == "insufficient":
        configuration = "insufficient_evidence"
    elif "unclear" in {human_direction, ai_direction}:
        configuration = "ambiguous_relational_signal"
    elif human_direction == "enabling" and ai_direction == "enabling":
        configuration = "mutualism"
    elif human_direction == "constraining" and ai_direction == "enabling":
        configuration = "ai_benefiting_parasitism"
    elif human_direction == "enabling" and ai_direction == "constraining":
        configuration = "human_benefiting_parasitism"
    elif human_direction == "constraining" and ai_direction == "constraining":
        configuration = "competition"
    elif human_direction == "enabling" and ai_direction == "neutral":
        configuration = "human_enabling_only"
    elif human_direction == "constraining" and ai_direction == "neutral":
        configuration = "human_constraining_only"
    elif human_direction == "neutral" and ai_direction == "enabling":
        configuration = "ai_enabling_only"
    elif human_direction == "neutral" and ai_direction == "constraining":
        configuration = "ai_constraining_only"
    else:
        configuration = "no_clear_relational_signal"

    return (
        configuration,
        human_direction,
        ai_direction,
        PLAIN_LABELS[configuration],
    )


def infer_relational_signal(human_type: str, ai_role: str, evidence_status: str) -> str:
    if evidence_status == "insufficient":
        return "unclear"
    human_direction = HUMAN_DIRECTION[human_type]
    ai_direction = AI_DIRECTION[ai_role]
    if "unclear" in {human_direction, ai_direction}:
        return "unclear"
    if human_direction != "neutral" and ai_direction != "neutral":
        return "complete"
    if human_direction != "neutral" and ai_direction == "neutral":
        return "human_only"
    if human_direction == "neutral" and ai_direction != "neutral":
        return "ai_only"
    return "none"


def validate_model_payload(payload: dict[str, Any]) -> dict[str, Any]:
    human_type = str(payload.get("human_experience_type") or "").strip()
    ai_role = str(payload.get("ai_expressive_role") or "").strip()
    evidence_status = str(payload.get("evidence_status") or "").strip()
    if human_type not in HUMAN_TYPES:
        raise ValueError(f"Invalid human_experience_type: {human_type}")
    if ai_role not in AI_ROLES:
        raise ValueError(f"Invalid ai_expressive_role: {ai_role}")
    if evidence_status not in EVIDENCE_STATUSES:
        raise ValueError(f"Invalid evidence_status: {evidence_status}")

    configuration, human_direction, ai_direction, plain_label = derive_configuration(
        human_type,
        ai_role,
        evidence_status,
    )
    relational_signal = infer_relational_signal(human_type, ai_role, evidence_status)
    model_signal = str(payload.get("relational_signal") or relational_signal).strip()
    if model_signal not in RELATIONAL_SIGNALS:
        model_signal = relational_signal

    countries = [
        str(value).strip().upper()
        for value in (payload.get("country_iso3s") or [])
        if str(value).strip()
    ]
    confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.0))))

    return {
        "ai_relevant": bool(payload.get("ai_relevant", True)),
        "evidence_status": evidence_status,
        "relational_signal": relational_signal,
        "human_experience_type": human_type,
        "ai_expressive_role": ai_role,
        "human_direction": human_direction,
        "ai_direction": ai_direction,
        "configuration": configuration,
        "plain_label": plain_label,
        "technical_label": TECHNICAL_LABELS[configuration],
        "human_reasoning": str(payload.get("human_reasoning") or "").strip(),
        "ai_reasoning": str(payload.get("ai_reasoning") or "").strip(),
        "summary": str(payload.get("summary") or "").strip(),
        "confidence": confidence,
        "topic": str(payload.get("topic") or "other").strip(),
        "geographic_scope": str(payload.get("geographic_scope") or "unclear").strip(),
        "country_iso3s": countries,
        "model_relational_signal": model_signal,
    }


def final_payload_from_classification(row: dict[str, Any]) -> dict[str, Any]:
    """Return reviewed values when present, otherwise model values."""
    reviewed = row.get("review_status") in {"accepted", "corrected", "insufficient_evidence"}
    configuration = row.get("final_configuration") if reviewed else row.get("model_configuration")
    human_type = (
        row.get("final_human_experience_type") if reviewed else row.get("model_human_experience_type")
    )
    ai_role = row.get("final_ai_expressive_role") if reviewed else row.get("model_ai_expressive_role")
    human_direction = row.get("final_human_direction") if reviewed else row.get("model_human_direction")
    ai_direction = row.get("final_ai_direction") if reviewed else row.get("model_ai_direction")
    evidence_status = row.get("final_evidence_status") if reviewed else row.get("evidence_status")
    return {
        "reviewed": reviewed,
        "review_status": row.get("review_status", "pending"),
        "configuration": configuration,
        "plain_label": (
            row.get("final_plain_label") if reviewed else row.get("model_plain_label")
        ) or PLAIN_LABELS.get(str(configuration), "Relationship under review"),
        "technical_label": TECHNICAL_LABELS.get(str(configuration), "Relationship under review"),
        "human_experience_type": human_type,
        "ai_expressive_role": ai_role,
        "human_direction": human_direction,
        "ai_direction": ai_direction,
        "evidence_status": evidence_status,
        "story_country_iso3s": row.get("final_story_country_iso3s") or row.get("country_iso3s") or [],
        "evidence_summary": row.get("final_evidence_summary") or row.get("model_summary") or "",
        "reasoning": row.get("final_reasoning") or row.get("model_summary") or "",
        "empowerment_status": row.get("final_empowerment_status"),
        "empowerment_degree": row.get("final_empowerment_degree"),
        "empowerment_reasoning": row.get("final_empowerment_reasoning"),
    }
