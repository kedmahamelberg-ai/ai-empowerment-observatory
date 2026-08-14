#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supabase import Client, create_client

ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / "validation" / "aieo-stage7c-classification-audit.csv"
CORRECTIONS_PATH = ROOT / "validation" / "stage7c-corrections-v1.json"

REVIEW_OUTPUT = ROOT / "review" / "classification" / "latest.json"
PUBLIC_OUTPUT = ROOT / "data" / "lenses" / "latest.json"
METHODOLOGY_OUTPUT = ROOT / "data" / "methodology" / "latest.json"

REVIEWER = "Kedma Hamelberg"
MIN_COUNTRY_SIGNAL_N = 3

VALID_DIMENSIONS = {
    "operational",
    "creative",
    "agentic",
    "normative",
}


class AuditError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise AuditError(f"{name} is missing.")
    return value


def first_row(response: Any, context: str) -> dict[str, Any]:
    data = getattr(response, "data", None)
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict) and data:
        return data
    raise AuditError(f"No row while {context}.")


def unit_score(status: str, degree: int) -> float | None:
    if status == "expanding":
        return round(degree / 3.0, 6)
    if status == "contracting":
        return round(-degree / 3.0, 6)
    if status in {"mixed", "non_empowerment"}:
        return 0.0
    return None


def load_audit() -> list[dict[str, str]]:
    if not AUDIT_PATH.exists():
        raise AuditError(f"Missing audit CSV: {AUDIT_PATH}")

    rows = list(
        csv.DictReader(
            AUDIT_PATH.open("r", encoding="utf-8-sig", newline="")
        )
    )

    if not rows:
        raise AuditError("Audit CSV is empty.")

    return rows


def load_manifest() -> dict[str, Any]:
    if not CORRECTIONS_PATH.exists():
        raise AuditError(f"Missing corrections manifest: {CORRECTIONS_PATH}")

    return json.loads(CORRECTIONS_PATH.read_text(encoding="utf-8"))


def load_classification(
    client: Client,
    classification_id: str,
) -> dict[str, Any]:
    response = (
        client.table("lens_classifications")
        .select(
            "lens_classification_id,classification_run_id,lens,"
            "article_id,event_id,ai_relevant,empowerment_status,"
            "empowerment_degree,unit_score,narrative_frame,"
            "distribution_breadth,dominant_dimension,ai_authority_shift,"
            "topic,geographic_scope,primary_country_iso3,country_iso3s,"
            "content_basis,confidence,reasoning,raw_output"
        )
        .eq("lens_classification_id", classification_id)
        .limit(1)
        .execute()
    )

    return first_row(response, f"loading classification {classification_id}")


def upsert_review(
    client: Client,
    *,
    classification_id: str,
    review_status: str,
    notes: str,
    final_output: dict[str, Any] | None,
) -> None:
    existing = (
        client.table("lens_human_reviews")
        .select("lens_human_review_id")
        .eq("lens_classification_id", classification_id)
        .eq("reviewer_name", REVIEWER)
        .order("reviewed_at", desc=True)
        .limit(1)
        .execute()
    )

    payload = {
        "lens_classification_id": classification_id,
        "reviewer_name": REVIEWER,
        "review_status": review_status,
        "notes": notes or None,
        "final_output": final_output,
        "reviewed_at": now_iso(),
    }

    data = getattr(existing, "data", None) or []

    if data:
        (
            client.table("lens_human_reviews")
            .update(payload)
            .eq("lens_human_review_id", data[0]["lens_human_review_id"])
            .execute()
        )
    else:
        client.table("lens_human_reviews").insert(payload).execute()


def update_dimensions(
    client: Client,
    classification_id: str,
    dimensions: dict[str, Any],
    confidence: float | None,
) -> None:
    (
        client.table("lens_dimensions")
        .delete()
        .eq("lens_classification_id", classification_id)
        .execute()
    )

    rows = []

    for name in sorted(VALID_DIMENSIONS):
        item = dimensions[name]
        present = bool(item["present"])

        rows.append(
            {
                "lens_classification_id": classification_id,
                "dimension": name,
                "present": present,
                "direction": item["direction"] if present else None,
                "degree": int(item["degree"]) if present else 0,
                "confidence": confidence,
                "reasoning": item.get("reasoning") or None,
            }
        )

    client.table("lens_dimensions").insert(rows).execute()


