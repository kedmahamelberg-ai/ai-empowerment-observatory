#!/usr/bin/env python3
"""Stage 7C.5 full-body-only post-processing without rerunning Qwen.

Why:
The substantive Stage 7C classifications are stored before this step. This
script keeps their evidence boundary intact while repairing normal QA output:
1. Qwen's self-reported numeric confidence was not calibrated and frequently 0.
2. auxiliary normalization warnings (especially geography) were treated as
   core classification failures.

This script:
- keeps the existing substantive classifications and unit scores;
- does NOT call any LLM;
- ignores model self-confidence as a review trigger;
- ignores auxiliary geography normalization as a review trigger;
- reserves review for core contradictions / unclear empowerment status;
- creates a small stratified human audit sample;
- recomputes Coverage/Event indices and Amplification snapshots;
- rebuilds the public/review JSON outputs.

The old run remains versioned provenance.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from supabase import Client, create_client

from translation_policy import SUPPORTED_TRANSLATION_PROFILES, preferred_translation_rows

ROOT = Path(__file__).resolve().parents[1]

REVIEW_OUTPUT = ROOT / "review" / "classification" / "latest.json"
PUBLIC_OUTPUT = ROOT / "data" / "lenses" / "latest.json"

POSTPROCESS_VERSION = "7C.5a_full_body_required"
TARGET_CLASSIFIER_VERSION = "7C.5_full_body_required"
EVENT_METHOD = "article_to_event_v1"

AUDIT_TARGET = 12
MULTI_EVENT_AUDIT_MAX = 5
MIN_COUNTRY_SIGNAL_N = 3

VALID_DIMENSIONS = {
    "operational",
    "creative",
    "agentic",
    "normative",
}


class RepairError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise RepairError(f"{name} is missing.")
    return value


def first_row(response: Any, context: str) -> dict[str, Any]:
    data = getattr(response, "data", None)

    if isinstance(data, list) and data:
        return data[0]

    if isinstance(data, dict) and data:
        return data

    raise RepairError(f"No row while {context}.")


def latest_stage7c_run(client: Client) -> dict[str, Any]:
    response = (
        client.table("classification_runs")
        .select(
            "classification_run_id,collection_run_id,run_key,"
            "started_at,completed_at,status,classifier_version,"
            "attempted_count,classified_count"
        )
        .eq("status", "success")
        .eq("classifier_version", TARGET_CLASSIFIER_VERSION)
        .order("completed_at", desc=True)
        .limit(1)
        .execute()
    )

    return first_row(
        response,
        f"loading latest successful {TARGET_CLASSIFIER_VERSION} run",
    )


def load_classifications(
    client: Client,
    classification_run_id: str,
) -> list[dict[str, Any]]:
    response = (
        client.table("lens_classifications")
        .select(
            "lens_classification_id,classification_run_id,lens,"
            "article_id,event_id,derived_from_lens_classification_id,"
            "ai_relevant,empowerment_status,empowerment_degree,unit_score,"
            "narrative_frame,distribution_breadth,dominant_dimension,"
            "ai_authority_shift,topic,geographic_scope,"
            "primary_country_iso3,country_iso3s,content_basis,"
            "confidence,reasoning,requires_review,review_reason,"
            "audit_selected,audit_reason,raw_output,created_at"
        )
        .eq("classification_run_id", classification_run_id)
        .execute()
    )

    rows = getattr(response, "data", None) or []

    if not rows:
        raise RepairError(
            "The latest successful full-body-required Stage 7C run has no lens classifications."
        )

    return rows


def load_dimensions(
    client: Client,
    classification_ids: list[str],
) -> dict[str, dict[str, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []

    for start in range(0, len(classification_ids), 150):
        response = (
            client.table("lens_dimensions")
            .select(
                "lens_classification_id,dimension,present,direction,"
                "degree,confidence,reasoning"
            )
            .in_(
                "lens_classification_id",
                classification_ids[start:start + 150],
            )
            .execute()
        )

        rows.extend(getattr(response, "data", None) or [])

    result: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    for row in rows:
        cid = str(row["lens_classification_id"])
        dimension = str(row["dimension"])

        result[cid][dimension] = {
            "present": bool(row["present"]),
            "direction": row.get("direction"),
            "degree": int(row.get("degree") or 0),
            "confidence": row.get("confidence"),
            "reasoning": str(row.get("reasoning") or ""),
        }

    for cid in classification_ids:
        for dimension in VALID_DIMENSIONS:
            result[cid].setdefault(
                dimension,
                {
                    "present": False,
                    "direction": None,
                    "degree": 0,
                    "confidence": None,
                    "reasoning": "",
                },
            )

    return result


def apply_non_empowerment_residual(
    row: dict[str, Any],
) -> bool:
    """Apply the agreed codebook residual rule deterministically.

    `unclear` is reserved for evidence suggesting an empowerment mechanism
    whose direction cannot be determined.

    When the classifier itself found:
    - no present empowerment dimension,
    - no dominant dimension,
    - degree 0,
    - and status `unclear`,

    the supplied evidence supports no human-empowerment mechanism. Under the
    Observatory codebook this is `non_empowerment`, not an item requiring
    manual review.

    Returns True when a row is normalized.
    """

    raw_output = row.get("raw_output")
    if isinstance(raw_output, dict) and raw_output.get("classification_not_run"):
        # Missing full article text is an evidence-availability state.  It is
        # not evidence for a non-empowerment finding.
        return False

    if str(row.get("empowerment_status")) != "unclear":
        return False

    dimensions = row.get("dimensions") or {}

    if any(
        bool(value.get("present"))
        for value in dimensions.values()
        if isinstance(value, dict)
    ):
        return False

    if row.get("dominant_dimension"):
        return False

    try:
        degree = int(row.get("empowerment_degree") or 0)
    except Exception:
        degree = 0

    if degree != 0:
        return False

    row["empowerment_status"] = "non_empowerment"
    row["empowerment_degree"] = 0
    row["unit_score"] = 0.0
    row["dominant_dimension"] = None

    note = (
        "Deterministic codebook normalization: no supported human "
        "empowerment dimension was identified, so the residual category is "
        "non-empowerment rather than unclear."
    )

    reasoning = str(row.get("reasoning") or "").strip()

    if note not in reasoning:
        row["reasoning"] = (
            f"{reasoning} {note}".strip()
        )

    row["_residual_normalized"] = True

    return True


def core_review_reasons(row: dict[str, Any]) -> list[str]:
    """Only CORE contradictions create model review.

    Confidence and geography are deliberately excluded:
    - Qwen self-confidence was empirically uncalibrated in this run.
    - auxiliary geography ambiguity must not force manual review.

    The agreed non-empowerment residual rule is applied before this function.
    Therefore a remaining `unclear` status should represent genuine core
    ambiguity rather than merely absent evidence of empowerment.
    """

    raw_output = row.get("raw_output")
    if isinstance(raw_output, dict) and raw_output.get("classification_not_run"):
        return ["full article body unavailable; model classification not run"]

    reasons: list[str] = []

    status = str(row["empowerment_status"])
    degree = int(row["empowerment_degree"])
    dimensions = row["dimensions"]
    dominant = row.get("dominant_dimension")

    if status == "unclear":
        reasons.append("empowerment status unclear")

    if status in {"expanding", "contracting", "mixed"} and degree == 0:
        reasons.append("directional empowerment has degree 0")

    if status in {"non_empowerment", "unclear"} and degree != 0:
        reasons.append("non-scored status has non-zero degree")

    if status == "non_empowerment":
        present = [
            name
            for name, value in dimensions.items()
            if value["present"]
        ]
        if present:
            reasons.append(
                "non-empowerment classification contains present dimensions"
            )

    if dominant:
        if dominant not in dimensions:
            reasons.append("dominant dimension is absent from dimension data")
        elif not dimensions[dominant]["present"]:
            reasons.append("dominant dimension is not marked present")

    if row["unit_score"] is None and status != "unclear":
        reasons.append("scored status has null unit score")

    if row["unit_score"] is not None and status == "unclear":
        reasons.append("unclear status has a numeric unit score")

    if not bool(row["ai_relevant"]) and status not in {
        "non_empowerment",
        "unclear",
    }:
        reasons.append(
            "AI-irrelevant unit has a directional empowerment classification"
        )

    return reasons


def stable_order(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_event_member_counts(
    client: Client,
    event_ids: list[str],
) -> dict[str, int]:
    counts: Counter[str] = Counter()

    for start in range(0, len(event_ids), 100):
        response = (
            client.table("event_articles")
            .select("event_id,article_id")
            .in_("event_id", event_ids[start:start + 100])
            .execute()
        )

        for row in getattr(response, "data", None) or []:
            counts[str(row["event_id"])] += 1

    return dict(counts)


def select_audit(
    rows: list[dict[str, Any]],
    event_member_counts: dict[str, int],
) -> set[str]:
    selected: set[str] = set()

    coverage = [
        row
        for row in rows
        if (
            row["lens"] == "coverage"
            and row["ai_relevant"]
            and not row["requires_review"]
        )
    ]

    # Two examples from each substantive status where available.
    for status in (
        "expanding",
        "contracting",
        "mixed",
        "non_empowerment",
    ):
        candidates = sorted(
            [
                row
                for row in coverage
                if row["empowerment_status"] == status
            ],
            key=lambda row: stable_order(
                str(row["lens_classification_id"])
            ),
        )

        for row in candidates[:2]:
            selected.add(str(row["lens_classification_id"]))

    # Add dimension diversity.
    for dimension in (
        "operational",
        "creative",
        "agentic",
        "normative",
    ):
        if len(selected) >= AUDIT_TARGET:
            break

        candidates = sorted(
            [
                row
                for row in coverage
                if (
                    str(row["lens_classification_id"]) not in selected
                    and row["dimensions"][dimension]["present"]
                )
            ],
            key=lambda row: stable_order(
                str(row["lens_classification_id"])
            ),
        )

        if candidates:
            selected.add(
                str(candidates[0]["lens_classification_id"])
            )

    # Fill to target deterministically.
    for row in sorted(
        coverage,
        key=lambda item: stable_order(
            str(item["lens_classification_id"])
        ),
    ):
        if len(selected) >= AUDIT_TARGET:
            break

        selected.add(str(row["lens_classification_id"]))

    # Multi-source events are especially important to the Event Lens.
    multi_events = sorted(
        [
            row
            for row in rows
            if (
                row["lens"] == "event"
                and event_member_counts.get(
                    str(row.get("event_id") or ""),
                    0,
                ) > 1
            )
        ],
        key=lambda row: stable_order(
            str(row["lens_classification_id"])
        ),
    )

    for row in multi_events[:MULTI_EVENT_AUDIT_MAX]:
        selected.add(str(row["lens_classification_id"]))

    return selected


def share_dict(
    values: list[str],
    allowed: list[str],
) -> dict[str, float]:
    if not values:
        return {key: 0.0 for key in allowed}

    counts = Counter(values)
    total = len(values)

    return {
        key: round(counts.get(key, 0) / total, 4)
        for key in allowed
    }


def dimension_share(
    rows: list[dict[str, Any]],
) -> dict[str, float]:
    ai_rows = [
        row
        for row in rows
        if row["ai_relevant"]
    ]

    if not ai_rows:
        return {
            dimension: 0.0
            for dimension in sorted(VALID_DIMENSIONS)
        }

    return {
        dimension: round(
            sum(
                1
                for row in ai_rows
                if row["dimensions"][dimension]["present"]
            )
            / len(ai_rows),
            4,
        )
        for dimension in sorted(VALID_DIMENSIONS)
    }


def summarize_lens(
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

    ai_rows = [
        row
        for row in scoped
        if row["ai_relevant"]
    ]

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
            1
            for row in scoped
            if row["requires_review"]
        ),
        "empowerment_index": index_value,

        # The model's numeric self-rating is retained per unit in Supabase,
        # but is not aggregated as a trustworthy calibration statistic.
        "mean_confidence": None,

        "status_distribution": share_dict(
            [
                row["empowerment_status"]
                for row in ai_rows
            ],
            [
                "expanding",
                "contracting",
                "mixed",
                "non_empowerment",
                "unclear",
            ],
        ),
        "narrative_distribution": share_dict(
            [
                row["narrative_frame"]
                for row in ai_rows
            ],
            [
                "opportunity",
                "threat",
                "contested",
                "descriptive_neutral",
                "unclear",
            ],
        ),
        "breadth_distribution": share_dict(
            [
                row["distribution_breadth"]
                for row in ai_rows
            ],
            [
                "broad",
                "targeted",
                "concentrated",
                "unclear",
            ],
        ),
        "dimension_distribution": dimension_share(scoped),
        "signal_ready": bool(
            len(scored)
            >= (1 if country is None else MIN_COUNTRY_SIGNAL_N)
        ),
    }


def amplification(
    coverage: dict[str, Any],
    event: dict[str, Any],
) -> dict[str, Any]:
    cov_index = coverage["empowerment_index"]
    evt_index = event["empowerment_index"]

    gap = (
        round(float(cov_index) - float(evt_index), 4)
        if (
            cov_index is not None
            and evt_index is not None
        )
        else None
    )

    event_n = event["unit_count_ai_relevant"]

    return {
        "scope": coverage["scope"],
        "country_iso3": coverage["country_iso3"],
        "coverage_index": cov_index,
        "event_index": evt_index,
        "directional_amplification_gap": gap,
        "coverage_unit_count": coverage["unit_count_ai_relevant"],
        "event_unit_count": event_n,
        "coverage_event_ratio": (
            round(
                coverage["unit_count_ai_relevant"] / event_n,
                4,
            )
            if event_n
            else None
        ),
        "coverage_narrative_distribution": coverage[
            "narrative_distribution"
        ],
        "event_narrative_distribution": event[
            "narrative_distribution"
        ],
        "signal_ready": bool(
            coverage["signal_ready"]
            and event["signal_ready"]
        ),
    }


def load_articles(
    client: Client,
    article_ids: list[str],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    article_rows: list[dict[str, Any]] = []

    for start in range(0, len(article_ids), 150):
        response = (
            client.table("articles")
            .select(
                "article_id,headline,publisher,canonical_url,"
                "published_at,first_seen_at,source_metadata"
            )
            .in_("article_id", article_ids[start:start + 150])
            .execute()
        )

        article_rows.extend(getattr(response, "data", None) or [])

    translations: list[dict[str, Any]] = []

    for start in range(0, len(article_ids), 150):
        response = (
            client.table("article_translations")
            .select(
                "article_id,translated_headline,"
                "source_language_iso2,translation_profile,created_at"
            )
            .in_("translation_profile", list(SUPPORTED_TRANSLATION_PROFILES))
            .in_("article_id", article_ids[start:start + 150])
            .order("created_at", desc=True)
            .execute()
        )

        translations.extend(getattr(response, "data", None) or [])

    newest = preferred_translation_rows(translations)

    return (
        {
            str(row["article_id"]): row
            for row in article_rows
        },
        newest,
    )


def article_display(
    article_id: str,
    article_map: dict[str, dict[str, Any]],
    translation_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    article = article_map.get(article_id) or {}
    translation = translation_map.get(article_id) or {}

    original = str(article.get("headline") or "")
    english = str(
        translation.get("translated_headline")
        or original
    )

    metadata = article.get("source_metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    snippet = ""

    for key in (
        "snippet",
        "description",
        "summary",
        "source_snippet",
    ):
        value = metadata.get(key)
        if value and str(value).strip():
            snippet = str(value).strip()
            break

    date = str(
        article.get("published_at")
        or article.get("first_seen_at")
        or ""
    )

    evidence = (
        "Lens unit: one news article\n"
        f"Publisher: {article.get('publisher') or 'Unknown source'}\n"
        f"Date: {date}\n"
        f"Original headline: {original}\n"
        f"English normalization: {english}\n"
        f"Snippet: {snippet}\n"
        f"Evidence basis available: "
        f"{'headline_and_snippet' if snippet else 'headline_only'}"
    )

    return {
        "title": english,
        "publisher": str(
            article.get("publisher")
            or "Unknown source"
        ),
        "date": date,
        "evidence": evidence,
    }


def load_events(
    client: Client,
    event_ids: list[str],
) -> dict[str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for start in range(0, len(event_ids), 100):
        response = (
            client.table("events")
            .select(
                "event_id,event_title,event_summary,event_date,"
                "first_seen_at,last_seen_at"
            )
            .in_("event_id", event_ids[start:start + 100])
            .execute()
        )

        rows.extend(getattr(response, "data", None) or [])

    return {
        str(row["event_id"]): row
        for row in rows
    }


def load_event_members(
    client: Client,
    event_ids: list[str],
) -> dict[str, list[str]]:
    members: dict[str, list[str]] = defaultdict(list)

    for start in range(0, len(event_ids), 100):
        response = (
            client.table("event_articles")
            .select("event_id,article_id")
            .in_("event_id", event_ids[start:start + 100])
            .execute()
        )

        for row in getattr(response, "data", None) or []:
            members[str(row["event_id"])].append(
                str(row["article_id"])
            )

    return members


def event_display(
    event_id: str,
    event_map: dict[str, dict[str, Any]],
    event_members: dict[str, list[str]],
    article_map: dict[str, dict[str, Any]],
    translation_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    event = event_map.get(event_id) or {}
    member_ids = event_members.get(event_id) or []

    source_blocks = []
    publishers = []

    for index, aid in enumerate(member_ids, start=1):
        display = article_display(
            aid,
            article_map,
            translation_map,
        )

        publishers.append(display["publisher"])

        source_blocks.append(
            f"Source {index}\n"
            f"Publisher: {display['publisher']}\n"
            f"Date: {display['date']}\n"
            f"Headline: {display['title']}"
        )

    evidence = (
        "Lens unit: one unique real-world event\n"
        f"Canonical event title: {event.get('event_title') or ''}\n"
        f"Event date: {event.get('event_date') or ''}\n"
        f"Number of source articles: {len(member_ids)}\n\n"
        + "\n\n".join(source_blocks)
    )

    return {
        "title": str(event.get("event_title") or ""),
        "publisher": ", ".join(sorted(set(publishers))),
        "date": str(event.get("event_date") or ""),
        "evidence": evidence,
    }


def review_card(
    row: dict[str, Any],
    display: dict[str, Any],
) -> dict[str, Any]:
    return {
        "lens_classification_id": str(
            row["lens_classification_id"]
        ),
        "lens": row["lens"],
        "unit_id": str(
            row.get("article_id")
            or row.get("event_id")
        ),
        "title": display["title"],
        "publisher_or_sources": display["publisher"],
        "date": display["date"],
        "evidence": display["evidence"],
        "ai_relevant": bool(row["ai_relevant"]),
        "empowerment_status": row["empowerment_status"],
        "empowerment_degree": row["empowerment_degree"],
        "unit_score": row["unit_score"],
        "narrative_frame": row["narrative_frame"],
        "distribution_breadth": row["distribution_breadth"],
        "dominant_dimension": row["dominant_dimension"],
        "dimensions": row["dimensions"],
        "ai_authority_shift": row["ai_authority_shift"],
        "topic": row["topic"],
        "geographic_scope": row["geographic_scope"],
        "country_iso3s": row["country_iso3s"],
        "confidence": row["confidence"],
        "reasoning": row["reasoning"],
        "requires_review": bool(row["requires_review"]),
        "review_reason": str(row.get("review_reason") or ""),
        "audit_selected": bool(row.get("audit_selected")),
        "audit_reason": str(row.get("audit_reason") or ""),
        "confidence_note": (
            "Model self-rating is not calibrated and is not used "
            "for index weighting or automatic review."
        ),
    }


def main() -> int:
    client: Client = create_client(
        required_env("SUPABASE_URL"),
        required_env("SUPABASE_SECRET_KEY"),
    )

    run = latest_stage7c_run(client)
    run_id = str(run["classification_run_id"])

    rows = load_classifications(client, run_id)

    dimensions = load_dimensions(
        client,
        [
            str(row["lens_classification_id"])
            for row in rows
        ],
    )

    residual_normalized_count = 0

    for row in rows:
        cid = str(row["lens_classification_id"])
        row["dimensions"] = dimensions[cid]

        if apply_non_empowerment_residual(row):
            residual_normalized_count += 1

        reasons = core_review_reasons(row)

        row["requires_review"] = bool(reasons)
        row["review_reason"] = "; ".join(reasons)

    # Persist corrected substantive residual rule and review policy.
    for row in rows:
        (
            client.table("lens_classifications")
            .update(
                {
                    "empowerment_status": row["empowerment_status"],
                    "empowerment_degree": row["empowerment_degree"],
                    "unit_score": row["unit_score"],
                    "dominant_dimension": row["dominant_dimension"],
                    "reasoning": row["reasoning"],
                    "requires_review": row["requires_review"],
                    "review_reason": (
                        row["review_reason"]
                        or None
                    ),
                    "audit_selected": False,
                    "audit_reason": None,
                }
            )
            .eq(
                "lens_classification_id",
                row["lens_classification_id"],
            )
            .execute()
        )

    event_ids = [
        str(row["event_id"])
        for row in rows
        if row["lens"] == "event"
    ]

    event_member_counts = load_event_member_counts(
        client,
        event_ids,
    )

    audit_ids = select_audit(
        rows,
        event_member_counts,
    )

    for row in rows:
        cid = str(row["lens_classification_id"])

        if cid in audit_ids:
            row["audit_selected"] = True
            row["audit_reason"] = (
                "multi-source Event Lens audit"
                if (
                    row["lens"] == "event"
                    and event_member_counts.get(
                        str(row.get("event_id") or ""),
                        0,
                    ) > 1
                )
                else "stratified quality audit"
            )

            (
                client.table("lens_classifications")
                .update(
                    {
                        "audit_selected": True,
                        "audit_reason": row["audit_reason"],
                    }
                )
                .eq("lens_classification_id", cid)
                .execute()
            )
        else:
            row["audit_selected"] = False
            row["audit_reason"] = ""

    # Replace stale snapshots for this exact run.
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

    global_coverage = summarize_lens(
        rows,
        "coverage",
    )

    global_event = summarize_lens(
        rows,
        "event",
    )

    global_amp = amplification(
        global_coverage,
        global_event,
    )

    for summary in (
        global_coverage,
        global_event,
    ):
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
        cov = summarize_lens(
            rows,
            "coverage",
            country,
        )

        evt = summarize_lens(
            rows,
            "event",
            country,
        )

        amp = amplification(cov, evt)

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
                    **amp,
                }
            )
            .execute()
        )

        country_rows.append(
            {
                "country_iso3": country,
                "coverage": cov,
                "event": evt,
                "amplification": amp,
            }
        )

    model_review_count = sum(
        1
        for row in rows
        if row["requires_review"]
    )

    (
        client.table("classification_runs")
        .update(
            {
                "review_required_count": model_review_count,
            }
        )
        .eq("classification_run_id", run_id)
        .execute()
    )

    coverage_article_ids = [
        str(row["article_id"])
        for row in rows
        if row["lens"] == "coverage"
    ]

    event_ids = [
        str(row["event_id"])
        for row in rows
        if row["lens"] == "event"
    ]

    event_members = load_event_members(
        client,
        event_ids,
    )

    all_article_ids = sorted(
        set(coverage_article_ids)
        | {
            aid
            for members in event_members.values()
            for aid in members
        }
    )

    article_map, translation_map = load_articles(
        client,
        all_article_ids,
    )

    event_map = load_events(
        client,
        event_ids,
    )

    review_queue = []

    for row in rows:
        if not (
            row["requires_review"]
            or row["audit_selected"]
        ):
            continue

        if row["lens"] == "coverage":
            display = article_display(
                str(row["article_id"]),
                article_map,
                translation_map,
            )
        else:
            display = event_display(
                str(row["event_id"]),
                event_map,
                event_members,
                article_map,
                translation_map,
            )

        review_queue.append(
            review_card(row, display)
        )

    multi_source_event_count = sum(
        1
        for event_id in event_ids
        if event_member_counts.get(event_id, 0) > 1
    )

    qwen_call_count = (
        len(coverage_article_ids)
        + multi_source_event_count
    )

    REVIEW_OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    REVIEW_OUTPUT.write_text(
        json.dumps(
            {
                "meta": {
                    "stage": POSTPROCESS_VERSION,
                    "classification_run_id": run_id,
                    "run_key": run["run_key"],
                    "source_classifier_version": run[
                        "classifier_version"
                    ],
                    "coverage_article_count": len(
                        coverage_article_ids
                    ),
                    "event_count": len(event_ids),
                    "multi_source_event_count": (
                        multi_source_event_count
                    ),
                    "qwen_call_count": qwen_call_count,
                    "model_review_required_count": (
                        model_review_count
                    ),
                    "residual_normalized_count": (
                        residual_normalized_count
                    ),
                    "audit_selected_count": len(audit_ids),
                    "review_queue_count": len(review_queue),
                    "confidence_policy": (
                        "Qwen numeric self-confidence from this run is not "
                        "calibrated. It is retained as a diagnostic only and "
                        "is not used for index weighting or review selection."
                    ),
                    "postprocess_policy": (
                        "No supported empowerment dimension maps to the "
                        "non-empowerment residual; core review only; auxiliary "
                        "geography normalization does not create manual review."
                    ),
                },
                "global": {
                    "coverage": global_coverage,
                    "event": global_event,
                    "amplification": global_amp,
                },
                "review_queue": review_queue,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    PUBLIC_OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    PUBLIC_OUTPUT.write_text(
        json.dumps(
            {
                "meta": {
                    "stage": POSTPROCESS_VERSION,
                    "provisional": True,
                    "classification_run_id": run_id,
                    "run_key": run["run_key"],
                    "confidence_calibrated": False,
                    "residual_normalized_count": (
                        residual_normalized_count
                    ),
                    "confidence_note": (
                        "Model self-confidence is excluded from index "
                        "weighting and quality-review selection."
                    ),
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
                        "country_signal_min_n": MIN_COUNTRY_SIGNAL_N,
                    },
                },
                "global": {
                    "coverage": global_coverage,
                    "event": global_event,
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

    print(
        json.dumps(
            {
                "classification_run_id": run_id,
                "coverage_articles": len(coverage_article_ids),
                "unique_events": len(event_ids),
                "multi_source_events": multi_source_event_count,
                "qwen_calls_already_completed": qwen_call_count,
                "residual_normalized": residual_normalized_count,
                "core_model_review_required": model_review_count,
                "audit_selected": len(audit_ids),
                "review_queue": len(review_queue),
                "coverage_index": global_coverage[
                    "empowerment_index"
                ],
                "event_index": global_event[
                    "empowerment_index"
                ],
                "directional_amplification_gap": global_amp[
                    "directional_amplification_gap"
                ],
                "coverage_event_ratio": global_amp[
                    "coverage_event_ratio"
                ],
            },
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RepairError as exc:
        print(
            f"Stage 7C QA repair failed: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
