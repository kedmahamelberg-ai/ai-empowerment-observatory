#!/usr/bin/env python3
"""Backfill the AIEO article-event occurrence ledger from stored history.

Run once after the Phase 5 migration and before the first live longitudinal
reconciliation. It reconstructs what the existing database already knows:

* the first observed coverage of an effective event;
* later articles about that event;
* exact article pages rediscovered in later collection runs;
* accepted follow-on developments in a continuing story family;
* first publisher and discovery-market appearances;
* coverage and replication delays.

The script never changes event assignments. It only writes missing occurrence
rows unless ``--replace`` is supplied. Re-running it is therefore safe.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from supabase import Client, create_client

ROOT = Path(__file__).resolve().parents[1]
WEEKLY_DIR = ROOT / "data" / "releases" / "weekly"
BACKFILL_VERSION = "occurrence_backfill_v1.1"
PRODUCTION_EVENT_METHOD = "article_to_event_v1"
PAGE_SIZE = 750


class BackfillError(RuntimeError):
    """The occurrence history could not be reconstructed safely."""


def required_secret() -> str:
    value = str(
        os.environ.get("SUPABASE_SECRET_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or ""
    ).strip()
    if not value:
        raise BackfillError("SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY is missing.")
    return value


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise BackfillError(f"{name} is missing.")
    return value


def parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def source_domain(url: Any) -> str | None:
    try:
        return (urlparse(str(url or "")).hostname or "").lower().removeprefix("www.") or None
    except ValueError:
        return None


def fetch_all(
    client: Client,
    table: str,
    columns: str,
    *,
    order_by: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        query = client.table(table).select(columns)
        if order_by:
            query = query.order(order_by, desc=False)
        batch = getattr(query.range(start, start + PAGE_SIZE - 1).execute(), "data", None) or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return rows


def canonical_map(events: dict[str, dict[str, Any]]) -> dict[str, str]:
    direct = {
        event_id: str(row["canonical_event_id"])
        for event_id, row in events.items()
        if row.get("canonical_event_id")
    }
    result: dict[str, str] = {}
    for event_id in events:
        current = event_id
        seen: set[str] = set()
        while current in direct:
            if current in seen:
                raise BackfillError(f"Canonical-event cycle detected at {current}")
            seen.add(current)
            current = direct[current]
        result[event_id] = current
    return result


def release_by_collection() -> dict[str, str]:
    result: dict[str, str] = {}
    if not WEEKLY_DIR.exists():
        return result
    for path in WEEKLY_DIR.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        collection_id = str((payload.get("lineage") or {}).get("collection_run_id") or "")
        release_id = str(payload.get("release_id") or "")
        if collection_id and release_id:
            result[collection_id] = release_id
    return result


def chunks(values: list[dict[str, Any]], size: int = 250) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Recalculate rows that already exist instead of keeping them unchanged.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client: Client = create_client(required_env("SUPABASE_URL"), required_secret())

    events = {
        str(row["event_id"]): row
        for row in fetch_all(
            client,
            "events",
            "event_id,canonical_event_id,story_family_id,first_seen_at,event_state,clustering_method",
        )
    }
    if not events:
        raise BackfillError("No event records are available.")
    cmap = canonical_map(events)

    links = fetch_all(client, "event_articles", "event_id,article_id")
    links_by_article: dict[str, set[str]] = defaultdict(set)
    for row in links:
        links_by_article[str(row["article_id"])].add(str(row["event_id"]))

    articles = {
        str(row["article_id"]): row
        for row in fetch_all(
            client,
            "articles",
            "article_id,publisher,canonical_url,published_at,first_seen_at",
        )
    }
    observations = fetch_all(
        client,
        "article_observations",
        "run_id,article_id,search_country_iso3,observed_at",
        order_by="observed_at",
    )
    collections = {
        str(row["run_id"]): row
        for row in fetch_all(
            client,
            "collection_runs",
            "run_id,run_key,started_at,completed_at,status",
        )
        if str(row.get("status") or "") in {"success", "partial"}
    }

    relationship_rows = fetch_all(
        client,
        "event_relationships",
        "from_event_id,to_event_id,story_family_id,relationship_type,confidence,status",
    )
    follow_on = {
        str(row["from_event_id"]): row
        for row in relationship_rows
        if row.get("relationship_type") == "follow_on_development"
        and row.get("status") == "accepted"
    }

    existing_rows = fetch_all(
        client,
        "event_occurrences",
        "collection_run_id,article_id,event_id,effective_event_id,appearance_type,"
        "observed_at,publisher,search_markets",
        order_by="observed_at",
    )
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in observations:
        run_id = str(row.get("run_id") or "")
        article_id = str(row.get("article_id") or "")
        observed = parse_datetime(row.get("observed_at"))
        if not run_id or not article_id or not observed or run_id not in collections:
            continue
        key = (run_id, article_id)
        item = grouped.setdefault(
            key,
            {"run_id": run_id, "article_id": article_id, "observed_at": observed, "markets": set()},
        )
        item["observed_at"] = min(item["observed_at"], observed)
        if row.get("search_country_iso3"):
            item["markets"].add(str(row["search_country_iso3"]))

    timeline: list[dict[str, Any]] = []
    skipped_ambiguous: list[dict[str, Any]] = []
    for item in grouped.values():
        article_id = item["article_id"]
        raw_ids = {
            event_id
            for event_id in links_by_article.get(article_id, set())
            if str((events.get(event_id) or {}).get("clustering_method") or "")
            == PRODUCTION_EVENT_METHOD
            and str((events.get(event_id) or {}).get("event_state") or "")
            in {"active", "pending_review"}
        }
        effective_ids = {cmap.get(event_id, event_id) for event_id in raw_ids}
        if not raw_ids:
            continue
        if len(effective_ids) != 1:
            skipped_ambiguous.append(
                {"article_id": article_id, "event_ids": sorted(raw_ids), "effective_event_ids": sorted(effective_ids)}
            )
            continue
        effective_id = next(iter(effective_ids))
        raw_id = effective_id if effective_id in raw_ids else sorted(raw_ids)[0]
        item.update({"raw_event_id": raw_id, "effective_event_id": effective_id})
        timeline.append(item)

    timeline.sort(key=lambda row: (row["observed_at"], row["run_id"], row["article_id"]))
    release_map = release_by_collection()

    seen_articles: set[str] = set()
    event_dates: dict[str, list[datetime]] = defaultdict(list)
    event_publishers: dict[str, set[str]] = defaultdict(set)
    event_markets: dict[str, set[str]] = defaultdict(set)
    derived_rows: list[dict[str, Any]] = []

    # Seed chronology with existing records, but still iterate all source rows so
    # new missing records are classified in their original temporal order.
    existing_by_key = {
        (str(row["collection_run_id"]), str(row["article_id"])): row
        for row in existing_rows
    }

    for item in timeline:
        run_id = item["run_id"]
        article_id = item["article_id"]
        raw_id = item["raw_event_id"]
        effective_id = item["effective_event_id"]
        observed_at = item["observed_at"]
        key = (run_id, article_id)
        article = articles.get(article_id) or {}
        publisher = clean(article.get("publisher")) or None
        markets = sorted(item["markets"])
        prior_event = event_dates[effective_id]
        previous = max(prior_event) if prior_event else None
        first = min(prior_event) if prior_event else observed_at

        accepted_follow_on = follow_on.get(raw_id) or follow_on.get(effective_id)
        if article_id in seen_articles:
            appearance = "same_article_rediscovered"
            track = "exact"
            confidence = 1.0
        elif accepted_follow_on and not prior_event:
            appearance = "follow_on_development"
            track = "reconciliation"
            confidence = accepted_follow_on.get("confidence")
        elif prior_event:
            appearance = "same_event_new_coverage"
            track = "historical"
            confidence = None
        else:
            appearance = "first_event_coverage"
            track = "historical"
            confidence = None

        existing = existing_by_key.get(key)
        if existing and not args.replace:
            appearance = str(existing.get("appearance_type") or appearance)

        days_since_first = max(0.0, (observed_at - first).total_seconds() / 86400.0)
        days_since_previous = (
            max(0.0, (observed_at - previous).total_seconds() / 86400.0)
            if previous else None
        )
        event = events.get(effective_id) or events.get(raw_id) or {}
        payload = {
            "event_id": raw_id,
            "effective_event_id": effective_id,
            "story_family_id": event.get("story_family_id"),
            "article_id": article_id,
            "collection_run_id": run_id,
            "release_id": release_map.get(run_id),
            "appearance_type": appearance,
            "article_published_at": article.get("published_at"),
            "observed_at": iso_z(observed_at),
            "previous_event_coverage_at": iso_z(previous) if previous else None,
            "days_since_event_first_seen": round(days_since_first, 3),
            "days_since_previous_coverage": round(days_since_previous, 3) if days_since_previous is not None else None,
            "publisher": publisher,
            "source_domain": source_domain(article.get("canonical_url")),
            "search_markets": markets,
            "first_source_appearance": bool(publisher and publisher not in event_publishers[effective_id]),
            "first_market_appearances": sorted(set(markets) - event_markets[effective_id]),
            "resolution_track": track,
            "relationship_confidence": (
                round(float(confidence), 4) if confidence is not None else None
            ),
            "resolver_version": BACKFILL_VERSION,
            "metadata": {
                "derived_from": "article_observations + event_articles",
                "backfill_version": BACKFILL_VERSION,
            },
            "updated_at": iso_z(datetime.now(timezone.utc)),
        }
        if not existing or args.replace:
            derived_rows.append(payload)

        seen_articles.add(article_id)
        event_dates[effective_id].append(observed_at)
        if publisher:
            event_publishers[effective_id].add(publisher)
        event_markets[effective_id].update(markets)

    if not args.dry_run:
        for batch in chunks(derived_rows):
            (
                client.table("event_occurrences")
                .upsert(batch, on_conflict="collection_run_id,article_id")
                .execute()
            )

    summary = {
        "dry_run": args.dry_run,
        "replace": args.replace,
        "source_observation_groups": len(grouped),
        "timeline_rows": len(timeline),
        "existing_occurrences": len(existing_rows),
        "rows_to_write": len(derived_rows),
        "ambiguous_articles_skipped": len(skipped_ambiguous),
        "appearance_counts": dict(
            sorted(
                {
                    value: sum(row["appearance_type"] == value for row in derived_rows)
                    for value in {
                        "first_event_coverage",
                        "same_event_new_coverage",
                        "same_article_rediscovered",
                        "follow_on_development",
                    }
                }.items()
            )
        ),
        "ambiguous_examples": skipped_ambiguous[:20],
    }
    print(json.dumps(summary, indent=2))
    if skipped_ambiguous:
        print(
            "WARNING: some articles map to more than one effective event and were not backfilled.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BackfillError as exc:
        print(f"Occurrence backfill failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