def apply_correction(
    client: Client,
    current: dict[str, Any],
    correction: dict[str, Any],
) -> dict[str, Any]:
    fields = dict(correction["fields"])
    degree = int(fields["empowerment_degree"])
    fields["unit_score"] = unit_score(
        fields["empowerment_status"],
        degree,
    )

    countries = [
        str(code).upper()
        for code in fields.pop("country_iso3s", [])
    ]

    fields["primary_country_iso3"] = (
        countries[0] if countries else None
    )
    fields["country_iso3s"] = countries
    fields["requires_review"] = False
    fields["review_reason"] = None
    fields["audit_selected"] = False
    fields["audit_reason"] = None

    (
        client.table("lens_classifications")
        .update(fields)
        .eq(
            "lens_classification_id",
            current["lens_classification_id"],
        )
        .execute()
    )

    update_dimensions(
        client,
        str(current["lens_classification_id"]),
        correction["dimensions"],
        current.get("confidence"),
    )

    if current["lens"] == "event" and current.get("event_id"):
        (
            client.table("events")
            .update(
                {
                    "primary_country_iso3": (
                        countries[0] if countries else None
                    ),
                    "additional_country_iso3": countries[1:],
                    "updated_at": now_iso(),
                }
            )
            .eq("event_id", current["event_id"])
            .execute()
        )

    final_output = {
        **fields,
        "dimensions": correction["dimensions"],
        "human_correction_reason": correction["title"],
    }

    return final_output


def load_run_rows(
    client: Client,
    run_id: str,
) -> list[dict[str, Any]]:
    response = (
        client.table("lens_classifications")
        .select(
            "lens_classification_id,lens,article_id,event_id,"
            "ai_relevant,empowerment_status,empowerment_degree,unit_score,"
            "narrative_frame,distribution_breadth,dominant_dimension,"
            "ai_authority_shift,topic,geographic_scope,"
            "primary_country_iso3,country_iso3s,confidence,"
            "requires_review,audit_selected"
        )
        .eq("classification_run_id", run_id)
        .execute()
    )

    rows = getattr(response, "data", None) or []

    ids = [str(row["lens_classification_id"]) for row in rows]
    dimensions: dict[str, dict[str, Any]] = defaultdict(dict)

    for start in range(0, len(ids), 150):
        dim_response = (
            client.table("lens_dimensions")
            .select(
                "lens_classification_id,dimension,present,direction,degree"
            )
            .in_("lens_classification_id", ids[start:start + 150])
            .execute()
        )

        for item in getattr(dim_response, "data", None) or []:
            dimensions[str(item["lens_classification_id"])][
                str(item["dimension"])
            ] = {
                "present": bool(item["present"]),
                "direction": item.get("direction"),
                "degree": int(item.get("degree") or 0),
            }

    for row in rows:
        cid = str(row["lens_classification_id"])
        row["dimensions"] = {
            name: dimensions[cid].get(
                name,
                {
                    "present": False,
                    "direction": None,
                    "degree": 0,
                },
            )
            for name in sorted(VALID_DIMENSIONS)
        }

    return rows


def share_dict(values: list[str], allowed: list[str]) -> dict[str, float]:
    if not values:
        return {key: 0.0 for key in allowed}

    counts = Counter(values)
    total = len(values)

    return {
        key: round(counts.get(key, 0) / total, 4)
        for key in allowed
    }


def dimension_share(rows: list[dict[str, Any]]) -> dict[str, float]:
    ai_rows = [row for row in rows if row["ai_relevant"]]

    if not ai_rows:
        return {name: 0.0 for name in sorted(VALID_DIMENSIONS)}

    return {
        name: round(
            sum(
                1
                for row in ai_rows
                if row["dimensions"][name]["present"]
            )
            / len(ai_rows),
            4,
        )
        for name in sorted(VALID_DIMENSIONS)
    }


