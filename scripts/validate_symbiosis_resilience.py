#!/usr/bin/env python3
"""Fast guard for the relationship-classification recovery contract.

This check deliberately avoids Supabase and model dependencies.  It stops a
workflow from starting when a future edit would reintroduce the confidence
parsing crash or remove the durable checkpoint/resume path.
"""

from __future__ import annotations

from pathlib import Path

from symbiosis_common import coerce_confidence, validate_model_payload


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
    }
    require(
        validate_model_payload({**model_payload, "confidence": "high"})["confidence"] == 0.85,
        "a full model payload with high confidence must be accepted",
    )

    source = CLASSIFIER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    required_source = (
        "def resume_or_start_run(",
        "def saved_rows_for_run(",
        "def checkpoint_run(",
        "--time-budget-minutes",
        "--status-output",
        "time_budget_reached",
    )
    for marker in required_source:
        require(marker in source, f"Missing symbiosis resilience marker: {marker}")
    require(
        'delete().eq("symbiosis_run_id", run_id)' not in source,
        "Symbiosis failures must preserve committed classifications for resume.",
    )
    for marker in ("pass_1:", "pass_2:", "pass_3:", "time-budget-minutes \"225\"", "ensure_complete:"):
        require(marker in workflow, f"Missing resumable workflow marker: {marker}")

    print("Validated symbiosis confidence parsing and resumable workflow contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
