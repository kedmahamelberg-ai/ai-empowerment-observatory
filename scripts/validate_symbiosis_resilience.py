#!/usr/bin/env python3
"""Fast guard for the relationship-classification recovery contract.

This check deliberately avoids Supabase and model dependencies.  It stops a
workflow from starting when a future edit would reintroduce the confidence
parsing crash or remove the durable checkpoint/resume path.
"""

from __future__ import annotations

from pathlib import Path

from symbiosis_common import (
    coerce_confidence,
    content_basis_for_storage,
    evidence_basis_covers,
    normalize_ai_role,
    validate_model_payload,
)


ROOT = Path(__file__).resolve().parents[1]
CLASSIFIER = ROOT / "scripts" / "classify_symbiosis.py"
WORKFLOW = ROOT / ".github" / "workflows" / "classify-current-symbiosis.yml"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> int:
    require(coerce_confidence("high") == 0.85, "high confidence must be accepted")
    require(coerce_confidence("medium") == 0.60, "medium confidence must be accepted")
    require(coerce_confidence("low") == 0.35, "low confidence must be accepted")
    require(coerce_confidence("very high") == 0.95, "very high confidence must be accepted")
    require(coerce_confidence("85%") == 0.85, "percentage confidence must be accepted")
    require(coerce_confidence("unrecognised label") == 0.0, "unknown confidence must remain non-fatal")
    require(
        normalize_ai_role("restriction") == "ai_restriction",
        "human-side shorthand in the AI role field must normalize",
    )
    require(
        normalize_ai_role("unknown model wording") == "unclear",
        "unknown AI role wording must remain conservative and non-fatal",
    )
    require(
        content_basis_for_storage("not_available") == "headline_only",
        "an unavailable full body must use the database-safe storage basis",
    )
    require(
        content_basis_for_storage("full_text") == "full_text",
        "a collected full body must retain its database storage basis",
    )

    model_payload = {
        "ai_relevant": True,
        "evidence_status": "partial",
        "relational_signal": "ai_only",
        "human_experience_type": "neutral",
        "ai_expressive_role": "ai_expansion",
        "human_reasoning": "No human outcome is stated.",
        "ai_reasoning": "The source describes growth.",
        "summary": "AI-side growth is described.",
        "topic": "business",
        "geographic_scope": "global",
        "country_iso3s": [],
        "relationship_patterns": {},
        "distribution_signal": "not_shown",
        "public_takeaway": "The source describes AI growth but no clear outcome for people.",
        "people_evidence": "no people outcome stated",
    }
    require(
        validate_model_payload({**model_payload, "confidence": "high"})["confidence"] == 0.85,
        "a full model payload with high confidence must be accepted",
    )
    alias_result = validate_model_payload(
        {**model_payload, "ai_expressive_role": "restriction"}
    )
    require(
        alias_result["ai_expressive_role"] == "ai_restriction",
        "an aliased AI role must be stored under the canonical value",
    )
    require(
        alias_result["normalization_warnings"],
        "aliased model values must be retained as an audit warning",
    )
    require(
        validate_model_payload(model_payload)["people_evidence"] == "no people outcome stated",
        "a compact people-evidence claim must be kept for later audit",
    )
    require(
        not evidence_basis_covers(
            stored_content_basis="headline_only",
            stored_evidence_summary={"source_count": 1, "headline_only_sources": 1},
            current_content_basis="full_text",
            current_evidence_summary={"source_count": 1, "full_text_sources": 1},
        ),
        "a headline-only success must not suppress a new full-body classification",
    )
    require(
        evidence_basis_covers(
            stored_content_basis="full_text",
            stored_evidence_summary={"source_count": 1, "full_text_sources": 1},
            current_content_basis="full_text",
            current_evidence_summary={"source_count": 1, "full_text_sources": 1},
        ),
        "a matching full-body success should remain reusable",
    )
    require(
        not evidence_basis_covers(
            stored_content_basis="multiple_sources",
            stored_evidence_summary={"source_count": 2, "headline_only_sources": 2},
            current_content_basis="multiple_sources",
            current_evidence_summary={
                "source_count": 2,
                "full_text_sources": 1,
                "headline_only_sources": 1,
            },
        ),
        "a multi-source row must be refreshed when one source gains a full body",
    )

    source = CLASSIFIER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    required_source = (
        "def resume_or_start_run(",
        "def saved_rows_for_run(",
        "def reusable_saved_rows(",
        "def checkpoint_run(",
        "--time-budget-minutes",
        "--status-output",
        "--resume-only",
        "time_budget_reached",
        "def classification_audit(",
        "def unavailable_full_body_result(",
        "FULL_BODY_REQUIRED_POLICY",
        "people_evidence",
        "classification_not_run",
        "content_basis_for_storage",
        "storage_content_basis",
    )
    for marker in required_source:
        require(marker in source, f"Missing symbiosis resilience marker: {marker}")
    require(
        'delete().eq("symbiosis_run_id", run_id)' not in source,
        "Symbiosis failures must preserve committed classifications for resume.",
    )
    for marker in (
        "pass_1:",
        "pass_2:",
        "pass_3:",
        "time-budget-minutes \"225\"",
        "resume_only:",
        "ensure_complete:",
    ):
        require(marker in workflow, f"Missing resumable workflow marker: {marker}")

    print("Validated symbiosis confidence parsing and resumable workflow contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