def summarize(
    rows: list[dict[str, Any]],
    lens: str,
    country: str | None = None,
) -> dict[str, Any]:
    scoped = [
        row
        for row in rows
        if (
            row["lens"] == lens
            and (
                country is None
                or row.get("primary_country_iso3") == country
            )
        )
    ]

    ai_rows = [row for row in scoped if row["ai_relevant"]]
    scored = [
        row
        for row in ai_rows
        if row["unit_score"] is not None
    ]

    index_value = (
        round(
            sum(float(row["unit_score"]) for row in scored)
            / len(scored)
            * 100.0,
            4,
        )
        if scored
        else None
    )

    return {
        "lens": lens,
        "scope": "global" if country is None else "country",
        "country_iso3": country,
        "unit_count_total": len(scoped),
        "unit_count_ai_relevant": len(ai_rows),
        "unit_count_scored": len(scored),
        "review_required_count": sum(
            1 for row in scoped if row["requires_review"]
        ),
        "empowerment_index": index_value,
        "mean_confidence": None,
        "status_distribution": share_dict(
            [row["empowerment_status"] for row in ai_rows],
            [
                "expanding",
                "contracting",
                "mixed",
                "non_empowerment",
                "unclear",
            ],
        ),
        "narrative_distribution": share_dict(
            [row["narrative_frame"] for row in ai_rows],
            [
                "opportunity",
                "threat",
                "contested",
                "descriptive_neutral",
                "unclear",
            ],
        ),
        "breadth_distribution": share_dict(
            [row["distribution_breadth"] for row in ai_rows],
            ["broad", "targeted", "concentrated", "unclear"],
        ),
        "dimension_distribution": dimension_share(scoped),
        "signal_ready": bool(
            len(scored)
            >= (1 if country is None else MIN_COUNTRY_SIGNAL_N)
        ),
    }


def amp(cov: dict[str, Any], evt: dict[str, Any]) -> dict[str, Any]:
    cov_i = cov["empowerment_index"]
    evt_i = evt["empowerment_index"]

    gap = (
        round(float(cov_i) - float(evt_i), 4)
        if cov_i is not None and evt_i is not None
        else None
    )

    event_n = evt["unit_count_ai_relevant"]

    return {
        "scope": cov["scope"],
        "country_iso3": cov["country_iso3"],
        "coverage_index": cov_i,
        "event_index": evt_i,
        "directional_amplification_gap": gap,
        "coverage_unit_count": cov["unit_count_ai_relevant"],
        "event_unit_count": event_n,
        "coverage_event_ratio": (
            round(cov["unit_count_ai_relevant"] / event_n, 4)
            if event_n
            else None
        ),
        "coverage_narrative_distribution": cov[
            "narrative_distribution"
        ],
        "event_narrative_distribution": evt[
            "narrative_distribution"
        ],
        "signal_ready": bool(
            cov["signal_ready"] and evt["signal_ready"]
        ),
    }


