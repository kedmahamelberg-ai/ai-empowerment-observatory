#!/usr/bin/env python3
"""Collect real Google News candidates and preserve private history in Supabase.

Stage 7A.2 performs discovery, normalization, private raw archival, and
longitudinal article observation storage. It still does not classify
empowerment or publish country scores.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from supabase_store import SupabasePersistenceError, SupabaseStore

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "edu_countries.json"
DATA_DIR = ROOT / "data"
REVIEW_DIR = DATA_DIR / "review"
STATUS_PATH = DATA_DIR / "collection_status.json"

SERPAPI_ENDPOINT = "https://serpapi.com/search"
COLLECTOR_VERSION = "7A.4-retry-complete-market-gate"
REQUEST_ATTEMPTS = 3
RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}
TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
}


class CollectionError(RuntimeError):
    """Raised when collection cannot produce a usable run."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_title(value: Any) -> str:
    text = normalize_space(value).casefold()
    return re.sub(r"[^\w\s]", "", text)


def canonicalize_url(value: Any) -> str:
    raw = normalize_space(value)
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    filtered_query = [
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in TRACKING_PARAMS
    ]
    return urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            parts.path.rstrip("/"),
            urlencode(filtered_query),
            "",
        )
    )


def source_name(item: dict[str, Any]) -> str:
    source = item.get("source")
    if isinstance(source, dict):
        return normalize_space(source.get("name") or source.get("title"))
    return normalize_space(source)


def iter_news_items(items: Iterable[Any]) -> Iterable[dict[str, Any]]:
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("title") and item.get("link"):
            yield item
        highlight = item.get("highlight")
        if isinstance(highlight, dict):
            yield from iter_news_items([highlight])
        stories = item.get("stories")
        if isinstance(stories, list):
            yield from iter_news_items(stories)


def candidate_id(title: str, link: str) -> str:
    basis = f"{normalize_title(title)}|{canonicalize_url(link)}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


def normalize_item(
    item: dict[str, Any],
    country: dict[str, Any],
    collected_at: str,
    rank: int,
) -> dict[str, Any] | None:
    title = normalize_space(item.get("title"))
    link = canonicalize_url(item.get("link"))
    if not title or not link:
        return None
    return {
        "id": candidate_id(title, link),
        "title": title,
        "link": link,
        "publisher": source_name(item) or "Unknown source",
        "iso_date": normalize_space(item.get("iso_date")) or None,
        "displayed_date": normalize_space(item.get("date")) or None,
        "thumbnail": normalize_space(
            item.get("thumbnail_small") or item.get("thumbnail")
        )
        or None,
        "result_type": normalize_space(item.get("type")) or None,
        "story_token": normalize_space(item.get("story_token")) or None,
        "snippet": normalize_space(
            item.get("snippet")
            or item.get("description")
            or item.get("summary")
            or item.get("source_snippet")
        ) or None,
        "search_rank": rank,
        "search_country": country["country"],
        "search_country_iso2": country["iso2"],
        "search_country_iso3": country["iso3"],
        "search_language": country["hl"],
        "search_query": country["query"],
        "collected_at": collected_at,
        "review_status": "unreviewed",
        "ai_relevance": None,
        "event_country": None,
        "notes": "",
    }


def merge_current_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for record in records:
        key = record["id"]
        existing = merged.get(key)
        market = {
            "country": record["search_country"],
            "iso2": record["search_country_iso2"],
            "iso3": record["search_country_iso3"],
            "language": record["search_language"],
            "rank": record["search_rank"],
            "query": record["search_query"],
        }
        if existing is None:
            clean = dict(record)
            for removable in [
                "search_country",
                "search_country_iso2",
                "search_country_iso3",
                "search_language",
                "search_rank",
                "search_query",
            ]:
                clean.pop(removable, None)
            clean["matched_searches"] = [market]
            merged[key] = clean
        else:
            known = {
                (entry["iso2"], entry["language"])
                for entry in existing["matched_searches"]
            }
            if (market["iso2"], market["language"]) not in known:
                existing["matched_searches"].append(market)
    return sorted(
        merged.values(),
        key=lambda item: (item.get("iso_date") or "", item.get("title") or ""),
        reverse=True,
    )


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "id",
        "title",
        "publisher",
        "iso_date",
        "displayed_date",
        "link",
        "snippet",
        "matched_countries",
        "matched_languages",
        "collected_at",
        "review_status",
        "ai_relevance",
        "event_country",
        "notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            searches = record.get("matched_searches", [])
            writer.writerow(
                {
                    "id": record.get("id"),
                    "title": record.get("title"),
                    "publisher": record.get("publisher"),
                    "iso_date": record.get("iso_date"),
                    "displayed_date": record.get("displayed_date"),
                    "link": record.get("link"),
                    "snippet": record.get("snippet"),
                    "matched_countries": "; ".join(
                        sorted({entry["country"] for entry in searches})
                    ),
                    "matched_languages": "; ".join(
                        sorted({entry["language"] for entry in searches})
                    ),
                    "collected_at": record.get("collected_at"),
                    "review_status": record.get("review_status"),
                    "ai_relevance": record.get("ai_relevance"),
                    "event_country": record.get("event_country"),
                    "notes": record.get("notes"),
                }
            )


