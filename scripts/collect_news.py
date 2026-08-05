#!/usr/bin/env python3
"""Collect weekly Google News candidates through SerpAPI.

Stage 7A deliberately performs discovery and normalization only.
It does not publish empowerment classifications or country scores.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "edu_countries.json"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
REVIEW_DIR = DATA_DIR / "review"
ARCHIVE_PATH = DATA_DIR / "news_candidates_archive.json"
STATUS_PATH = DATA_DIR / "collection_status.json"

SERPAPI_ENDPOINT = "https://serpapi.com/search"
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
    """Raised when no configured search can be completed."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
    """Yield story-shaped records from SerpAPI's nested news structures."""
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


def record_id(title: str, link: str) -> str:
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

    source = source_name(item) or "Unknown source"
    iso_date = normalize_space(item.get("iso_date"))
    displayed_date = normalize_space(item.get("date"))
    story_token = normalize_space(item.get("story_token"))
    result_type = normalize_space(item.get("type"))

    return {
        "id": record_id(title, link),
        "title": title,
        "link": link,
        "publisher": source,
        "iso_date": iso_date or None,
        "displayed_date": displayed_date or None,
        "thumbnail": normalize_space(
            item.get("thumbnail_small") or item.get("thumbnail")
        )
        or None,
        "result_type": result_type or None,
        "story_token": story_token or None,
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
    """Deduplicate the current run while retaining every search market match."""
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
            existing_markets = {
                (entry["iso2"], entry["language"])
                for entry in existing["matched_searches"]
            }
            if (market["iso2"], market["language"]) not in existing_markets:
                existing["matched_searches"].append(market)

    return sorted(
        merged.values(),
        key=lambda item: (
            item.get("iso_date") or "",
            item.get("title") or "",
        ),
        reverse=True,
    )


def merge_archive(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
    collected_at: str,
) -> list[dict[str, Any]]:
    archive = {item["id"]: dict(item) for item in previous if item.get("id")}

    for item in current:
        existing = archive.get(item["id"])
        if existing is None:
            created = dict(item)
            created["first_seen"] = collected_at
            created["last_seen"] = collected_at
            archive[item["id"]] = created
            continue

        existing["last_seen"] = collected_at
        existing["title"] = item["title"]
        existing["link"] = item["link"]
        existing["publisher"] = item["publisher"]
        existing["iso_date"] = item.get("iso_date")
        existing["displayed_date"] = item.get("displayed_date")
        existing["thumbnail"] = item.get("thumbnail")

        known_markets = {
            (entry["iso2"], entry["language"])
            for entry in existing.get("matched_searches", [])
        }
        for market in item.get("matched_searches", []):
            if (market["iso2"], market["language"]) not in known_markets:
                existing.setdefault("matched_searches", []).append(market)
                known_markets.add((market["iso2"], market["language"]))

    return sorted(
        archive.values(),
        key=lambda item: (
            item.get("last_seen") or "",
            item.get("iso_date") or "",
        ),
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


def collect_country(
    country: dict[str, Any],
    api_key: str,
    collected_at: str,
    raw_run_dir: Path,
    max_results: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    params = {
        "engine": "google_news",
        "q": country["query"],
        "gl": country["gl"],
        "hl": country["hl"],
        "so": "1",
        "output": "json",
        "api_key": api_key,
    }

    response = requests.get(
        SERPAPI_ENDPOINT,
        params=params,
        timeout=(10, 90),
    )
    response.raise_for_status()
    payload = response.json()

    raw_path = raw_run_dir / f"{country['iso3'].lower()}.json"
    write_json(raw_path, payload)

    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))

    metadata = payload.get("search_metadata") or {}
    status = normalize_space(metadata.get("status"))
    if status and status.casefold() != "success":
        raise RuntimeError(f"SerpAPI status was {status!r}")

    normalized: list[dict[str, Any]] = []
    for rank, item in enumerate(
        iter_news_items(payload.get("news_results") or []),
        start=1,
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
    return normalized, summary


def main() -> int:
    api_key = normalize_space(os.environ.get("SERPAPI_KEY"))
    if not api_key:
        print(
            "SERPAPI_KEY is missing. Add it as a GitHub Actions repository secret.",
            file=sys.stderr,
        )
        return 2

    config = read_json(CONFIG_PATH)
    if not config or not config.get("countries"):
        print(f"Invalid or empty configuration: {CONFIG_PATH}", file=sys.stderr)
        return 2

    now = utc_now()
    collected_at = now.isoformat().replace("+00:00", "Z")
    run_date = now.date().isoformat()
    max_results = int(config["meta"].get("max_results_per_country", 30))
    raw_run_dir = RAW_DIR / run_date

    all_records: list[dict[str, Any]] = []
    search_summaries: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for country in config["countries"]:
        print(f"Collecting {country['country']} ({country['hl']})...")
        try:
            records, summary = collect_country(
                country=country,
                api_key=api_key,
                collected_at=collected_at,
                raw_run_dir=raw_run_dir,
                max_results=max_results,
            )
            all_records.extend(records)
            search_summaries.append(summary)
            print(f"  {len(records)} normalized candidates")
        except Exception as exc:  # keep partial runs usable
            message = f"{type(exc).__name__}: {exc}"
            print(f"  ERROR: {message}", file=sys.stderr)
            errors.append(
                {
                    "country": country["country"],
                    "iso3": country["iso3"],
                    "message": message,
                }
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
    if successful_searches == 0:
        raise CollectionError("All configured country searches failed.")

    current = merge_current_records(all_records)
    previous_archive = read_json(ARCHIVE_PATH, default=[]) or []
    archive = merge_archive(previous_archive, current, collected_at)

    latest_payload = {
        "meta": {
            "stage": "7A",
            "status": "unclassified review candidates",
            "collected_at": collected_at,
            "run_date": run_date,
            "successful_searches": successful_searches,
            "configured_searches": len(config["countries"]),
            "candidate_count_before_global_deduplication": len(all_records),
            "candidate_count_after_global_deduplication": len(current),
            "warning": (
                "These are discovery results, not validated AI-empowerment "
                "events. Search market does not establish event country."
            ),
        },
        "searches": search_summaries,
        "errors": errors,
        "candidates": current,
    }

    history_dir = REVIEW_DIR / "history"
    write_json(REVIEW_DIR / "latest.json", latest_payload)
    write_json(history_dir / f"{run_date}.json", latest_payload)
    write_csv(REVIEW_DIR / "latest.csv", current)
    write_csv(history_dir / f"{run_date}.csv", current)
    write_json(ARCHIVE_PATH, archive)

    status_payload = {
        "stage": "7A",
        "last_attempt_at": collected_at,
        "last_success_at": collected_at,
        "run_date": run_date,
        "configured_searches": len(config["countries"]),
        "successful_searches": successful_searches,
        "failed_searches": len(errors),
        "latest_candidate_count": len(current),
        "archive_candidate_count": len(archive),
        "countries": search_summaries,
        "publication_state": "review only; no automated empowerment scores",
    }
    write_json(STATUS_PATH, status_payload)

    print()
    print(f"Successful searches: {successful_searches}/{len(config['countries'])}")
    print(f"Current unique candidates: {len(current)}")
    print(f"Archive candidates: {len(archive)}")
    print(f"Review JSON: {REVIEW_DIR / 'latest.json'}")
    print(f"Review CSV:  {REVIEW_DIR / 'latest.csv'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CollectionError as exc:
        print(f"Collection failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
