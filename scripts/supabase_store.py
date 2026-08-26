#!/usr/bin/env python3
"""Private historical persistence for the AI Empowerment Observatory."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from supabase import Client, create_client


class SupabasePersistenceError(RuntimeError):
    """Raised when a required Supabase write cannot be completed."""


def stable_article_id(canonical_url: str) -> str:
    """Create a stable article key from the canonical URL only."""
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:32]


def valid_timestamp(value: Any) -> str | None:
    """Return an ISO timestamp only when Postgres can reasonably parse it."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return text


class SupabaseStore:
    """Write collection history to private Supabase tables and Storage."""

    def __init__(
        self,
        url: str,
        secret_key: str,
        bucket: str = "raw-news",
    ) -> None:
        if not url or not secret_key:
            raise SupabasePersistenceError(
                "SUPABASE_URL and SUPABASE_SECRET_KEY are required."
            )
        self.client: Client = create_client(url, secret_key)
        self.bucket = bucket

    @staticmethod
    def _first_row(response: Any, context: str) -> dict[str, Any]:
        data = getattr(response, "data", None)
        if not data:
            raise SupabasePersistenceError(
                f"Supabase returned no row while {context}."
            )
        if isinstance(data, list):
            return data[0]
        if isinstance(data, dict):
            return data
        raise SupabasePersistenceError(
            f"Unexpected Supabase response while {context}: {type(data)!r}"
        )

    def start_collection_run(
        self,
        *,
        run_key: str,
        started_at: str,
        configured_country_count: int,
        workflow_run_id: str | None,
        collector_version: str,
    ) -> str:
        response = (
            self.client.table("collection_runs")
            .insert(
                {
                    "run_key": run_key,
                    "started_at": started_at,
                    "status": "running",
                    "configured_country_count": configured_country_count,
                    "workflow_run_id": workflow_run_id,
                    "collector_version": collector_version,
                }
            )
            .select("run_id")
            .execute()
        )
        return str(
            self._first_row(response, "creating a collection run")["run_id"]
        )

    def finish_collection_run(
        self,
        *,
        run_id: str,
        completed_at: str,
        status: str,
        successful_search_count: int,
        failed_search_count: int,
        candidate_count: int,
    ) -> None:
        (
            self.client.table("collection_runs")
            .update(
                {
                    "completed_at": completed_at,
                    "status": status,
                    "successful_search_count": successful_search_count,
                    "failed_search_count": failed_search_count,
                    "candidate_count": candidate_count,
                }
            )
            .eq("run_id", run_id)
            .execute()
        )

    def create_search_run(
        self,
        *,
        run_id: str,
        country: dict[str, Any],
        status: str,
        result_count: int,
        raw_storage_path: str | None,
        serpapi_search_id: str | None,
        error_message: str | None = None,
    ) -> str:
        response = (
            self.client.table("search_runs")
            .insert(
                {
                    "run_id": run_id,
                    "country_name": country["country"],
                    "country_iso2": country["iso2"],
                    "country_iso3": country["iso3"],
                    "search_language": country["hl"],
                    "search_query": country["query"],
                    "result_count": result_count,
                    "status": status,
                    "error_message": error_message,
                    "raw_storage_path": raw_storage_path,
                    "serpapi_search_id": serpapi_search_id,
                }
            )
            .select("search_id")
            .execute()
        )
        return str(
            self._first_row(response, "creating a search run")["search_id"]
        )

    def upload_raw_json(
        self,
        *,
        object_path: str,
        payload: dict[str, Any],
    ) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.client.storage.from_(self.bucket).upload(
            path=object_path,
            file=body,
            file_options={
                "content-type": "application/json; charset=utf-8",
                "cache-control": "31536000",
                "upsert": "false",
            },
        )

    def persist_articles_and_observations(
        self,
        *,
        run_id: str,
        search_id: str,
        records: list[dict[str, Any]],
        observed_at: str,
    ) -> None:
        if not records:
            return

        # Deduplicate within one localized search before inserting observations.
        by_article_id: dict[str, dict[str, Any]] = {}
        for record in records:
            article_id = stable_article_id(record["link"])
            current = by_article_id.get(article_id)
            if current is None or int(record["search_rank"]) < int(
                current["search_rank"]
            ):
                enriched = dict(record)
                enriched["stable_article_id"] = article_id
                by_article_id[article_id] = enriched

        article_ids = list(by_article_id)
        existing_first_seen: dict[str, str] = {}
        existing_source_metadata: dict[str, dict[str, Any]] = {}
        for start in range(0, len(article_ids), 200):
            batch_ids = article_ids[start : start + 200]
            response = (
                self.client.table("articles")
                .select("article_id,first_seen_at,source_metadata")
                .in_("article_id", batch_ids)
                .execute()
            )
            for row in getattr(response, "data", None) or []:
                article_key = str(row["article_id"])
                existing_first_seen[article_key] = str(row["first_seen_at"])
                metadata = row.get("source_metadata")
                existing_source_metadata[article_key] = (
                    dict(metadata) if isinstance(metadata, dict) else {}
                )

        article_rows: list[dict[str, Any]] = []
        observation_rows: list[dict[str, Any]] = []

        for article_id, record in by_article_id.items():
            source_metadata = dict(existing_source_metadata.get(article_id, {}))
            observed_metadata = {
                "thumbnail": record.get("thumbnail"),
                "result_type": record.get("result_type"),
                "story_token": record.get("story_token"),
                "snippet": record.get("snippet"),
            }
            source_metadata.update(
                {key: value for key, value in observed_metadata.items() if value is not None}
            )

            article_rows.append(
                {
                    "article_id": article_id,
                    "canonical_url": record["link"],
                    "headline": record["title"],
                    "publisher": record.get("publisher"),
                    "published_at": valid_timestamp(record.get("iso_date")),
                    "displayed_date": record.get("displayed_date"),
                    "language": record.get("search_language"),
                    "first_seen_at": existing_first_seen.get(
                        article_id, observed_at
                    ),
                    "last_seen_at": observed_at,
                    "source_metadata": source_metadata,
                    "updated_at": observed_at,
                }
            )
            observation_rows.append(
                {
                    "run_id": run_id,
                    "search_id": search_id,
                    "article_id": article_id,
                    "search_country_iso3": record["search_country_iso3"],
                    "search_language": record["search_language"],
                    "search_rank": int(record["search_rank"]),
                    "observed_at": observed_at,
                    "observation_metadata": {
                        "search_country": record["search_country"],
                        "displayed_date": record.get("displayed_date"),
                        "candidate_id": record.get("id"),
                    },
                }
            )

        for start in range(0, len(article_rows), 100):
            (
                self.client.table("articles")
                .upsert(
                    article_rows[start : start + 100],
                    on_conflict="article_id",
                )
                .execute()
            )

        for start in range(0, len(observation_rows), 100):
            (
                self.client.table("article_observations")
                .insert(observation_rows[start : start + 100])
                .execute()
            )