def request_country(
    country: dict[str, Any],
    api_key: str,
    collected_at: str,
    max_results: int,
    attempts: int = REQUEST_ATTEMPTS,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    params = {
        "engine": "google_news",
        "q": country["query"],
        "gl": country["gl"],
        "hl": country["hl"],
        "output": "json",
        "api_key": api_key,
    }
    response = None
    payload: dict[str, Any] = {}
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(SERPAPI_ENDPOINT, params=params, timeout=(10, 90))
        except requests.RequestException:
            if attempt >= attempts:
                raise
            time.sleep(6 * attempt)
            continue

        try:
            decoded = response.json()
            payload = decoded if isinstance(decoded, dict) else {}
        except ValueError:
            payload = {}

        if response.status_code in RETRYABLE_HTTP_STATUS and attempt < attempts:
            print(
                f"  transient SerpAPI HTTP {response.status_code}; "
                f"retrying ({attempt}/{attempts})..."
            )
            time.sleep(6 * attempt)
            continue
        break

    if response is None:
        raise RuntimeError("SerpAPI request produced no response.")
    if not response.ok:
        api_message = payload.get("error") if isinstance(payload, dict) else None
        if not api_message:
            api_message = response.text[:500] or "No response body"
        raise RuntimeError(
            f"SerpAPI returned HTTP {response.status_code}: {api_message}"
        )
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    metadata = payload.get("search_metadata") or {}
    status = normalize_space(metadata.get("status"))
    if status and status.casefold() != "success":
        raise RuntimeError(f"SerpAPI status was {status!r}")

    normalized: list[dict[str, Any]] = []
    for rank, item in enumerate(
        iter_news_items(payload.get("news_results") or []), start=1
    ):
        if len(normalized) >= max_results:
            break
        record = normalize_item(item, country, collected_at, rank)
        if record is not None:
            normalized.append(record)

    summary = {
        "country": country["country"],
        "iso3": country["iso3"],
        "language": country["hl"],
        "query": country["query"],
        "status": "success",
        "raw_result_count": len(payload.get("news_results") or []),
        "normalized_candidate_count": len(normalized),
        "serpapi_search_id": metadata.get("id"),
        "google_news_url": metadata.get("google_news_url"),
    }
    return normalized, summary, payload


def required_env(name: str) -> str:
    value = normalize_space(os.environ.get(name))
    if not value:
        raise CollectionError(f"{name} is missing from the workflow environment.")
    return value


def main() -> int:
    api_key = required_env("SERPAPI_KEY")
    supabase_url = required_env("SUPABASE_URL")
    supabase_secret_key = required_env("SUPABASE_SECRET_KEY")

    config = read_json(CONFIG_PATH)
    if not config or not config.get("countries"):
        raise CollectionError(f"Invalid or empty configuration: {CONFIG_PATH}")

    previous_status = read_json(STATUS_PATH, {}) or {}
    started = utc_now()
    collected_at = iso_z(started)
    run_date = started.date().isoformat()
    run_key = started.strftime("run_%Y%m%dT%H%M%SZ")
    workflow_run_id = normalize_space(os.environ.get("GITHUB_RUN_ID")) or None
    max_results = int(config["meta"].get("max_results_per_country", 30))
    request_attempts = max(1, int(config["meta"].get("request_attempts_per_market", REQUEST_ATTEMPTS)))

    store = SupabaseStore(supabase_url, supabase_secret_key)
    run_id = store.start_collection_run(
        run_key=run_key,
        started_at=collected_at,
        configured_country_count=len(config["countries"]),
        workflow_run_id=workflow_run_id,
        collector_version=COLLECTOR_VERSION,
    )

    all_records: list[dict[str, Any]] = []
    search_summaries: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    try:
        for country in config["countries"]:
            print(f"Collecting {country['country']} ({country['hl']})...")
            try:
                records, summary, payload = request_country(
                    country=country,
                    api_key=api_key,
                    collected_at=collected_at,
                    max_results=max_results,
                    attempts=request_attempts,
                )
                object_path = (
                    f"serpapi/google-news/{started:%Y/%m/%d}/"
                    f"{run_key}/{country['iso3'].lower()}.json"
                )
                store.upload_raw_json(object_path=object_path, payload=payload)
                search_id = store.create_search_run(
                    run_id=run_id,
                    country=country,
                    status="success",
                    result_count=len(records),
                    raw_storage_path=object_path,
                    serpapi_search_id=summary.get("serpapi_search_id"),
                )
                store.persist_articles_and_observations(
                    run_id=run_id,
                    search_id=search_id,
                    records=records,
                    observed_at=collected_at,
                )
                summary["raw_storage_path"] = object_path
                all_records.extend(records)
                search_summaries.append(summary)
                print(f"  {len(records)} candidates persisted")
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                print(f"  ERROR: {message}", file=sys.stderr)
                errors.append(
                    {
                        "country": country["country"],
                        "iso3": country["iso3"],
                        "message": message,
                    }
                )
                try:
                    store.create_search_run(
                        run_id=run_id,
                        country=country,
                        status="error",
                        result_count=0,
                        raw_storage_path=None,
                        serpapi_search_id=None,
                        error_message=message,
                    )
                except Exception as storage_exc:
                    print(
                        f"  Could not record failed search: {storage_exc}",
                        file=sys.stderr,
                    )
                search_summaries.append(
                    {
                        "country": country["country"],
                        "iso3": country["iso3"],
                        "language": country["hl"],
                        "query": country["query"],
                        "status": "error",
                        "message": message,
                    }
                )

        successful_searches = sum(
            1 for item in search_summaries if item.get("status") == "success"
        )
        current = merge_current_records(all_records)

        if successful_searches == 0:
            final_status = "failed"
        elif errors:
            final_status = "partial"
        else:
            final_status = "success"

        completed_at = iso_z(utc_now())
        store.finish_collection_run(
            run_id=run_id,
            completed_at=completed_at,
            status=final_status,
            successful_search_count=successful_searches,
            failed_search_count=len(errors),
            candidate_count=len(current),
        )

        if successful_searches == 0:
            raise CollectionError("All configured country searches failed.")

        latest_payload = {
            "meta": {
                "stage": "7A.2",
                "status": "unclassified review candidates",
                "run_key": run_key,
                "run_id": run_id,
                "collected_at": collected_at,
                "run_date": run_date,
                "successful_searches": successful_searches,
                "configured_searches": len(config["countries"]),
                "candidate_count_before_global_deduplication": len(all_records),
                "candidate_count_after_global_deduplication": len(current),
                "private_history": "Supabase PostgreSQL + private raw-news bucket",
                "warning": (
                    "These are discovery results, not validated AI-empowerment "
                    "events. Search market does not establish event country."
                ),
            },
            "searches": search_summaries,
            "errors": errors,
            "candidates": current,
        }
        write_json(REVIEW_DIR / "latest.json", latest_payload)
        write_csv(REVIEW_DIR / "latest.csv", current)

        status_payload = {
            "stage": "7A.2",
            "last_attempt_at": completed_at,
            "last_success_at": (
                completed_at
                if final_status == "success"
                else previous_status.get("last_success_at")
            ),
            "run_key": run_key,
            "run_id": run_id,
            "run_date": run_date,
            "configured_searches": len(config["countries"]),
            "successful_searches": successful_searches,
            "failed_searches": len(errors),
            "latest_candidate_count": len(current),
            "private_storage": "Supabase",
            "countries": search_summaries,
            "publication_state": "review only; no automated empowerment scores",
        }
        write_json(STATUS_PATH, status_payload)

        require_all_markets = bool(config.get("meta", {}).get("require_all_markets", True))
        if errors and require_all_markets:
            raise CollectionError(
                f"{len(errors)} of {len(config['countries'])} configured market searches failed. "
                "The partial run was preserved privately, but weekly publication is blocked."
            )

        print()
        print(f"Supabase collection run: {run_key} ({run_id})")
        print(f"Successful searches: {successful_searches}/{len(config['countries'])}")
        print(f"Current unique candidates: {len(current)}")
        print(f"Public review JSON: {REVIEW_DIR / 'latest.json'}")
        return 0
    except Exception:
        try:
            store.finish_collection_run(
                run_id=run_id,
                completed_at=iso_z(utc_now()),
                status="failed",
                successful_search_count=sum(
                    1
                    for item in search_summaries
                    if item.get("status") == "success"
                ),
                failed_search_count=max(1, len(errors)),
                candidate_count=len(merge_current_records(all_records)),
            )
        except Exception as finalization_exc:
            print(
                f"Could not finalize failed Supabase run: {finalization_exc}",
                file=sys.stderr,
            )
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CollectionError, SupabasePersistenceError) as exc:
        print(f"Collection failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
