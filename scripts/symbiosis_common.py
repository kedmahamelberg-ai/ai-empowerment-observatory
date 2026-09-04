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
CLASSIFIER_VERSION = "symbiosis_news_v0.5_full_body_required"
EVIDENCE_POLICY_VERSION = "aieo_evidence_basis_v5_full_body_required"
PUBLIC_SIGNAL_SCHEMA_VERSION = "aieo_people_signals_v1"

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

# Qwen occasionally emits the human-side shorthand (for example
# ``restriction``) in the AI-role field even though the prompt asks for the
# storage value ``ai_restriction``.  Keep the database contract canonical, but
# normalize the model boundary instead of spending three retries on a
# deterministic validation error.  Unknown values are deliberately mapped to
# ``unclear``; the raw model response and a warning are retained for review.
_AI_ROLE_ALIASES = {
    "extension": "ai_extension",
    "expansion": "ai_expansion",
    "restriction": "ai_restriction",
    "reduction": "ai_reduction",
    "enabling": "ai_extension",
    "enabled": "ai_extension",
    "growing": "ai_expansion",
    "growth": "ai_expansion",
    "adoption": "ai_expansion",
    "constraining": "ai_restriction",
    "constrained": "ai_restriction",
    "constraint": "ai_restriction",
    "blocked": "ai_restriction",
    "limited": "ai_restriction",
    "regulated": "ai_restriction",
    "failing": "ai_reduction",
    "failure": "ai_reduction",
    "degraded": "ai_reduction",
    "degradation": "ai_reduction",
    "withdrawn": "ai_reduction",
    "declining": "ai_reduction",
    "not_clear": "unclear",
    "no_clear": "unclear",
}

_HUMAN_TYPE_ALIASES = {
    "human_extension": "extension",
    "human_expansion": "expansion",
    "human_restriction": "restriction",
    "human_reduction": "reduction",
    "not_clear": "unclear",
    "no_clear": "unclear",
}

_EVIDENCE_STATUS_ALIASES = {
    "adequate": "sufficient",
    "enough": "sufficient",
    "complete": "sufficient",
    "limited": "partial",
    "incomplete": "partial",
    "not_enough": "insufficient",
    "insufficient_evidence": "insufficient",
}

_EVIDENCE_BASIS_RANK = {
    "not_available": -1,
    "headline_only": 0,
    "headline_and_snippet": 1,
    "article_summary": 2,
    "multiple_sources": 0,
    "full_text": 3,
    "full_text_supplied_by_owner": 3,
}