def rewrite_snapshots(
    client: Client,
    run_id: str,
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    (
        client.table("lens_index_snapshots")
        .delete()
        .eq("classification_run_id", run_id)
        .execute()
    )
    (
        client.table("amplification_snapshots")
        .delete()
        .eq("classification_run_id", run_id)
        .execute()
    )

    global_cov = summarize(rows, "coverage")
    global_evt = summarize(rows, "event")
    global_amp = amp(global_cov, global_evt)

    for summary in (global_cov, global_evt):
        (
            client.table("lens_index_snapshots")
            .insert(
                {
                    "classification_run_id": run_id,
                    **summary,
                }
            )
            .execute()
        )

    (
        client.table("amplification_snapshots")
        .insert(
            {
                "classification_run_id": run_id,
                **global_amp,
            }
        )
        .execute()
    )

    countries = sorted(
        {
            str(row["primary_country_iso3"])
            for row in rows
            if row.get("primary_country_iso3")
        }
    )

    country_rows = []

    for country in countries:
        cov = summarize(rows, "coverage", country)
        evt = summarize(rows, "event", country)
        amplification = amp(cov, evt)

        for summary in (cov, evt):
            (
                client.table("lens_index_snapshots")
                .insert(
                    {
                        "classification_run_id": run_id,
                        **summary,
                    }
                )
                .execute()
            )

        (
            client.table("amplification_snapshots")
            .insert(
                {
                    "classification_run_id": run_id,
                    **amplification,
                }
            )
            .execute()
        )

        country_rows.append(
            {
                "country_iso3": country,
                "coverage": cov,
                "event": evt,
                "amplification": amplification,
            }
        )

    return global_cov, global_evt, global_amp, country_rows


def main() -> int:
    client: Client = create_client(
        required_env("SUPABASE_URL"),
        required_env("SUPABASE_SECRET_KEY"),
    )

    audit_rows = load_audit()
    manifest = load_manifest()
    corrections = manifest["corrections"]

    counts = Counter(
        str(row.get("review_status") or "")
        for row in audit_rows
    )

    expected_total = int(manifest["expected_audit_rows"])
    expected_accepted = int(manifest["expected_accepted"])
    expected_corrections = int(
        manifest["expected_needs_correction"]
    )

    if len(audit_rows) != expected_total:
        raise AuditError(
            f"Expected {expected_total} audit rows, found {len(audit_rows)}."
        )

    if counts["accepted"] != expected_accepted:
        raise AuditError(
            f"Expected {expected_accepted} accepted rows, "
            f"found {counts['accepted']}."
        )

    if counts["needs_correction"] != expected_corrections:
        raise AuditError(
            f"Expected {expected_corrections} correction rows, "
            f"found {counts['needs_correction']}."
        )

    run_ids = set()
    corrected = 0
    accepted = 0

    for audit_row in audit_rows:
        cid = str(audit_row["lens_classification_id"])
        status = str(audit_row["review_status"])

        current = load_classification(client, cid)
        run_ids.add(str(current["classification_run_id"]))

        if status == "accepted":
            (
                client.table("lens_classifications")
                .update(
                    {
                        "requires_review": False,
                        "review_reason": None,
                        "audit_selected": False,
                        "audit_reason": None,
                    }
                )
                .eq("lens_classification_id", cid)
                .execute()
            )

            upsert_review(
                client,
                classification_id=cid,
                review_status="accepted",
                notes="Human audit accepted the substantive classification.",
                final_output=None,
            )
            accepted += 1

        elif status == "needs_correction":
            correction = corrections.get(cid)

            if not correction:
                raise AuditError(
                    f"No correction manifest entry for {cid}."
                )

            final_output = apply_correction(
                client,
                current,
                correction,
            )

            upsert_review(
                client,
                classification_id=cid,
                review_status="needs_correction",
                notes=(
                    "Human governance audit corrected the substantive "
                    "classification according to Stage 7C audit v1."
                ),
                final_output=final_output,
            )
            corrected += 1

        else:
            raise AuditError(
                f"Unsupported review status {status!r} for {cid}."
            )

    if len(run_ids) != 1:
        raise AuditError(
            f"Audit rows span multiple classification runs: {sorted(run_ids)}"
        )

    run_id = next(iter(run_ids))
    rows = load_run_rows(client, run_id)

    global_cov, global_evt, global_amp, country_rows = (
        rewrite_snapshots(client, run_id, rows)
    )

    (
        client.table("classification_runs")
        .update(
            {
                "review_required_count": sum(
                    1 for row in rows if row["requires_review"]
                ),
            }
        )
        .eq("classification_run_id", run_id)
        .execute()
    )

    audit_summary = {
        "audit_version": manifest["version"],
        "classification_run_id": run_id,
        "completed_at": now_iso(),
        "reviewer": REVIEWER,
        "sample_size": len(audit_rows),
        "accepted_count": accepted,
        "corrected_count": corrected,
        "selection_note": manifest["audit_design"],
        "representativeness_note": (
            "This stratified audit is a governance and error-discovery sample. "
            "The correction share must not be interpreted as a population-level "
            "model accuracy estimate."
        ),
    }

    REVIEW_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_OUTPUT.write_text(
        json.dumps(
            {
                "meta": {
                    "stage": "7C.2",
                    "classification_run_id": run_id,
                    "audit_complete": True,
                    "audit_summary": audit_summary,
                    "review_queue_count": 0,
                    "release_status": "human_audited_pilot",
                },
                "global": {
                    "coverage": global_cov,
                    "event": global_evt,
                    "amplification": global_amp,
                },
                "review_queue": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    PUBLIC_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_OUTPUT.write_text(
        json.dumps(
            {
                "meta": {
                    "stage": "7C.2",
                    "provisional": False,
                    "release_status": "human_audited_pilot",
                    "classification_run_id": run_id,
                    "audit": audit_summary,
                    "method": {
                        "coverage_lens": (
                            "one weight per observed AI-relevant article"
                        ),
                        "event_lens": (
                            "one weight per unique active AI event"
                        ),
                        "empowerment_index": (
                            "mean deterministic unit score x 100"
                        ),
                        "directional_amplification_gap": (
                            "Coverage Empowerment Index - "
                            "Event Empowerment Index"
                        ),
                        "confidence_weighting": False,
                    },
                },
                "global": {
                    "coverage": global_cov,
                    "event": global_evt,
                    "amplification": global_amp,
                },
                "countries": country_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    METHODOLOGY_OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    METHODOLOGY_OUTPUT.write_text(
        json.dumps(
            {
                "methodology_version": "public_v1.0",
                "release_status": "human_audited_pilot",
                "last_updated": now_iso(),
                "classification_run_id": run_id,
                "public_disclosure": {
                    "published": [
                        "data-source categories and update cadence",
                        "Coverage Lens and Event Lens unit definitions",
                        "empowerment-status and degree definitions",
                        "four parallel empowerment dimensions",
                        "index and Amplification Gap formulae",
                        "model families and versioned run identifiers",
                        "unit counts, exclusions and human-audit summary",
                        "known limitations and methodology changelog"
                    ],
                    "kept_private": [
                        "full production prompts and prompt-routing logic",
                        "exact merge/review thresholds",
                        "source code and orchestration implementation",
                        "private validation examples and anti-gaming rules",
                        "database credentials and internal schema operations"
                    ],
                    "principle": (
                        "The Observatory is auditable through versioned inputs, "
                        "definitions, formulas, run provenance, aggregate outputs "
                        "and human-audit records; it is not fully open-sourced."
                    ),
                },
                "method": {
                    "coverage_lens": (
                        "Each observed article receives one weight. "
                        "Repetition intentionally captures coverage volume."
                    ),
                    "event_lens": (
                        "Each resolved real-world development receives one weight."
                    ),
                    "empowerment_statuses": [
                        "expanding",
                        "contracting",
                        "mixed",
                        "non_empowerment",
                        "unclear"
                    ],
                    "dimensions_parallel": [
                        "operational",
                        "creative",
                        "agentic",
                        "normative"
                    ],
                    "residual_rule": (
                        "Non-empowerment is the residual; operational is substantive."
                    ),
                    "unit_score": {
                        "expanding": "+degree/3",
                        "contracting": "-degree/3",
                        "mixed": "0",
                        "non_empowerment": "0",
                        "unclear": "excluded"
                    },
                    "lens_index": "arithmetic mean of unit scores x 100",
                    "amplification_gap": (
                        "Coverage Empowerment Index - Event Empowerment Index"
                    ),
                    "confidence_policy": (
                        "Model self-confidence is diagnostic only and is not "
                        "used as an index weight."
                    ),
                },
                "audit": audit_summary,
                "current_signal": {
                    "coverage_empowerment_index": global_cov[
                        "empowerment_index"
                    ],
                    "event_empowerment_index": global_evt[
                        "empowerment_index"
                    ],
                    "directional_amplification_gap": global_amp[
                        "directional_amplification_gap"
                    ],
                    "coverage_event_ratio": global_amp[
                        "coverage_event_ratio"
                    ],
                },
                "limitations": [
                    "Pilot data are headline- and snippet-led.",
                    "The present country sample is not a population-weighted world estimate.",
                    "Automated weekly releases are provisional until a governance audit is completed.",
                    "Coverage volume measures attention, not objective event importance.",
                    "The public methodology supports scrutiny without disclosing proprietary implementation details."
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "classification_run_id": run_id,
                "audit_rows": len(audit_rows),
                "accepted": accepted,
                "corrected": corrected,
                "coverage_index": global_cov["empowerment_index"],
                "event_index": global_evt["empowerment_index"],
                "amplification_gap": global_amp[
                    "directional_amplification_gap"
                ],
                "coverage_event_ratio": global_amp[
                    "coverage_event_ratio"
                ],
                "release_status": "human_audited_pilot",
            },
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as exc:
        print(f"Applying Stage 7C audit failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
