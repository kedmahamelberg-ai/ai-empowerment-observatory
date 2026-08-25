#!/usr/bin/env python3
"""Restate weekly releases after accepted longitudinal event corrections.

The previous revision is archived before a changed release is written. The
latest release receives revision n+1, an explicit reason and a machine-readable
change summary. Re-running the script is idempotent when no accepted registry
change affects a release.
"""

from __future__ import annotations

import argparse
import copy
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from build_weekly_release import novelty_status, update_index
from release_common import (
    CURRENT_RELEASE,
    ROOT,
    WEEKLY_DIR,
    ReleaseError,
    calculate_index,
    load_json,
    parse_date,
    source_summary,
    stable_hash,
    supabase_admin,
    write_json,
)

ARCHIVE_DIR = WEEKLY_DIR / "archive"
RESTATE_VERSION = "release_restatement_v1.1"


class RestatementError(RuntimeError):
    """A weekly release could not be safely restated."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_registry(client) -> dict[str, dict[str, Any]]:
    response = (
        client.table("events")
        .select(
            "event_id,event_title,event_summary,event_date,first_seen_at,last_seen_at,"
            "canonical_event_id,story_family_id,canonicalized_at,canonicalization_reason,"
            "requires_cluster_review,cluster_review_reason,cluster_confidence,registry_version"
        )
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return {str(row["event_id"]): row for row in rows}


def canonical_map(registry: dict[str, dict[str, Any]]) -> dict[str, str]:
    direct = {
        event_id: str(row["canonical_event_id"])
        for event_id, row in registry.items()
        if row.get("canonical_event_id")
    }
    result: dict[str, str] = {}
    for event_id in registry:
        current = event_id
        seen: set[str] = set()
        while current in direct:
            if current in seen:
                raise RestatementError(f"Canonical-event cycle detected at {current}")
            seen.add(current)
            current = direct[current]
        result[event_id] = current
    return result


def latest_reconciliation(client) -> dict[str, Any] | None:
    response = (
        client.table("event_reconciliation_runs")
        .select(
            "reconciliation_run_id,run_key,mode,pool_start_at,pool_considered_through,"
            "completed_at,status,registry_snapshot_id,dry_run"
        )
        .eq("status", "success")
        .eq("dry_run", False)
        .order("completed_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return rows[0] if rows else None


def date_value(value: Any) -> date | None:
    try:
        return parse_date(value)
    except Exception:
        return None


def is_ai(row: dict[str, Any]) -> bool:
    return bool((row.get("classification") or {}).get("ai_relevant"))


def choose_base_row(rows: list[dict[str, Any]], canonical_id: str) -> dict[str, Any]:
    canonical = [row for row in rows if str(row.get("event_id")) == canonical_id]
    candidates = canonical or sorted(
        rows,
        key=lambda row: (len(row.get("sources") or []), str(row.get("first_seen_at") or "")),
        reverse=True,
    )
    chosen = copy.deepcopy(candidates[0])
    labels = {
        (
            (row.get("classification") or {}).get("empowerment_status"),
            (row.get("classification") or {}).get("empowerment_degree"),
            (row.get("classification") or {}).get("dominant_dimension"),
        )
        for row in rows
    }
    if len(labels) > 1:
        classification = copy.deepcopy(chosen.get("classification") or {})
        existing = str(classification.get("review_reason") or "").strip()
        note = "event classifications differed before longitudinal merge"
        classification["requires_review"] = True
        classification["review_reason"] = f"{existing}; {note}".strip("; ")
        chosen["classification"] = classification
    return chosen


def merge_event_rows(
    rows: list[dict[str, Any]],
    *,
    canonical_id: str,
    registry: dict[str, dict[str, Any]],
    period_start: date,
    period_end: date,
) -> dict[str, Any]:
    chosen = choose_base_row(rows, canonical_id)
    canonical = registry.get(canonical_id) or {}
    sources: dict[str, dict[str, Any]] = {}
    member_ids: set[str] = set()
    original_ids: set[str] = set()
    duplicate_reasons: set[str] = set()
    for row in rows:
        original_ids.add(str(row.get("event_id") or ""))
        member_ids.update(str(value) for value in (row.get("member_article_ids") or []))
        for source in row.get("sources") or []:
            article_id = str(source.get("article_id") or "")
            if article_id:
                sources.setdefault(article_id, copy.deepcopy(source))
        if row.get("possible_duplicate_reason"):
            duplicate_reasons.add(str(row["possible_duplicate_reason"]))

    first_seen_at = canonical.get("first_seen_at") or min(
        (str(row.get("first_seen_at")) for row in rows if row.get("first_seen_at")),
        default=None,
    )
    first_seen = date_value(first_seen_at)
    chosen.update(
        {
            "event_id": canonical_id,
            "effective_event_id": canonical_id,
            "original_event_ids": sorted(value for value in original_ids if value),
            "story_family_id": canonical.get("story_family_id") or chosen.get("story_family_id"),
            "event_title": canonical.get("event_title") or chosen.get("event_title"),
            "event_summary": canonical.get("event_summary") or chosen.get("event_summary"),
            "event_date": canonical.get("event_date") or chosen.get("event_date"),
            "first_seen_at": first_seen_at,
            "last_seen_at": canonical.get("last_seen_at") or max(
                (str(row.get("last_seen_at")) for row in rows if row.get("last_seen_at")),
                default=None,
            ),
            "first_seen_date": first_seen.isoformat() if first_seen else None,
            "new_in_period": bool(first_seen and period_start <= first_seen <= period_end),
            "member_article_ids": sorted(member_ids or sources),
            "member_article_count": len(member_ids or sources),
            "sources": sorted(
                sources.values(),
                key=lambda source: (str(source.get("published_date") or ""), str(source.get("publisher") or "")),
            ),
            "cluster_confidence": canonical.get("cluster_confidence") or chosen.get("cluster_confidence"),
            "possible_duplicate_record": bool(canonical.get("requires_cluster_review")),
            "possible_duplicate_reason": canonical.get("cluster_review_reason") or (
                "; ".join(sorted(duplicate_reasons)) if duplicate_reasons else None
            ),
            "registry_version": canonical.get("registry_version"),
        }
    )
    return chosen


def load_release_occurrences(client, release: dict[str, Any]) -> list[dict[str, Any]]:
    collection_run_id = str((release.get("lineage") or {}).get("collection_run_id") or "")
    if not collection_run_id:
        return []
    response = (
        client.table("event_occurrences")
        .select(
            "event_id,effective_event_id,story_family_id,article_id,appearance_type,"
            "observed_at,days_since_previous_coverage,release_id"
        )
        .eq("collection_run_id", collection_run_id)
        .execute()
    )
    return getattr(response, "data", None) or []


def recalculate_dynamics(
    coverage_rows: list[dict[str, Any]],
    event_rows: list[dict[str, Any]],
    occurrences: list[dict[str, Any]],
) -> dict[str, Any]:
    ai_articles = {
        str(row.get("article_id") or "")
        for row in coverage_rows
        if is_ai(row)
    }
    ai_events = {
        str(row.get("event_id") or "")
        for row in event_rows
        if is_ai(row)
    }
    statuses = {
        str(row.get("event_id") or ""): str(row.get("novelty_status") or "unclassified")
        for row in event_rows
    }
    rediscovered = 0
    rediscovery_lag_values: list[float] = []
    for row in occurrences:
        if str(row.get("appearance_type") or "") != "same_article_rediscovered":
            continue
        rediscovered += 1
        try:
            lag = float(row.get("days_since_previous_coverage"))
        except (TypeError, ValueError):
            continue
        if lag >= 0:
            rediscovery_lag_values.append(lag)
    recurring_articles = 0
    resurfaced: set[str] = set()
    lag_values: list[float] = []
    for row in occurrences:
        article_id = str(row.get("article_id") or "")
        event_id = str(row.get("effective_event_id") or row.get("event_id") or "")
        if article_id not in ai_articles or event_id not in ai_events:
            continue
        if statuses.get(event_id) != "recurring":
            continue
        if row.get("appearance_type") != "same_event_new_coverage":
            continue
        recurring_articles += 1
        try:
            lag = float(row.get("days_since_previous_coverage"))
        except (TypeError, ValueError):
            continue
        if lag < 0:
            continue
        lag_values.append(lag)
        if lag >= 28:
            resurfaced.add(event_id)
    lag_values.sort()
    rediscovery_lag_values.sort()
    def pct(values: list[float], p: float) -> float | None:
        if not values:
            return None
        if len(values) == 1:
            return round(values[0], 2)
        idx = (len(values) - 1) * p
        lo = int(idx)
        hi = min(lo + 1, len(values) - 1)
        return round(values[lo] + (values[hi] - values[lo]) * (idx - lo), 2)
    return {
        "coverage_episode_count": len(ai_articles),
        "first_time_event_appearances": sum(value == "first_time" for value in statuses.values()),
        "follow_on_developments": sum(value == "follow_on_development" for value in statuses.values()),
        "recurring_event_appearances": sum(value == "recurring" for value in statuses.values()),
        "possible_historical_matches": sum(value == "possible_historical_match" for value in statuses.values()),
        "resurfaced_event_appearances": len(resurfaced),
        "same_event_new_coverage_articles": recurring_articles,
        "rediscovered_article_records": rediscovered,
        "resurface_threshold_days": 28,
        "event_novelty_statuses": statuses,
        "replication_lag_days": {
            "count": len(lag_values),
            "median": round(median(lag_values), 2) if lag_values else None,
            "p75": pct(lag_values, 0.75),
            "p90": pct(lag_values, 0.90),
            "values": [round(value, 3) for value in lag_values],
        },
        "article_rediscovery_lag_days": {
            "count": len(rediscovery_lag_values),
            "median": round(median(rediscovery_lag_values), 2) if rediscovery_lag_values else None,
            "p75": pct(rediscovery_lag_values, 0.75),
            "p90": pct(rediscovery_lag_values, 0.90),
            "values": [round(value, 3) for value in rediscovery_lag_values],
        },
    }


def recalculate(
    client,
    release: dict[str, Any],
    *,
    registry: dict[str, dict[str, Any]],
    cmap: dict[str, str],
    reconciliation: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    updated = copy.deepcopy(release)
    coverage_rows = (updated.setdefault("units", {})).get("coverage_articles") or []
    original_event_rows = updated["units"].get("event_records") or []
    occurrences = load_release_occurrences(client, updated)
    occurrence_by_article = {
        str(row.get("article_id") or ""): row
        for row in occurrences
        if row.get("article_id")
    }
    coverage_article_ids = {
        str(row.get("article_id") or "")
        for row in coverage_rows
        if row.get("article_id")
    }
    missing_occurrences = sorted(coverage_article_ids - set(occurrence_by_article))
    if missing_occurrences:
        raise RestatementError(
            f"{len(missing_occurrences)} release article(s) have no occurrence-ledger row. "
            "Run Backfill Observatory Event Occurrences before restating releases."
        )

    changed_articles = 0
    for row in coverage_rows:
        old_id = str(row.get("effective_event_id") or row.get("event_id") or "")
        new_id = cmap.get(old_id, old_id)
        row["original_event_id"] = str(row.get("event_id") or old_id)
        row["event_id"] = new_id
        row["effective_event_id"] = new_id
        row["story_family_id"] = (registry.get(new_id) or {}).get("story_family_id")
        occurrence = occurrence_by_article.get(str(row.get("article_id") or "")) or {}
        if occurrence.get("appearance_type"):
            row["appearance_type"] = occurrence.get("appearance_type")
            row["days_since_previous_coverage"] = occurrence.get("days_since_previous_coverage")
        if new_id != old_id:
            changed_articles += 1
            row["longitudinal_assignment"] = "same_event_new_coverage"

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in original_event_rows:
        old_id = str(row.get("effective_event_id") or row.get("event_id") or "")
        grouped[cmap.get(old_id, old_id)].append(row)

    start = date_value(updated.get("period_start"))
    end = date_value(updated.get("period_end"))
    if not start or not end:
        raise RestatementError(f"Invalid release period: {updated.get('release_id')}")
    event_rows = [
        merge_event_rows(
            rows,
            canonical_id=canonical_id,
            registry=registry,
            period_start=start,
            period_end=end,
        )
        for canonical_id, rows in grouped.items()
    ]
    coverage_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in coverage_rows:
        coverage_by_event[str(row.get("event_id") or "")].append(row)
    for row in event_rows:
        event_id = str(row.get("event_id") or "")
        types = {
            str(item.get("appearance_type") or "")
            for item in coverage_by_event.get(event_id, [])
            if item.get("appearance_type")
        }
        status = novelty_status(types)
        row["appearance_types"] = sorted(types)
        row["novelty_status"] = status
        row["new_in_period"] = status in {"first_time", "follow_on_development"}
        row["first_time_in_period"] = status == "first_time"
        row["recurring_in_period"] = status == "recurring"
        row["follow_on_development"] = status == "follow_on_development"
        row["possible_historical_match"] = status == "possible_historical_match"
    event_rows.sort(key=lambda row: (str(row.get("event_date") or ""), str(row.get("event_id") or "")))
    updated["units"]["event_records"] = event_rows

    relevant_articles = [row for row in coverage_rows if is_ai(row)]
    relevant_events = [row for row in event_rows if is_ai(row)]
    coverage_lens = calculate_index([row.get("classification") or {} for row in coverage_rows])
    event_lens = calculate_index([row.get("classification") or {} for row in event_rows])
    updated["lenses"] = {"coverage": coverage_lens, "event": event_lens}

    first_time_count = sum(row.get("novelty_status") == "first_time" for row in relevant_events)
    follow_on_count = sum(row.get("novelty_status") == "follow_on_development" for row in relevant_events)
    new_count = first_time_count + follow_on_count
    recurring_count = sum(row.get("novelty_status") == "recurring" for row in relevant_events)
    possible_match_count = sum(row.get("novelty_status") == "possible_historical_match" for row in relevant_events)
    unclassified_count = sum(row.get("novelty_status") == "unclassified" for row in relevant_events)
    possible_duplicates = sum(bool(row.get("possible_duplicate_record")) for row in relevant_events)
    extra = max(0, len(relevant_articles) - len(relevant_events))
    sources = source_summary(relevant_articles)
    counts = copy.deepcopy(updated.get("counts") or {})
    counts.update(
        {
            "observed_article_records": len(coverage_rows),
            "ai_relevant_articles": len(relevant_articles),
            "represented_event_records": len(event_rows),
            "ai_relevant_event_records": len(relevant_events),
            "new_event_records": new_count,
            "first_time_event_records": first_time_count,
            "follow_on_event_records": follow_on_count,
            "recurring_event_records": recurring_count,
            "possible_historical_match_event_records": possible_match_count,
            "unclassified_novelty_event_records": unclassified_count,
            "extra_coverage": extra,
            "extra_coverage_from_memberships": extra,
            "singleton_event_records": sum(int(row.get("member_article_count") or 0) == 1 for row in relevant_events),
            "multi_source_event_records": sum(int(row.get("member_article_count") or 0) > 1 for row in relevant_events),
            "possible_duplicate_event_records": possible_duplicates,
            "unique_publications": sources.get("unique_publications", 0),
            "unique_domains": sources.get("unique_domains", 0),
        }
    )
    updated["counts"] = counts
    updated["dynamics"] = recalculate_dynamics(coverage_rows, event_rows, occurrences)
    counts["resurfaced_event_records"] = int(updated["dynamics"]["resurfaced_event_appearances"])
    counts["rediscovered_article_records"] = int(updated["dynamics"]["rediscovered_article_records"])
    updated["sources"] = sources
    coverage_index = coverage_lens.get("empowerment_index")
    event_index = event_lens.get("empowerment_index")
    updated["amplification"] = {
        "directional_gap": (
            round(float(coverage_index) - float(event_index), 4)
            if coverage_index is not None and event_index is not None else None
        ),
        "coverage_event_ratio": round(len(relevant_articles) / len(relevant_events), 4) if relevant_events else None,
    }
    updated.setdefault("resolution", {})["event_count_is_provisional"] = bool(possible_duplicates)
    updated["resolution"]["conservative_distinct_event_range"] = {
        "minimum": max(0, len(relevant_events) - possible_duplicates),
        "maximum": len(relevant_events),
    }
    updated["evidence"] = [row for row in event_rows if is_ai(row)]

    if reconciliation:
        updated["historical_pool"] = {
            "starts_at": reconciliation.get("pool_start_at"),
            "considered_through": reconciliation.get("pool_considered_through"),
            "registry_snapshot_id": reconciliation.get("registry_snapshot_id"),
            "all_prior_events_considered": True,
        }
        updated["data_current_through"] = reconciliation.get("completed_at")
        updated["reconciliation"] = {
            "run_id": reconciliation.get("reconciliation_run_id"),
            "run_key": reconciliation.get("run_key"),
            "mode": reconciliation.get("mode"),
            "completed_at": reconciliation.get("completed_at"),
            "status": reconciliation.get("status"),
        }

    before_ids = {
        str(row.get("effective_event_id") or row.get("event_id") or "")
        for row in original_event_rows
    }
    after_ids = {str(row.get("event_id") or "") for row in event_rows}
    before_relevant = len([row for row in original_event_rows if is_ai(row)])
    summary = {
        "coverage_articles_reassigned": changed_articles,
        "event_records_before": before_relevant,
        "event_records_after": len(relevant_events),
        "event_record_change": len(relevant_events) - before_relevant,
        "new_event_records_before": int((release.get("counts") or {}).get("new_event_records") or 0),
        "new_event_records_after": new_count,
        "recurring_event_records_before": int((release.get("counts") or {}).get("recurring_event_records") or 0),
        "recurring_event_records_after": recurring_count,
        "follow_on_event_records_after": follow_on_count,
        "possible_historical_match_records_after": possible_match_count,
        "merged_event_ids": sorted(before_ids - after_ids),
        "effective_event_ids_added": sorted(after_ids - before_ids),
        "follow_on_event_records_before": int((release.get("counts") or {}).get("follow_on_event_records") or 0),
        "possible_historical_match_records_before": int((release.get("counts") or {}).get("possible_historical_match_event_records") or 0),
        "rediscovered_article_records_before": int((release.get("counts") or {}).get("rediscovered_article_records") or 0),
        "rediscovered_article_records_after": int((updated.get("counts") or {}).get("rediscovered_article_records") or 0),
        "replication_lag_count_before": int((((release.get("dynamics") or {}).get("replication_lag_days") or {}).get("count")) or 0),
        "replication_lag_count_after": int((((updated.get("dynamics") or {}).get("replication_lag_days") or {}).get("count")) or 0),
    }
    return updated, summary


def meaningful(summary: dict[str, Any]) -> bool:
    return bool(
        summary.get("coverage_articles_reassigned")
        or summary.get("event_record_change")
        or summary.get("new_event_records_before") != summary.get("new_event_records_after")
        or summary.get("recurring_event_records_before") != summary.get("recurring_event_records_after")
        or summary.get("follow_on_event_records_before") != summary.get("follow_on_event_records_after")
        or summary.get("possible_historical_match_records_before") != summary.get("possible_historical_match_records_after")
        or summary.get("rediscovered_article_records_before") != summary.get("rediscovered_article_records_after")
        or summary.get("replication_lag_count_before") != summary.get("replication_lag_count_after")
    )


def archive_path(release_id: str, revision: int) -> Path:
    return ARCHIVE_DIR / release_id / f"revision-{revision}.json"


def record_release_revision(
    client,
    *,
    release_id: str,
    from_revision: int,
    to_revision: int,
    reconciliation: dict[str, Any] | None,
    reason: str,
    summary: dict[str, Any],
) -> None:
    (
        client.table("release_revision_events")
        .upsert(
            {
                "release_id": release_id,
                "from_revision": from_revision,
                "to_revision": to_revision,
                "reconciliation_run_id": reconciliation.get("reconciliation_run_id") if reconciliation else None,
                "reason": reason,
                "change_summary": summary,
            },
            on_conflict="release_id,to_revision",
        )
        .execute()
    )


def restate_one(
    client,
    path: Path,
    *,
    registry: dict[str, dict[str, Any]],
    cmap: dict[str, str],
    reconciliation: dict[str, Any] | None,
    reason: str,
    dry_run: bool,
) -> dict[str, Any]:
    original = load_json(path)
    if not isinstance(original, dict):
        raise RestatementError(f"Invalid release file: {path}")
    updated, summary = recalculate(
        client, original, registry=registry, cmap=cmap, reconciliation=reconciliation
    )
    if not meaningful(summary):
        return {"release_id": original.get("release_id"), "changed": False, "summary": summary}

    old_revision = int(original.get("revision") or 1)
    new_revision = old_revision + 1
    changed_at = utc_now_iso()
    revision_entry = {
        "revision": new_revision,
        "changed_at": changed_at,
        "reason": reason,
        "change_summary": summary,
        "reconciliation_run_id": reconciliation.get("reconciliation_run_id") if reconciliation else None,
    }
    history = list(original.get("revision_history") or [])
    history.append(revision_entry)
    archive = archive_path(str(original["release_id"]), old_revision)
    updated.update(
        {
            "schema_version": "aieo_public_release_v3.0",
            "revision": new_revision,
            "generated_at": changed_at,
            "restated_at": changed_at,
            "revision_reason": reason,
            "revision_history": history,
            "previous_revision_path": "/" + str(archive.relative_to(ROOT)).replace("\\", "/"),
            "restatement_version": RESTATE_VERSION,
        }
    )
    updated["content_sha256"] = stable_hash(
        {key: value for key, value in updated.items() if key != "content_sha256"}
    )

    if not dry_run:
        if not archive.exists():
            write_json(archive, original)
        write_json(path, updated)
        current = load_json(CURRENT_RELEASE)
        if isinstance(current, dict) and current.get("release_id") == original.get("release_id"):
            write_json(CURRENT_RELEASE, updated)
        record_release_revision(
            client,
            release_id=str(original["release_id"]),
            from_revision=old_revision,
            to_revision=new_revision,
            reconciliation=reconciliation,
            reason=reason,
            summary=summary,
        )
    return {
        "release_id": original.get("release_id"),
        "changed": True,
        "from_revision": old_revision,
        "to_revision": new_revision,
        "summary": summary,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", help="Restate one release only.")
    parser.add_argument("--all", action="store_true", help="Inspect every standardized weekly release.")
    parser.add_argument(
        "--reason",
        default="Longitudinal reconciliation changed new-versus-recurring assignments.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = supabase_admin()
    registry = load_registry(client)
    cmap = canonical_map(registry)
    reconciliation = latest_reconciliation(client)

    if args.release_id:
        paths = [WEEKLY_DIR / f"{args.release_id}.json"]
    elif args.all:
        paths = sorted(path for path in WEEKLY_DIR.glob("*.json") if path.is_file())
    else:
        current = load_json(CURRENT_RELEASE)
        if not isinstance(current, dict) or not current.get("release_id"):
            raise RestatementError("No current weekly release exists.")
        paths = [WEEKLY_DIR / f"{current['release_id']}.json"]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise RestatementError("Missing release files: " + ", ".join(missing))

    results = [
        restate_one(
            client,
            path,
            registry=registry,
            cmap=cmap,
            reconciliation=reconciliation,
            reason=args.reason,
            dry_run=args.dry_run,
        )
        for path in paths
    ]
    if not args.dry_run:
        update_index()
    print(json.dumps({"dry_run": args.dry_run, "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RestatementError, ReleaseError) as exc:
        print(f"Release restatement failed: {exc}")
        raise SystemExit(1)