def _evidence_count(summary: dict[str, Any], key: str) -> int:
    try:
        return max(0, int(summary.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def evidence_basis_strength(
    content_basis: Any,
    evidence_summary: Any,
) -> tuple[int, int, int]:
    """Return comparable evidence strength as full bodies, best tier, sources.

    This is intentionally about input provenance, not the model's confidence.
    It lets a later full article body invalidate an older successful headline
    classification even when the unit key and codebook version are unchanged.
    """
    basis = _normalized_token(content_basis) or "headline_only"
    summary = evidence_summary if isinstance(evidence_summary, dict) else {}
    full = _evidence_count(summary, "full_text_sources")
    article_summaries = _evidence_count(summary, "article_summary_sources")
    snippets = _evidence_count(summary, "snippet_sources")
    headlines = _evidence_count(summary, "headline_only_sources")
    sources = _evidence_count(summary, "source_count")

    # Older rows may have a trustworthy content_basis but predate the detailed
    # input_evidence counters. Preserve that provenance rather than treating it
    # as absent.
    if basis in {"full_text", "full_text_supplied_by_owner"}:
        full = max(full, 1)
    elif basis == "article_summary":
        article_summaries = max(article_summaries, 1)
    elif basis == "headline_and_snippet":
        snippets = max(snippets, 1)
    elif basis == "headline_only" and not any((full, article_summaries, snippets, headlines)):
        headlines = 1

    sources = max(sources, full + article_summaries + snippets + headlines, 1)
    tier = _EVIDENCE_BASIS_RANK.get(basis, 0)
    if full:
        tier = max(tier, 3)
    elif article_summaries:
        tier = max(tier, 2)
    elif snippets:
        tier = max(tier, 1)
    return full, tier, sources


def evidence_basis_covers(
    *,
    stored_content_basis: Any,
    stored_evidence_summary: Any,
    current_content_basis: Any,
    current_evidence_summary: Any,
) -> bool:
    """Whether a saved classification used evidence at least as strong as now."""
    stored = evidence_basis_strength(stored_content_basis, stored_evidence_summary)
    current = evidence_basis_strength(current_content_basis, current_evidence_summary)
    return all(saved >= required for saved, required in zip(stored, current))


def classification_input_evidence(row: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Read the evidence provenance persisted beside one model result."""
    raw_output = row.get("raw_output") if isinstance(row.get("raw_output"), dict) else {}
    summary = raw_output.get("input_evidence")
    if not isinstance(summary, dict):
        summary = row.get("evidence_basis_summary")
    return (
        row.get("content_basis") or raw_output.get("content_basis") or "headline_only",
        summary if isinstance(summary, dict) else {},
    )


def release_full_text_requirements(
    release: dict[str, Any],
) -> tuple[dict[str, int], dict[str, int]]:
    """Return required full-body source counts for coverage and event units."""
    full_body_articles: set[str] = set()
    coverage_requirements: dict[str, int] = {}
    for row in release.get("units", {}).get("coverage_articles", []) or []:
        if not isinstance(row, dict) or not row.get("article_id"):
            continue
        classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
        if classification.get("ai_relevant") is False:
            continue
        article_id = str(row["article_id"])
        if str(classification.get("content_basis") or "") == "full_text":
            full_body_articles.add(article_id)
            coverage_requirements[article_id] = 1

    event_requirements: dict[str, int] = {}
    for event in release.get("evidence") or []:
        if not isinstance(event, dict):
            continue
        classification = event.get("classification") if isinstance(event.get("classification"), dict) else {}
        if classification.get("ai_relevant") is False:
            continue
        event_id = str(event.get("effective_event_id") or event.get("event_id") or "").strip()
        if not event_id:
            continue
        member_ids = [str(value) for value in event.get("member_article_ids") or [] if value]
        if not member_ids:
            member_ids = [
                str(source.get("article_id"))
                for source in event.get("sources") or []
                if isinstance(source, dict) and source.get("article_id")
            ]
        full_count = sum(article_id in full_body_articles for article_id in set(member_ids))
        if not full_count and str(classification.get("content_basis") or "") == "full_text":
            full_count = 1
        if full_count:
            event_requirements[event_id] = full_count
    return coverage_requirements, event_requirements


def _normalized_token(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_").strip("_")


def normalize_ai_role(value: Any) -> str:
    """Return a canonical AI role without allowing model wording to abort a run."""
    token = _normalized_token(value)
    if token in AI_ROLES:
        return token
    return _AI_ROLE_ALIASES.get(token, "unclear")


def normalize_human_type(value: Any) -> str:
    """Return a canonical human experience type at the model boundary."""
    token = _normalized_token(value)
    if token in HUMAN_TYPES:
        return token
    return _HUMAN_TYPE_ALIASES.get(token, "unclear")


def normalize_evidence_status(value: Any) -> str:
    """Return a canonical evidence status; unknown status is conservatively insufficient."""
    token = _normalized_token(value)
    if token in EVIDENCE_STATUSES:
        return token
    return _EVIDENCE_STATUS_ALIASES.get(token, "insufficient")

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

RELATIONSHIP_PATTERN_KEYS = (
    "mutualism",
    "ai_benefiting_parasitism",
    "human_benefiting_parasitism",
    "competition",
)

DISTRIBUTION_SIGNALS = {
    "broadly_shared",
    "unequal",
    "not_shown",
    "unclear",
}


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y", "present"}


def coerce_confidence(value: Any) -> float:
    """Return a bounded numeric confidence without letting a model label abort a run.

    Models sometimes use a qualitative label even when prompted for a number.
    The label is still useful diagnostic information, but it must never turn a
    valid classification into a failed multi-hour workflow.  Keep the public
    storage contract numeric and use a conservative, documented mapping.
    """
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))

    raw = str(value or "").strip().casefold()
    if not raw:
        return 0.0
    normalized = raw.replace("-", "_").replace(" ", "_")
    labels = {
        "very_high": 0.95,
        "high": 0.85,
        "medium_high": 0.70,
        "medium": 0.60,
        "medium_low": 0.45,
        "low": 0.35,
        "very_low": 0.15,
        "unknown": 0.0,
        "unclear": 0.0,
        "none": 0.0,
        "n_a": 0.0,
    }
    if normalized in labels:
        return labels[normalized]

    try:
        numeric = float(raw[:-1]) / 100.0 if raw.endswith("%") else float(raw)
    except (TypeError, ValueError):
        # An unsupported model value is diagnostic only.  Evidence and labels
        # remain reviewable, so retain the row with the least assertive score.
        return 0.0
    return max(0.0, min(1.0, numeric))


def normalize_relationship_patterns(
    value: Any,
    *,
    fallback_configuration: str | None = None,
) -> tuple[dict[str, bool], bool]:
    """Normalize multi-label pattern flags and report whether they were explicit.

    Older releases contain one configuration per development. They remain
    readable through the fallback while newer classifier and owner-QC records
    may mark several patterns in the same development.
    """
    patterns = {key: False for key in RELATIONSHIP_PATTERN_KEYS}
    explicit = False
    if isinstance(value, dict):
        explicit = any(key in value for key in RELATIONSHIP_PATTERN_KEYS)
        for key in RELATIONSHIP_PATTERN_KEYS:
            if key in value:
                patterns[key] = _boolean(value.get(key))
    elif isinstance(value, (list, tuple, set)):
        explicit = True
        selected = {str(item or "").strip() for item in value}
        for key in RELATIONSHIP_PATTERN_KEYS:
            patterns[key] = key in selected
    elif isinstance(value, str) and value.strip():
        explicit = True
        selected = {item.strip() for item in value.split("|") if item.strip()}
        for key in RELATIONSHIP_PATTERN_KEYS:
            patterns[key] = key in selected

    if not explicit and fallback_configuration in patterns:
        patterns[str(fallback_configuration)] = True
    return patterns, explicit


def normalize_distribution_signal(value: Any) -> tuple[str, bool]:
    raw = str(value or "").strip().casefold().replace(" ", "_")
    aliases = {
        "yes": "unequal",
        "not_everyone_benefits": "unequal",
        "some_more_than_others": "unequal",
        "some_people_benefit_more": "unequal",
        "no": "not_shown",
        "not_evident": "not_shown",
        "not_applicable": "not_shown",
        "equal": "broadly_shared",
        "shared": "broadly_shared",
        "not_sure": "unclear",
    }
    normalized = aliases.get(raw, raw)
    if normalized in DISTRIBUTION_SIGNALS:
        return normalized, True
    return "not_shown", False


def public_signals_from_patterns(
    patterns: dict[str, bool],
    *,
    configuration: str | None,
    human_direction: str | None,
    evidence_status: str | None,
    distribution_signal: str,
) -> dict[str, bool]:
    gaining = bool(
        patterns.get("mutualism")
        or patterns.get("human_benefiting_parasitism")
        or configuration == "human_enabling_only"
        or (not any(patterns.values()) and human_direction == "enabling")
    )
    losing = bool(
        patterns.get("ai_benefiting_parasitism")
        or patterns.get("competition")
        or configuration == "human_constraining_only"
        or (not any(patterns.values()) and human_direction == "constraining")
    )
    return {
        "people_gaining": gaining,
        "people_losing_ground": losing,
        "mixed_picture": gaining and losing,
        "not_everyone_benefits": distribution_signal == "unequal",
        "not_clear_yet": not gaining and not losing,
    }


def public_signal_payload(
    *,
    raw_payload: dict[str, Any] | None,
    configuration: str | None,
    human_direction: str | None,
    evidence_status: str | None,
    public_takeaway: str = "",
) -> dict[str, Any]:
    raw = raw_payload if isinstance(raw_payload, dict) else {}
    nested = raw.get("model_response") if isinstance(raw.get("model_response"), dict) else {}
    pattern_value = (
        raw.get("relationship_patterns")
        if "relationship_patterns" in raw
        else nested.get("relationship_patterns")
    )
    patterns, explicit_patterns = normalize_relationship_patterns(
        pattern_value,
        fallback_configuration=configuration,
    )
    distribution_value = (
        raw.get("distribution_signal")
        if "distribution_signal" in raw
        else raw.get("human_distribution")
        if "human_distribution" in raw
        else nested.get("distribution_signal", nested.get("human_distribution"))
    )
    distribution_signal, distribution_explicit = normalize_distribution_signal(distribution_value)
    takeaway = str(
        raw.get("public_takeaway")
        or nested.get("public_takeaway")
        or public_takeaway
        or ""
    ).strip()
    if evidence_status == "insufficient":
        patterns = {key: False for key in RELATIONSHIP_PATTERN_KEYS}
    signals = public_signals_from_patterns(
        patterns,
        configuration=configuration,
        human_direction=human_direction,
        evidence_status=evidence_status,
        distribution_signal=distribution_signal,
    )
    return {
        "schema_version": PUBLIC_SIGNAL_SCHEMA_VERSION,
        "relationship_patterns": patterns,
        "public_signals": signals,
        "distribution_signal": distribution_signal,
        "public_takeaway": takeaway,
        "multi_label_available": explicit_patterns,
        "distribution_coded": distribution_explicit,
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
    raw_human_type = str(payload.get("human_experience_type") or "").strip()
    raw_ai_role = str(payload.get("ai_expressive_role") or "").strip()
    raw_evidence_status = str(payload.get("evidence_status") or "").strip()
    human_type = normalize_human_type(raw_human_type)
    ai_role = normalize_ai_role(raw_ai_role)
    evidence_status = normalize_evidence_status(raw_evidence_status)
    normalization_warnings: list[str] = []
    if _normalized_token(raw_human_type) != human_type:
        normalization_warnings.append(
            f"human_experience_type {raw_human_type!r} normalized to {human_type!r}"
        )
    if _normalized_token(raw_ai_role) != ai_role:
        normalization_warnings.append(
            f"ai_expressive_role {raw_ai_role!r} normalized to {ai_role!r}"
        )
    if _normalized_token(raw_evidence_status) != evidence_status:
        normalization_warnings.append(
            f"evidence_status {raw_evidence_status!r} normalized to {evidence_status!r}"
        )

    # A genuinely insufficient unit cannot support directional or neutral
    # component claims. Normalize the components to unclear so future model
    # outputs and owner gold use one internally coherent representation.
    if evidence_status == "insufficient":
        human_type = "unclear"
        ai_role = "unclear"

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
    confidence = coerce_confidence(payload.get("confidence", 0.0))
    public_layer = public_signal_payload(
        raw_payload=payload,
        configuration=configuration,
        human_direction=human_direction,
        evidence_status=evidence_status,
        public_takeaway=str(payload.get("public_takeaway") or payload.get("summary") or ""),
    )

    people_evidence = " ".join(str(payload.get("people_evidence") or "").split())[:280]
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
        "people_evidence": people_evidence,
        "summary": str(payload.get("summary") or "").strip(),
        "confidence": confidence,
        "topic": str(payload.get("topic") or "other").strip(),
        "geographic_scope": str(payload.get("geographic_scope") or "unclear").strip(),
        "country_iso3s": countries,
        "model_relational_signal": model_signal,
        "normalization_warnings": normalization_warnings,
        **public_layer,
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
    public_layer = public_signal_payload(
        raw_payload=row.get("raw_output") if isinstance(row.get("raw_output"), dict) else {},
        configuration=str(configuration or ""),
        human_direction=str(human_direction or ""),
        evidence_status=str(evidence_status or ""),
        public_takeaway=str(
            row.get("final_evidence_summary")
            or row.get("model_summary")
            or ""
        ),
    )
    raw_output = row.get("raw_output") if isinstance(row.get("raw_output"), dict) else {}
    input_evidence = (
        raw_output.get("input_evidence")
        if isinstance(raw_output.get("input_evidence"), dict)
        else {}
    )
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
        "content_basis": row.get("content_basis") or raw_output.get("content_basis") or "headline_only",
        "evidence_basis_summary": input_evidence,
        "story_country_iso3s": row.get("final_story_country_iso3s") or row.get("country_iso3s") or [],
        "evidence_summary": row.get("final_evidence_summary") or row.get("model_summary") or "",
        "reasoning": row.get("final_reasoning") or row.get("model_summary") or "",
        "empowerment_status": row.get("final_empowerment_status"),
        "empowerment_degree": row.get("final_empowerment_degree"),
        "empowerment_reasoning": row.get("final_empowerment_reasoning"),
        "classification_audit": raw_output.get("classification_audit")
        if isinstance(raw_output.get("classification_audit"), dict)
        else {},
        **public_layer,
    }
