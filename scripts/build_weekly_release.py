#!/usr/bin/env python3
"""Build a period-consistent, immutable weekly AIEO release snapshot.

The script scopes the latest successful collection/classification run to one
completed Monday-Sunday period. All counts, evidence cards, sources and lens
statistics are generated from exactly the same article IDs and event IDs.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

from release_common import (
    AMSTERDAM,
    CURRENT_RELEASE,
    EVENT_METHOD,
    RELEASE_INDEX,
    ROOT,
    WEEKLY_DIR,
    Period,
    ReleaseError,
    calculate_index,
    chunks,
    date_for_article,
    governance_for,
    iso_week_id,
    iso_z,
    load_json,
    normalize_name,
    parse_date,
    previous_complete_week,
    release_summary,
    source_stratum,
    source_summary,
    stable_hash,
    supabase_admin,
    unique,
    utc_now,
    write_json,
)

BASELINE_DIR = ROOT / "data" / "releases" / "baselines"
RESURFACE_DAYS = 28.0
STAGE7C_CLASSIFIER_VERSION = "7C.5_full_body_required"


def latest_successful_classification(client) -> dict[str, Any]:
    response = (
        client.table("classification_runs")
        .select(
            "classification_run_id,collection_run_id,run_key,started_at,"
            "completed_at,status,classifier_version,codebook_version_id,"
            "model_version_id,attempted_count,classified_count,"
            "review_required_count"
        )
        .eq("status", "success")
        .eq("classifier_version", STAGE7C_CLASSIFIER_VERSION)
        .order("completed_at", desc=True)
        .limit(1)
        .execute()
    )
    data = getattr(response, "data", None) or []
    if not data:
        raise ReleaseError(
            "No successful full-body-required Stage 7C classification run exists."
        )
    return data[0]


def collection_row(client, run_id: str) -> dict[str, Any]:
    response = (
        client.table("collection_runs")
        .select("run_id,run_key,started_at,completed_at,status")
        .eq("run_id", run_id)
        .limit(1)
        .execute()
    )
    data = getattr(response, "data", None) or []
    if not data:
        raise ReleaseError(f"Collection run not found: {run_id}")
    return data[0]


def resolution_row(client, collection_run_id: str) -> dict[str, Any] | None:
    response = (
        client.table("event_resolution_runs")
        .select(
            "resolution_run_id,collection_run_id,run_key,started_at,completed_at,"
            "status,resolver_version,article_count,already_assigned_count,"
            "auto_merge_count,new_event_count,review_count,verifier_call_count"
        )
        .eq("collection_run_id", collection_run_id)
        .eq("status", "success")
        .order("completed_at", desc=True)
        .limit(1)
        .execute()
    )
    data = getattr(response, "data", None) or []
    return data[0] if data else None


def load_collection_observations(client, run_id: str) -> dict[str, dict[str, Any]]:
    response = (
        client.table("article_observations")
        .select(
            "article_id,search_rank,search_country_iso3,search_language,observed_at"
        )
        .eq("run_id", run_id)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    if not rows:
        raise ReleaseError("The classified collection has no article observations.")

    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        aid = str(row["article_id"])
        item = result.setdefault(
            aid,
            {
                "search_rank": 999999,
                "search_markets": set(),
                "search_languages": set(),
                "observed_at": [],
            },
        )
        if row.get("search_rank") is not None:
            item["search_rank"] = min(item["search_rank"], int(row["search_rank"]))
        if row.get("search_country_iso3"):
            item["search_markets"].add(str(row["search_country_iso3"]))
        if row.get("search_language"):
            item["search_languages"].add(str(row["search_language"]).lower())
        if row.get("observed_at"):
            item["observed_at"].append(str(row["observed_at"]))
    return result


def load_articles(client, article_ids: list[str]) -> dict[str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for batch in chunks(article_ids, 150):
        response = (
            client.table("articles")
            .select(
                "article_id,headline,publisher,canonical_url,published_at,"
                "first_seen_at,last_seen_at,source_metadata"
            )
            .in_("article_id", batch)
            .execute()
        )
        rows.extend(getattr(response, "data", None) or [])
    return {str(row["article_id"]): row for row in rows}


def load_translations(client, article_ids: list[str]) -> dict[str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for batch in chunks(article_ids, 150):
        response = (
            client.table("article_translations")
            .select(
                "article_id,source_language_iso2,translated_headline,"
                "requires_review,review_reason,created_at"
            )
            .eq("translation_profile", "validated_language_routing_v3")
            .in_("article_id", batch)
            .order("created_at", desc=True)
            .execute()
        )
        rows.extend(getattr(response, "data", None) or [])
    newest: dict[str, dict[str, Any]] = {}
    for row in rows:
        newest.setdefault(str(row["article_id"]), row)
    return newest


def load_event_links(client, article_ids: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for batch in chunks(article_ids, 150):
        response = (
            client.table("event_articles")
            .select("event_id,article_id,is_canonical_source,similarity_score")
            .in_("article_id", batch)
            .execute()
        )
        rows.extend(getattr(response, "data", None) or [])
    return rows


def load_events(client, event_ids: list[str]) -> dict[str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for batch in chunks(event_ids, 100):
        response = (
            client.table("events")
            .select(
                "event_id,event_title,event_summary,event_date,first_seen_at,"
                "last_seen_at,event_state,clustering_method,cluster_confidence,"
                "requires_cluster_review,cluster_review_reason,"
                "primary_country_iso3,additional_country_iso3,story_family_id,"
                "canonical_event_id,canonicalized_at,last_reconciled_at,registry_version"
            )
            .in_("event_id", batch)
            .eq("event_state", "active")
            .eq("clustering_method", EVENT_METHOD)
            .execute()
        )
        rows.extend(getattr(response, "data", None) or [])
    return {str(row["event_id"]): row for row in rows}


def load_classifications(
    client,
    classification_run_id: str,
    article_ids: list[str],
    event_ids: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    fields = (
        "lens_classification_id,lens,article_id,event_id,ai_relevant,"
        "empowerment_status,empowerment_degree,unit_score,narrative_frame,"
        "distribution_breadth,dominant_dimension,ai_authority_shift,topic,"
        "geographic_scope,primary_country_iso3,country_iso3s,content_basis,"
        "confidence,reasoning,requires_review,review_reason,"
        "audit_selected,audit_reason"
    )
    rows: list[dict[str, Any]] = []
    for batch in chunks(article_ids, 100):
        response = (
            client.table("lens_classifications")
            .select(fields)
            .eq("classification_run_id", classification_run_id)
            .eq("lens", "coverage")
            .in_("article_id", batch)
            .execute()
        )
        rows.extend(getattr(response, "data", None) or [])
    for batch in chunks(event_ids, 100):
        response = (
            client.table("lens_classifications")
            .select(fields)
            .eq("classification_run_id", classification_run_id)
            .eq("lens", "event")
            .in_("event_id", batch)
            .execute()
        )
        rows.extend(getattr(response, "data", None) or [])

    classification_ids = [str(row["lens_classification_id"]) for row in rows]
    dimensions: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for batch in chunks(classification_ids, 100):
        response = (
            client.table("lens_dimensions")
            .select(
                "lens_classification_id,dimension,present,direction,degree,"
                "confidence,reasoning"
            )
            .in_("lens_classification_id", batch)
            .execute()
        )
        for row in getattr(response, "data", None) or []:
            dimensions[str(row["lens_classification_id"])][str(row["dimension"])] = {
                "present": bool(row.get("present")),
                "direction": row.get("direction"),
                "degree": int(row.get("degree") or 0),
                "confidence": float(row.get("confidence") or 0.0),
                "reasoning": str(row.get("reasoning") or ""),
            }

    coverage: dict[str, dict[str, Any]] = {}
    events: dict[str, dict[str, Any]] = {}
    for row in rows:
        row = dict(row)
        row["dimensions"] = dimensions.get(str(row["lens_classification_id"]), {})
        if row.get("lens") == "coverage" and row.get("article_id"):
            coverage[str(row["article_id"])] = row
        elif row.get("lens") == "event" and row.get("event_id"):
            events[str(row["event_id"])] = row
    return coverage, events


def load_resolution_decisions(
    client,
    resolution_run_id: str | None,
    article_ids: list[str],
) -> dict[str, dict[str, Any]]:
    if not resolution_run_id:
        return {}
    rows: list[dict[str, Any]] = []
    for batch in chunks(article_ids, 100):
        response = (
            client.table("event_assignment_decisions")
            .select(
                "article_id,decision,candidate_event_id,assigned_event_id,"
                "event_similarity,modernbert_max_probability,qwen_relationship,"
                "qwen_confidence,requires_review,review_reason"
            )
            .eq("resolution_run_id", resolution_run_id)
            .in_("article_id", batch)
            .execute()
        )
        rows.extend(getattr(response, "data", None) or [])
    return {str(row["article_id"]): row for row in rows}


def reconciliation_row(client, collection_run_id: str) -> dict[str, Any] | None:
    response = (
        client.table("event_reconciliation_runs")
        .select(
            "reconciliation_run_id,run_key,mode,pool_start_at,pool_considered_through,"
            "started_at,completed_at,status,registry_snapshot_id,dry_run,metadata"
        )
        .eq("collection_run_id", collection_run_id)
        .eq("status", "success")
        .eq("dry_run", False)
        .order("completed_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return rows[0] if rows else None


def load_occurrences(client, collection_run_id: str) -> list[dict[str, Any]]:
    response = (
        client.table("event_occurrences")
        .select(
            "occurrence_id,event_id,effective_event_id,story_family_id,article_id,"
            "collection_run_id,release_id,appearance_type,article_published_at,"
            "observed_at,previous_event_coverage_at,days_since_event_first_seen,"
            "days_since_previous_coverage,publisher,source_domain,search_markets,"
            "first_source_appearance,first_market_appearances,resolution_track,"
            "relationship_confidence,metadata"
        )
        .eq("collection_run_id", collection_run_id)
        .execute()
    )
    return getattr(response, "data", None) or []


def percentile(values: list[float], proportion: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    index = (len(ordered) - 1) * proportion
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    fraction = index - low
    return round(ordered[low] + (ordered[high] - ordered[low]) * fraction, 2)


def prior_standardized_event_ids(period: Period) -> set[str]:
    """Return effective event IDs already published in an earlier weekly release.

    Public recurrence is a release-history concept, not a collection-run concept.
    An article can be rediscovered by a retry without ever having appeared in an
    earlier standardized release. Conversely, an event collected on the Monday
    after a prior week still counts as recurring when that prior weekly release
    already published it.
    """
    result: set[str] = set()
    for path in sorted(WEEKLY_DIR.glob("*.json")):
        payload = load_json(path, {})
        if not isinstance(payload, dict):
            continue
        prior_end = parse_date(payload.get("period_end"))
        if prior_end is None or prior_end >= period.start:
            continue
        rows = ((payload.get("units") or {}).get("event_records") or [])
        if not rows:
            rows = payload.get("evidence") or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            event_id = str(row.get("effective_event_id") or row.get("event_id") or "").strip()
            if event_id:
                result.add(event_id)
    return result


def novelty_status(
    appearance_types: set[str],
    *,
    seen_in_prior_release: bool | None = None,
    first_seen: date | None = None,
    period_start: date | None = None,
) -> str:
    """Return the public event-level novelty status.

    Public novelty is anchored to standardized release history. Collection
    retries, repeat search results, and repeat discovery of the same stored
    article are diagnostics and do not by themselves make a development
    recurring.

    Precedence:
    1. unresolved longitudinal matches remain explicitly under validation;
    2. accepted follow-on developments remain new but linked occurrences;
    3. an event already present in an earlier standardized weekly release is recurring;
    4. otherwise it is first-time for the current weekly release.

    first_seen is retained only as a legacy fallback for release files created
    before standardized weekly history was available.
    """
    if "possible_historical_match" in appearance_types:
        return "possible_historical_match"
    if "follow_on_development" in appearance_types:
        return "follow_on_development"
    if seen_in_prior_release is not None:
        return "recurring" if seen_in_prior_release else "first_time"
    if first_seen is not None and period_start is not None:
        return "recurring" if first_seen < period_start else "first_time"
    if "first_event_coverage" in appearance_types:
        return "first_time"
    if "same_event_new_coverage" in appearance_types or "same_article_rediscovered" in appearance_types:
        return "recurring"
    return "unclassified"


def occurrence_dynamics(
    occurrences: list[dict[str, Any]],
    article_rows: dict[str, dict[str, Any]],
    period: Period,
    *,
    ai_article_ids: set[str],
    ai_event_ids: set[str],
    event_statuses: dict[str, str],
) -> dict[str, Any]:
    current_rows: list[dict[str, Any]] = []
    rediscovered = 0
    rediscovery_lags: list[float] = []
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in occurrences:
        article_id = str(row.get("article_id") or "")
        appearance = str(row.get("appearance_type") or "")
        effective_event_id = str(row.get("effective_event_id") or row.get("event_id") or "")
        article = article_rows.get(article_id) or {}
        article_date = date_for_article(article)

        # Rediscovery is an explicit discovery-system signal. The old page is
        # never added to newly published article volume.
        if appearance == "same_article_rediscovered":
            rediscovered += 1
            try:
                rediscovery_lag = float(row.get("days_since_previous_coverage"))
            except (TypeError, ValueError):
                rediscovery_lag = None
            if rediscovery_lag is not None and rediscovery_lag >= 0:
                rediscovery_lags.append(rediscovery_lag)

        if (
            article_id in ai_article_ids
            and effective_event_id in ai_event_ids
            and article_date
            and period.contains(article_date)
        ):
            current_rows.append(row)
            by_event[effective_event_id].append(row)

    recurring_ids: set[str] = set()
    resurfaced_ids: set[str] = set()
    follow_on_ids: set[str] = set()
    first_time_ids: set[str] = set()
    possible_ids: set[str] = set()
    lag_values: list[float] = []
    recurring_article_count = 0

    for event_id, rows in by_event.items():
        status = event_statuses.get(event_id, "unclassified")
        if status == "follow_on_development":
            follow_on_ids.add(event_id)
        elif status == "first_time":
            first_time_ids.add(event_id)
        elif status == "recurring":
            recurring_ids.add(event_id)
        elif status == "possible_historical_match":
            possible_ids.add(event_id)

        # Replication delay describes genuinely recurring attention. Additional
        # articles within a first-time or follow-on episode remain extra
        # coverage, but do not become a long-term replication delay.
        if status == "recurring":
            for row in rows:
                if row.get("appearance_type") != "same_event_new_coverage":
                    continue
                recurring_article_count += 1
                try:
                    lag = float(row.get("days_since_previous_coverage"))
                except (TypeError, ValueError):
                    continue
                if lag < 0:
                    continue
                lag_values.append(lag)
                if lag >= RESURFACE_DAYS:
                    resurfaced_ids.add(event_id)

    return {
        "coverage_episode_count": len(current_rows),
        "first_time_event_appearances": len(first_time_ids),
        "follow_on_developments": len(follow_on_ids),
        "recurring_event_appearances": len(recurring_ids),
        "possible_historical_matches": len(possible_ids),
        "resurfaced_event_appearances": len(resurfaced_ids),
        "same_event_new_coverage_articles": recurring_article_count,
        "rediscovered_article_records": rediscovered,
        "resurface_threshold_days": RESURFACE_DAYS,
        "event_novelty_statuses": event_statuses,
        "replication_lag_days": {
            "count": len(lag_values),
            "median": round(median(lag_values), 2) if lag_values else None,
            "p75": percentile(lag_values, 0.75),
            "p90": percentile(lag_values, 0.90),
            "values": [round(value, 3) for value in sorted(lag_values)],
        },
        "article_rediscovery_lag_days": {
            "count": len(rediscovery_lags),
            "median": round(median(rediscovery_lags), 2) if rediscovery_lags else None,
            "p75": percentile(rediscovery_lags, 0.75),
            "p90": percentile(rediscovery_lags, 0.90),
            "values": [round(value, 3) for value in sorted(rediscovery_lags)],
        },
    }


def attach_release_to_occurrences(client, collection_run_id: str, release_id: str) -> None:
    (
        client.table("event_occurrences")
        .update({"release_id": release_id, "updated_at": iso_z(utc_now())})
        .eq("collection_run_id", collection_run_id)
        .execute()
    )


def public_classification(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ai_relevant": bool(row.get("ai_relevant")),
        "empowerment_status": row.get("empowerment_status"),
        "empowerment_degree": int(row.get("empowerment_degree") or 0),
        "unit_score": row.get("unit_score"),
        "narrative_frame": row.get("narrative_frame"),
        "distribution_breadth": row.get("distribution_breadth"),
        "dominant_dimension": row.get("dominant_dimension"),
        "dimensions": row.get("dimensions") or {},
        "ai_authority_shift": row.get("ai_authority_shift"),
        "topic": row.get("topic"),
        "geographic_scope": row.get("geographic_scope"),
        "primary_country_iso3": row.get("primary_country_iso3"),
        "country_iso3s": row.get("country_iso3s") or [],
        "content_basis": row.get("content_basis"),
        "confidence_diagnostic": row.get("confidence"),
        "reasoning": str(row.get("reasoning") or "")[:1500],
        "requires_review": bool(row.get("requires_review")),
        "review_reason": row.get("review_reason"),
        "audit_selected": bool(row.get("audit_selected")),
        "audit_reason": row.get("audit_reason"),
    }


def build_release(
    period: Period,
    *,
    replace: bool = False,
    revision_reason: str | None = None,
) -> tuple[dict[str, Any], Path]:
    client = supabase_admin()
    classification_run = latest_successful_classification(client)
    collection = collection_row(client, str(classification_run["collection_run_id"]))
    resolution = resolution_row(client, str(classification_run["collection_run_id"]))
    reconciliation = reconciliation_row(client, str(collection["run_id"]))

    observations = load_collection_observations(client, str(collection["run_id"]))
    all_article_ids = sorted(observations)
    article_rows = load_articles(client, all_article_ids)
    translations = load_translations(client, all_article_ids)
    if not reconciliation:
        raise ReleaseError(
            "No accepted longitudinal reconciliation exists for this collection. "
            "Run ‘Reconcile Observatory History’ before building the weekly release."
        )

    occurrences = load_occurrences(client, str(collection["run_id"]))
    occurrence_article_ids = {str(row.get("article_id") or "") for row in occurrences}
    missing_occurrences = [article_id for article_id in all_article_ids if article_id not in occurrence_article_ids]
    if missing_occurrences:
        raise ReleaseError(
            f"{len(missing_occurrences)} collection article(s) are missing from the "
            "longitudinal occurrence ledger. Run reconciliation/backfill first."
        )
    occurrence_by_article = {
        str(row.get("article_id")): row
        for row in occurrences
        if row.get("article_id")
    }

    selected_ids = []
    for aid in all_article_ids:
        row = article_rows.get(aid)
        if not row:
            continue
        article_date = date_for_article(row)
        if article_date and period.contains(article_date):
            selected_ids.append(aid)

    if not selected_ids:
        raise ReleaseError(
            f"No articles from collection {collection['run_key']} fall inside "
            f"{period.start} to {period.end}. Use --period-start/--period-end "
            "if the workflow was run outside its normal Monday schedule."
        )

    links = load_event_links(client, selected_ids)
    candidate_event_ids = unique(str(row["event_id"]) for row in links)
    event_rows = load_events(client, candidate_event_ids)

    active_links = [row for row in links if str(row["event_id"]) in event_rows]
    article_to_events: dict[str, list[str]] = defaultdict(list)
    for row in active_links:
        article_to_events[str(row["article_id"])].append(str(row["event_id"]))

    missing_assignment = [aid for aid in selected_ids if not article_to_events.get(aid)]
    multiple_assignment = [aid for aid in selected_ids if len(set(article_to_events.get(aid, []))) > 1]
    if missing_assignment:
        raise ReleaseError(
            f"{len(missing_assignment)} selected article(s) have no active production event."
        )
    if multiple_assignment:
        raise ReleaseError(
            f"{len(multiple_assignment)} selected article(s) map to more than one active event."
        )

    event_ids = sorted({article_to_events[aid][0] for aid in selected_ids})
    coverage_class, event_class = load_classifications(
        client,
        str(classification_run["classification_run_id"]),
        selected_ids,
        event_ids,
    )

    missing_coverage = [aid for aid in selected_ids if aid not in coverage_class]
    missing_event_class = [eid for eid in event_ids if eid not in event_class]
    if missing_coverage or missing_event_class:
        raise ReleaseError(
            "Classification lineage is incomplete: "
            f"{len(missing_coverage)} coverage unit(s), "
            f"{len(missing_event_class)} event unit(s) missing."
        )

    decisions = load_resolution_decisions(
        client,
        str(resolution["resolution_run_id"]) if resolution else None,
        selected_ids,
    )

    source_config = load_json(ROOT / "config" / "source-strata.json", {})
    coverage_units: list[dict[str, Any]] = []
    for aid in selected_ids:
        article = article_rows[aid]
        translation = translations.get(aid) or {}
        event_id = article_to_events[aid][0]
        article_date = date_for_article(article)
        meta = article.get("source_metadata") if isinstance(article.get("source_metadata"), dict) else {}
        coverage_units.append(
            {
                "article_id": aid,
                "event_id": event_id,
                "effective_event_id": str(
                    (occurrence_by_article.get(aid) or {}).get("effective_event_id")
                    or event_id
                ),
                "story_family_id": (
                    (occurrence_by_article.get(aid) or {}).get("story_family_id")
                    or (event_rows.get(event_id) or {}).get("story_family_id")
                ),
                "appearance_type": (occurrence_by_article.get(aid) or {}).get("appearance_type"),
                "days_since_previous_coverage": (
                    (occurrence_by_article.get(aid) or {}).get("days_since_previous_coverage")
                ),
                "published_date": article_date.isoformat() if article_date else None,
                "published_at": article.get("published_at"),
                "first_seen_at": article.get("first_seen_at"),
                "last_seen_at": article.get("last_seen_at"),
                "headline_original": normalize_name(article.get("headline")),
                "headline_english": normalize_name(
                    translation.get("translated_headline") or article.get("headline")
                ),
                "publisher": normalize_name(article.get("publisher")) or "Unknown source",
                "url": article.get("canonical_url"),
                "source_language": str(
                    translation.get("source_language_iso2")
                    or next(iter(observations[aid]["search_languages"]), "en")
                ),
                "search_rank": observations[aid]["search_rank"],
                "search_markets": sorted(observations[aid]["search_markets"]),
                "search_languages": sorted(observations[aid]["search_languages"]),
                "source_stratum": source_stratum(
                    normalize_name(article.get("publisher")),
                    article.get("canonical_url"),
                    source_config,
                ),
                "snippet": normalize_name(
                    meta.get("snippet")
                    or meta.get("description")
                    or meta.get("summary")
                    or meta.get("source_snippet")
                )[:1000],
                "translation_review_required": bool(translation.get("requires_review")),
                "translation_review_reason": translation.get("review_reason"),
                "resolution_decision": decisions.get(aid, {}).get("decision"),
                "classification": public_classification(coverage_class[aid]),
            }
        )

    selected_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for article in coverage_units:
        selected_by_event[str(article["event_id"])].append(article)

    prior_release_event_ids = prior_standardized_event_ids(period)

    event_units: list[dict[str, Any]] = []
    for eid in event_ids:
        event = event_rows[eid]
        members = sorted(
            selected_by_event[eid],
            key=lambda item: (item.get("published_date") or "", item.get("publisher") or ""),
        )
        first_seen = parse_date(event.get("first_seen_at"))
        appearance_types = {
            str((occurrence_by_article.get(str(item["article_id"])) or {}).get("appearance_type"))
            for item in members
            if (occurrence_by_article.get(str(item["article_id"])) or {}).get("appearance_type")
        }
        event_novelty = novelty_status(
            appearance_types,
            seen_in_prior_release=eid in prior_release_event_ids,
            first_seen=first_seen,
            period_start=period.start,
        )
        event_units.append(
            {
                "event_id": eid,
                "effective_event_id": eid,
                "story_family_id": event.get("story_family_id"),
                "registry_version": event.get("registry_version"),
                "last_reconciled_at": event.get("last_reconciled_at"),
                "event_title": normalize_name(event.get("event_title")),
                "event_summary": normalize_name(event.get("event_summary")),
                "event_date": str(event.get("event_date") or ""),
                "first_seen_at": event.get("first_seen_at"),
                "last_seen_at": event.get("last_seen_at"),
                "first_seen_date": first_seen.isoformat() if first_seen else None,
                "member_article_ids": [str(item["article_id"]) for item in members],
                "member_article_count": len(members),
                "sources": [
                    {
                        "article_id": item["article_id"],
                        "publisher": item["publisher"],
                        "headline": item["headline_english"],
                        "url": item["url"],
                        "published_date": item["published_date"],
                        "source_language": item["source_language"],
                    }
                    for item in members
                ],
                "cluster_confidence": event.get("cluster_confidence"),
                "possible_duplicate_record": bool(event.get("requires_cluster_review")),
                "possible_duplicate_reason": event.get("cluster_review_reason"),
                "appearance_types": sorted(appearance_types),
                "novelty_status": event_novelty,
                "new_in_period": event_novelty in {"first_time", "follow_on_development"},
                "first_time_in_period": event_novelty == "first_time",
                "recurring_in_period": event_novelty == "recurring",
                "follow_on_development": event_novelty == "follow_on_development",
                "possible_historical_match": event_novelty == "possible_historical_match",
                "classification": public_classification(event_class[eid]),
            }
        )

    coverage_ai = [item for item in coverage_units if item["classification"]["ai_relevant"]]
    event_ai = [item for item in event_units if item["classification"]["ai_relevant"]]
    coverage_ai_ids = {str(item["article_id"]) for item in coverage_ai}
    event_ai_ids = {str(item["event_id"]) for item in event_ai}

    # Event-Lens reconciliation uses only AI-relevant article/event units.
    ai_memberships = {
        eid: [aid for aid in selected_by_event[eid] if aid["article_id"] in coverage_ai_ids]
        for eid in event_ai_ids
    }
    mismatched = [
        aid
        for aid in coverage_ai_ids
        if str(article_to_events[aid][0]) not in event_ai_ids
    ]
    if mismatched:
        raise ReleaseError(
            f"{len(mismatched)} AI-relevant coverage unit(s) point to an event "
            "classified as not AI-relevant. Fix Stage 7C before publishing."
        )

    membership_total = sum(len(items) for items in ai_memberships.values())
    extra_coverage_memberships = sum(max(0, len(items) - 1) for items in ai_memberships.values())
    if membership_total != len(coverage_ai):
        raise ReleaseError(
            "Coverage/Event reconciliation failed: AI-relevant article memberships "
            f"sum to {membership_total}, expected {len(coverage_ai)}."
        )

    coverage_summary = calculate_index([coverage_class[item["article_id"]] for item in coverage_units])
    event_summary = calculate_index([event_class[item["event_id"]] for item in event_units])
    coverage_index = coverage_summary.get("empowerment_index")
    event_index = event_summary.get("empowerment_index")
    gap = (
        round(float(coverage_index) - float(event_index), 4)
        if coverage_index is not None and event_index is not None
        else None
    )

    decision_counts = Counter(
        str(decisions.get(aid, {}).get("decision") or "unrecorded")
        for aid in selected_ids
    )
    source_data = source_summary(coverage_ai)
    governance = governance_for(str(classification_run["classification_run_id"]))
    dynamics = occurrence_dynamics(
        occurrences,
        article_rows,
        period,
        ai_article_ids=coverage_ai_ids,
        ai_event_ids=event_ai_ids,
        event_statuses={
            str(item["event_id"]): str(item.get("novelty_status") or "unclassified")
            for item in event_ai
        },
    )

    counts = {
        "observed_article_records": len(coverage_units),
        "ai_relevant_articles": len(coverage_ai),
        "represented_event_records": len(event_units),
        "ai_relevant_event_records": len(event_ai),
        "new_event_records": sum(
            item.get("novelty_status") in {"first_time", "follow_on_development"}
            for item in event_ai
        ),
        "first_time_event_records": sum(
            item.get("novelty_status") == "first_time" for item in event_ai
        ),
        "follow_on_event_records": sum(
            item.get("novelty_status") == "follow_on_development" for item in event_ai
        ),
        "recurring_event_records": sum(
            item.get("novelty_status") == "recurring" for item in event_ai
        ),
        "possible_historical_match_event_records": sum(
            item.get("novelty_status") == "possible_historical_match" for item in event_ai
        ),
        "unclassified_novelty_event_records": sum(
            item.get("novelty_status") == "unclassified" for item in event_ai
        ),
        "resurfaced_event_records": int(dynamics["resurfaced_event_appearances"]),
        "rediscovered_article_records": int(dynamics["rediscovered_article_records"]),
        "extra_coverage": len(coverage_ai) - len(event_ai),
        "extra_coverage_from_memberships": extra_coverage_memberships,
        "singleton_event_records": sum(
            len(ai_memberships.get(str(item["event_id"]), [])) == 1
            for item in event_ai
        ),
        "multi_source_event_records": sum(
            len(ai_memberships.get(str(item["event_id"]), [])) > 1
            for item in event_ai
        ),
        "possible_duplicate_event_records": sum(
            bool(item["possible_duplicate_record"]) for item in event_ai
        ),
        "unique_publications": source_data["unique_publications"],
        "unique_domains": source_data["unique_domains"],
    }

    if counts["extra_coverage"] != counts["extra_coverage_from_memberships"]:
        raise ReleaseError(
            "Top-line extra coverage does not equal the sum of current-period "
            "event membership excess."
        )

    release_id = iso_week_id(period)
    generated_at = iso_z(utc_now())
    previous = load_json(WEEKLY_DIR / f"{release_id}.json")
    revision = int((previous or {}).get("revision") or 0) + 1 if previous else 1

    release = {
        "schema_version": "aieo_public_release_v3.0",
        "release_id": release_id,
        "release_type": "weekly",
        "revision": revision,
        "period_start": period.start.isoformat(),
        "period_end": period.end.isoformat(),
        "generated_at": generated_at,
        "public_label": governance["public_label"],
        "lineage": {
            "collection_run_id": collection["run_id"],
            "collection_run_key": collection["run_key"],
            "collection_started_at": collection.get("started_at"),
            "collection_completed_at": collection.get("completed_at"),
            "resolution_run_id": resolution.get("resolution_run_id") if resolution else None,
            "resolution_run_key": resolution.get("run_key") if resolution else None,
            "resolver_version": resolution.get("resolver_version") if resolution else None,
            "classification_run_id": classification_run["classification_run_id"],
            "classification_run_key": classification_run["run_key"],
            "classifier_version": classification_run.get("classifier_version"),
        },
        "definitions": {
            "coverage_lens": "Each newly published AI-relevant article in this declared weekly period receives one weight.",
            "event_lens": "Each effective resolved event represented by those articles receives one weight.",
            "extra_coverage": "AI-relevant published articles minus effective represented events for the same period.",
            "new_event_record": "A first-time event or a genuine follow-on occurrence not collapsed into earlier reality.",
            "recurring_event_record": "An effective event already present in an earlier standardized AIEO weekly release and represented again by current-week coverage.",
            "resurfaced_event_record": "A recurring event receiving new coverage after at least 28 days without observed coverage.",
            "rediscovered_article_record": "A previously stored article page returned by a later collection run. This is a collection diagnostic and does not by itself make a development recurring.",
            "follow_on_development": "A genuinely new occurrence linked to an earlier event through a continuing story family.",
            "event_record_caveat": "AIEO uses a precision-first resolver. Ambiguous possible duplicates remain separate until governance resolves them.",
        },
        "observation_period": {
            "start": period.start.isoformat(),
            "end": period.end.isoformat(),
            "basis": "publication date, with first-seen date used only when publication date is unavailable",
        },
        "historical_pool": {
            "starts_at": reconciliation.get("pool_start_at") if reconciliation else None,
            "considered_through": reconciliation.get("pool_considered_through") if reconciliation else collection.get("completed_at"),
            "registry_snapshot_id": reconciliation.get("registry_snapshot_id") if reconciliation else None,
            "all_prior_events_considered": True,
            "disclosure": "First-time means the canonical development was not present in an earlier standardized AIEO weekly release. Collection retries do not turn a current-week development into a recurring one.",
        },
        "reconciliation": {
            "run_id": reconciliation.get("reconciliation_run_id") if reconciliation else None,
            "run_key": reconciliation.get("run_key") if reconciliation else None,
            "mode": reconciliation.get("mode") if reconciliation else None,
            "completed_at": reconciliation.get("completed_at") if reconciliation else None,
            "status": reconciliation.get("status") if reconciliation else "not_available",
        },
        "data_current_through": (
            reconciliation.get("completed_at") if reconciliation else collection.get("completed_at")
        ),
        "counts": counts,
        "dynamics": dynamics,
        "lenses": {
            "coverage": coverage_summary,
            "event": event_summary,
        },
        "amplification": {
            "directional_gap": gap,
            "coverage_event_ratio": round(len(coverage_ai) / len(event_ai), 4) if event_ai else None,
        },
        "resolution": {
            "decision_counts": dict(sorted(decision_counts.items())),
            "precision_first": True,
            "event_count_is_provisional": bool(counts["possible_duplicate_event_records"]),
            "conservative_distinct_event_range": {
                "minimum": max(
                    0,
                    counts["ai_relevant_event_records"]
                    - counts["possible_duplicate_event_records"],
                ),
                "maximum": counts["ai_relevant_event_records"],
            },
        },
        "sources": source_data,
        "reliability": {
            "validation_status": "passed",
            "governance": governance,
            "coverage_diagnostics": {
                "source_strata_represented": sorted(source_data["strata"]),
                "unclassified_source_share": source_data["unclassified_source_share"],
                "newsletter_feed_coverage_claimed": False,
                "coverage_claim": "Observed discovery coverage; not a comprehensive census of AI news.",
            },
            "event_diagnostics": {
                "singleton_share": round(
                    counts["singleton_event_records"]
                    / counts["ai_relevant_event_records"],
                    6,
                ) if counts["ai_relevant_event_records"] else 0.0,
                "multi_source_share": round(
                    counts["multi_source_event_records"]
                    / counts["ai_relevant_event_records"],
                    6,
                ) if counts["ai_relevant_event_records"] else 0.0,
                "possible_duplicate_records": counts["possible_duplicate_event_records"],
                "same_event_recall_validated": False,
                "longitudinal_event_memory_active": True,
                "historical_pool_disclosed": True,
            },
            "denominators": {
                "coverage_total": coverage_summary["unit_count_total"],
                "coverage_ai_relevant": coverage_summary["unit_count_ai_relevant"],
                "coverage_scored": coverage_summary["unit_count_scored"],
                "coverage_excluded_unclear": coverage_summary["unit_count_excluded_unclear"],
                "coverage_not_ai_relevant": coverage_summary["unit_count_not_ai_relevant"],
                "event_total": event_summary["unit_count_total"],
                "event_ai_relevant": event_summary["unit_count_ai_relevant"],
                "event_scored": event_summary["unit_count_scored"],
                "event_excluded_unclear": event_summary["unit_count_excluded_unclear"],
                "event_not_ai_relevant": event_summary["unit_count_not_ai_relevant"],
            },
        },
        "evidence": sorted(
            event_ai,
            key=lambda item: (
                -int(item["member_article_count"]),
                str(item.get("event_date") or ""),
                str(item.get("event_title") or ""),
            ),
        ),
        "units": {
            "coverage_articles": coverage_units,
            "event_records": event_units,
        },
    }
    if previous and replace:
        if not revision_reason:
            raise ReleaseError("--revision-reason is required with --replace.")
        prior_history = list(previous.get("revision_history") or [])
        prior_history.append(
            {
                "revision": revision,
                "changed_at": generated_at,
                "reason": revision_reason,
                "previous_revision": int(previous.get("revision") or 1),
            }
        )
        release["revision_reason"] = revision_reason
        release["revision_history"] = prior_history
        release["previous_revision_path"] = (
            f"/data/releases/weekly/archive/{release_id}/"
            f"revision-{int(previous.get('revision') or 1)}.json"
        )
    release["content_sha256"] = stable_hash(
        {key: value for key, value in release.items() if key != "content_sha256"}
    )

    output_path = WEEKLY_DIR / f"{release_id}.json"
    if previous and not replace:
        if measurement_hash(previous) == measurement_hash(release):
            # Scheduled reruns are safe: keep the immutable release byte-for-byte
            # stable, refresh the current pointer, and rebuild the public index.
            write_json(CURRENT_RELEASE, previous)
            attach_release_to_occurrences(client, str(collection["run_id"]), release_id)
            update_index()
            return previous, output_path
        raise ReleaseError(
            f"{output_path.relative_to(ROOT)} already exists with different "
            "measurement content. Re-run with --replace to create a new "
            "revision while preserving the previous revision."
        )
    if previous and replace:
        archive = (
            WEEKLY_DIR / "archive" / release_id
            / f"revision-{int(previous.get('revision') or 1)}.json"
        )
        if not archive.exists():
            write_json(archive, previous)

    write_json(output_path, release)
    write_json(CURRENT_RELEASE, release)
    attach_release_to_occurrences(client, str(collection["run_id"]), release_id)
    update_index()
    return release, output_path


def substantive_release_payload(release: dict[str, Any]) -> dict[str, Any]:
    """Return the measurement-bearing release content for idempotency checks."""
    return {
        key: value
        for key, value in release.items()
        if key not in {
            "generated_at", "revision", "content_sha256", "revision_reason",
            "revision_history", "previous_revision_path", "restated_at",
            "restatement_version",
        }
    }


def measurement_hash(release: dict[str, Any]) -> str:
    return stable_hash(substantive_release_payload(release))


def load_historical_snapshots() -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    if not BASELINE_DIR.exists():
        return snapshots
    for path in sorted(BASELINE_DIR.glob("*.json")):
        item = load_json(path)
        if not isinstance(item, dict):
            continue
        required = {"snapshot_id", "period_start", "period_end", "articles", "event_records"}
        missing = sorted(required - set(item))
        if missing:
            raise ReleaseError(
                f"Historical snapshot {path.relative_to(ROOT)} is missing: {missing}"
            )
        snapshots.append(item)
    snapshots.sort(key=lambda item: (item["period_end"], item["snapshot_id"]))
    return snapshots


def snapshot_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "snapshot_id": item["snapshot_id"],
        "snapshot_type": item.get("snapshot_type", "historical_snapshot"),
        "label": item.get("label", "Historical snapshot"),
        "series_kind": "historical_snapshot",
        "period_start": item["period_start"],
        "period_end": item["period_end"],
        "generated_at": item.get("generated_at"),
        "articles": int(item["articles"]),
        "event_records": int(item["event_records"]),
        "extra_coverage": int(item.get("extra_coverage", int(item["articles"]) - int(item["event_records"]))),
        "coverage_index": item.get("coverage_index"),
        "event_index": item.get("event_index"),
        "amplification_gap": item.get("amplification_gap"),
        "audit_status": item.get("audit_status"),
        "validation_status": item.get("validation_status"),
        "comparable_to_weekly_series": False,
        "connect_to_previous": False,
        "note": item.get("note"),
    }


def weekly_display_series(releases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    previous_end: date | None = None
    for release in releases:
        summary = release_summary(release)
        start = date.fromisoformat(str(summary["period_start"]))
        end = date.fromisoformat(str(summary["period_end"]))
        connected = bool(previous_end and start == previous_end + timedelta(days=1))
        series.append(
            {
                **summary,
                "series_kind": "weekly",
                "label": f"Week ending {summary['period_end']}",
                "comparable_to_weekly_series": True,
                "connect_to_previous": connected,
            }
        )
        previous_end = end
    return series


def update_index() -> dict[str, Any]:
    weekly_files = sorted(path for path in WEEKLY_DIR.glob("*.json") if path.is_file())
    releases = [load_json(path) for path in weekly_files]
    releases = [item for item in releases if isinstance(item, dict)]
    releases.sort(key=lambda item: (item["period_end"], item["release_id"]))

    historical = load_historical_snapshots()
    historical_summaries = [snapshot_summary(item) for item in historical]
    weekly_summaries = [release_summary(item) for item in releases]
    weekly_series = weekly_display_series(releases)

    all_article_ids: set[str] = set()
    all_event_ids: set[str] = set()
    for release in releases:
        all_article_ids.update(
            str(row["article_id"])
            for row in release.get("units", {}).get("coverage_articles", [])
            if row.get("classification", {}).get("ai_relevant")
        )
        all_event_ids.update(
            str(row.get("effective_event_id") or row["event_id"])
            for row in release.get("units", {}).get("event_records", [])
            if row.get("classification", {}).get("ai_relevant")
        )

    current = releases[-1] if releases else None
    month_to_date_articles: set[str] = set()
    month_to_date_events: set[str] = set()
    if current:
        current_month = current["period_end"][:7]
        for release in releases:
            for row in release.get("units", {}).get("coverage_articles", []):
                if (
                    str(row.get("published_date") or "").startswith(current_month)
                    and row.get("classification", {}).get("ai_relevant")
                ):
                    month_to_date_articles.add(str(row["article_id"]))
            for row in release.get("units", {}).get("event_records", []):
                if not row.get("classification", {}).get("ai_relevant"):
                    continue
                if any(
                    str(source.get("published_date") or "").startswith(current_month)
                    for source in row.get("sources", [])
                ):
                    month_to_date_events.add(str(row.get("effective_event_id") or row["event_id"]))

    generated_values = [
        str(item.get("generated_at"))
        for item in [*historical_summaries, *weekly_summaries]
        if item.get("generated_at")
    ]
    updated_at = max(generated_values) if generated_values else iso_z(utc_now())

    display_series = sorted(
        [*historical_summaries, *weekly_series],
        key=lambda item: (item["period_end"], item.get("snapshot_id") or item.get("release_id") or ""),
    )

    standardized_cumulative = {
        "distinct_articles": len(all_article_ids),
        "distinct_event_records": len(all_event_ids),
        "release_count": len(releases),
        "period_start": releases[0]["period_start"] if releases else None,
        "period_end": releases[-1]["period_end"] if releases else None,
    }

    revision_archives: dict[str, list[dict[str, Any]]] = {}
    archive_root = WEEKLY_DIR / "archive"
    if archive_root.exists():
        for release_dir in sorted(path for path in archive_root.iterdir() if path.is_dir()):
            rows = []
            for path in sorted(release_dir.glob("revision-*.json")):
                item = load_json(path)
                if not isinstance(item, dict):
                    continue
                rows.append({
                    "revision": int(item.get("revision") or 1),
                    "generated_at": item.get("generated_at"),
                    "path": "/" + str(path.relative_to(ROOT)).replace("\\", "/"),
                    "content_sha256": item.get("content_sha256"),
                })
            if rows:
                revision_archives[release_dir.name] = rows

    index = {
        "schema_version": "aieo_release_index_v3.0",
        "updated_at": updated_at,
        # Backward-compatible: release_count continues to mean standardized weeks.
        "release_count": len(releases),
        "weekly_release_count": len(releases),
        "historical_snapshot_count": len(historical_summaries),
        "publication_snapshot_count": len(releases) + len(historical_summaries),
        "current_release_id": current.get("release_id") if current else None,
        "current_revision": int(current.get("revision") or 1) if current else None,
        "data_current_through": (
            current.get("data_current_through") if current else None
        ),
        "historical_pool": current.get("historical_pool") if current else None,
        "revision_archives": revision_archives,
        "historical_snapshots": historical_summaries,
        "weekly": weekly_summaries,
        "display_series": display_series,
        "cumulative_standardized_series": standardized_cumulative,
        "cumulative_since_launch": {
            **standardized_cumulative,
            "scope": "standardized_weekly_releases_only",
            "historical_snapshots_excluded": len(historical_summaries),
        },
        "month_to_date": {
            "month": current["period_end"][:7] if current else None,
            "distinct_articles": len(month_to_date_articles),
            "distinct_event_records": len(month_to_date_events),
        },
        "comparison_rule": {
            "weekly_series": "Connect only consecutive, non-overlapping Monday-Sunday releases.",
            "historical_snapshots": "Show as separate reference points and never connect them to the weekly line.",
            "recurrence": "Repeated coverage changes attention volume but does not create another effective event.",
            "revisions": "The current series uses the latest accepted revision; earlier revisions remain accessible in the archive.",
        },
    }
    write_json(RELEASE_INDEX, index)
    return index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period-start", type=date.fromisoformat)
    parser.add_argument("--period-end", type=date.fromisoformat)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument(
        "--revision-reason",
        help="Required with --replace. Published in the revision ledger.",
    )
    parser.add_argument(
        "--rebuild-index-only",
        action="store_true",
        help="Rebuild data/releases/index.json without contacting Supabase.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.rebuild_index_only:
        index = update_index()
        print(
            json.dumps(
                {
                    "weekly_release_count": index["weekly_release_count"],
                    "historical_snapshot_count": index["historical_snapshot_count"],
                    "publication_snapshot_count": index["publication_snapshot_count"],
                    "current_release_id": index["current_release_id"],
                    "output": str(RELEASE_INDEX.relative_to(ROOT)),
                },
                indent=2,
            )
        )
        return 0

    if bool(args.period_start) != bool(args.period_end):
        raise ReleaseError("Provide both --period-start and --period-end.")
    period = (
        Period(args.period_start, args.period_end)
        if args.period_start and args.period_end
        else previous_complete_week()
    )
    if period.start > period.end:
        raise ReleaseError("period_start must not be later than period_end.")

    if args.replace and not str(args.revision_reason or "").strip():
        raise ReleaseError("--revision-reason is required when --replace is used.")
    release, path = build_release(
        period,
        replace=args.replace,
        revision_reason=str(args.revision_reason or "").strip() or None,
    )
    print(
        json.dumps(
            {
                "release_id": release["release_id"],
                "revision": release["revision"],
                "period": [release["period_start"], release["period_end"]],
                "articles": release["counts"]["ai_relevant_articles"],
                "event_records": release["counts"]["ai_relevant_event_records"],
                "new_event_records": release["counts"]["new_event_records"],
                "recurring_event_records": release["counts"]["recurring_event_records"],
                "extra_coverage": release["counts"]["extra_coverage"],
                "output": str(path.relative_to(ROOT)),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseError as exc:
        print(f"Weekly release failed: {exc}", flush=True)
        raise SystemExit(1)
