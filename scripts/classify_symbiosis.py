#!/usr/bin/env python3
"""Classify weekly AIEO coverage and developments with a two-sided symbiosis lens.

The unit of interpretation is release-specific:

- Coverage Lens: one published page represented in a weekly release.
- Event Lens: one resolved development as represented by the source evidence in
  that same weekly release.

This matters longitudinally. A stable event can receive different framing in a
later week, so Event Lens unit keys include the release ID. Every model result is written with review_status=pending. The live Observatory can
display the current source-evidence reading, while optional owner quality control can
accept or correct sampled rows and add those adjudications to the gold set.

Scopes:
- latest: the current weekly release JSON;
- history: the next unclassified units across all standardized weekly releases.

The classifier describes source representations. It does not claim objective
system performance, consciousness, intentions, or biological fitness.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from huggingface_hub import HfApi
from supabase import Client, create_client

from brief_content_common import MIN_FULL_BODY_EVIDENCE_UNITS, evidence_unit_count
from symbiosis_common import (
    CLASSIFIER_VERSION,
    CODEBOOK_VERSION,
    EVIDENCE_POLICY_VERSION,
    classification_input_evidence,
    content_basis_for_storage,
    evidence_basis_covers,
    release_identifier,
    release_review_scope,
    validate_model_payload,
)
from translation_policy import SUPPORTED_TRANSLATION_PROFILES, preferred_translation_rows

ROOT = Path(__file__).resolve().parents[1]
RELEASES_DIR = ROOT / "data" / "releases"
OUTPUT_PATH = ROOT / "review" / "symbiosis" / "latest.json"

QWEN_REPO = os.environ.get("SYMBIOSIS_QWEN_REPO", "Qwen/Qwen3-4B-GGUF")
QWEN_QUANT = os.environ.get("SYMBIOSIS_QWEN_QUANT", "Q4_K_M")
LLAMA_SERVER_BIN = os.environ.get(
    "LLAMA_SERVER_BIN",
    "/tmp/llama.cpp/build/bin/llama-server",
)
SERVER_URL = "http://127.0.0.1:8080/v1/chat/completions"
HEALTH_URL = "http://127.0.0.1:8080/health"
OWNER_GOLD_PATH = ROOT / "validation" / "symbiosis-owner-gold.json"
MAX_ARTICLE_EVIDENCE_CHARS = 14000
MAX_EVENT_EVIDENCE_CHARS = 22000
MIN_FULL_TEXT_WORDS = MIN_FULL_BODY_EVIDENCE_UNITS
FULL_BODY_REQUIRED_POLICY = "full_article_body_required_v1"


class SymbiosisClassificationError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise SymbiosisClassificationError(f"{name} is missing.")
    return value


def first_row(response: Any, context: str) -> dict[str, Any]:
    data = getattr(response, "data", None)
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict) and data:
        return data
    raise SymbiosisClassificationError(f"Supabase returned no row while {context}.")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SymbiosisClassificationError(f"Missing release JSON: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SymbiosisClassificationError(f"Release file is not a JSON object: {path}")
    return payload


def current_release_path(release_id: str = "") -> Path:
    if release_id:
        return RELEASES_DIR / "weekly" / f"{release_id}.json"
    return RELEASES_DIR / "current.json"


def historical_release_paths(release_id: str = "") -> list[Path]:
    roots = [RELEASES_DIR / "baselines", RELEASES_DIR / "weekly"]
    if release_id:
        for root in roots:
            path = root / f"{release_id}.json"
            if path.exists():
                return [path]
        return []
    paths = [path for root in roots for path in root.glob("*.json") if path.is_file()]
    return sorted(paths, key=lambda path: (path.parent.name, path.stem))


def selected_releases(
    scope: str,
    release_id: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    paths = [current_release_path(release_id)] if scope == "latest" else historical_release_paths(release_id)
    releases: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for path in paths:
        payload = read_json(path)
        source_path = str(path.relative_to(ROOT))
        stable_id = release_identifier(payload, path)
        if not stable_id:
            raise SymbiosisClassificationError(f"Publication lacks a stable identifier: {path}")

        if scope == "latest" and payload.get("release_type") != "weekly":
            continue

        payload["release_id"] = stable_id
        payload["_source_path"] = source_path
        review_scope = release_review_scope(payload, source_path)
        if not review_scope["reviewable"]:
            if scope == "latest":
                raise SymbiosisClassificationError(
                    "Current weekly release has no item-level coverage or event evidence: "
                    f"{path}"
                )
            skipped.append(review_scope)
            print(
                "Skipping publication reference without item-level evidence: "
                f"{stable_id} ({review_scope['reason']})",
                flush=True,
            )
            continue

        releases.append(payload)

    if not releases and not skipped:
        raise SymbiosisClassificationError("No publication files were selected.")
    return releases, skipped


def paged_table(
    client: Client,
    table: str,
    select: str,
    *,
    page_size: int = 1000,
    apply: Any | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        query = client.table(table).select(select)
        if apply is not None:
            query = apply(query)
        response = query.range(start, start + page_size - 1).execute()
        batch = getattr(response, "data", None) or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def load_translation_map(client: Client, article_ids: list[str]) -> dict[str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for start in range(0, len(article_ids), 150):
        response = (
            client.table("article_translations")
            .select(
                "article_id,source_language_iso2,translated_headline,"
                "translation_profile,created_at"
            )
            .in_("translation_profile", list(SUPPORTED_TRANSLATION_PROFILES))
            .in_("article_id", article_ids[start:start + 150])
            .order("created_at", desc=True)
            .execute()
        )
        rows.extend(getattr(response, "data", None) or [])
    return preferred_translation_rows(rows)


def parse_metadata(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def compact_evidence_text(value: Any, max_chars: int = MAX_ARTICLE_EVIDENCE_CHARS) -> str:
    text = re.sub(r"[ \t]+", " ", str(value or ""))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= max_chars:
        return text
    head_chars = int(max_chars * 0.72)
    tail_chars = max_chars - head_chars
    return f"{text[:head_chars].rstrip()}\n\n[Middle shortened for classification]\n\n{text[-tail_chars:].lstrip()}"


def load_full_text_map(client: Client, article_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Load the best current legally collected article body for each source."""
    rows: list[dict[str, Any]] = []
    for start in range(0, len(article_ids), 150):
        response = (
            client.table("brief_article_content_snapshots")
            .select("article_id,body_text,word_count,extraction_quality,retrieval_method,retrieved_at")
            .eq("is_current", True)
            .in_("article_id", article_ids[start:start + 150])
            .execute()
        )
        rows.extend(getattr(response, "data", None) or [])

    rows.sort(
        key=lambda row: (
            float(row.get("extraction_quality") or 0),
            int(row.get("word_count") or 0),
            str(row.get("retrieved_at") or ""),
        ),
        reverse=True,
    )
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        article_id = str(row.get("article_id") or "")
        body = compact_evidence_text(row.get("body_text"))
        words = int(row.get("word_count") or evidence_unit_count(body))
        if article_id and article_id not in result and body and words >= MIN_FULL_TEXT_WORDS:
            result[article_id] = {**row, "body_text": body, "word_count": words}
    return result


def evidence_from_metadata(metadata: dict[str, Any]) -> tuple[str, str]:
    for key, basis in (
        ("human_evidence_summary", "article_summary"),
        ("article_summary", "article_summary"),
        ("summary", "article_summary"),
        ("snippet", "headline_and_snippet"),
        ("description", "headline_and_snippet"),
        ("source_snippet", "headline_and_snippet"),
    ):
        value = metadata.get(key)
        if value and str(value).strip():
            return str(value).strip(), basis
    return "", "headline_only"


def evidence_basis_summary(articles: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for article in articles:
        counts[str(article.get("content_basis") or "headline_only")] += 1
    total = len(articles)
    full = counts.get("full_text", 0)
    return {
        "source_count": total,
        "full_text_sources": full,
        "article_summary_sources": counts.get("article_summary", 0),
        "snippet_sources": counts.get("headline_and_snippet", 0),
        "headline_only_sources": counts.get("headline_only", 0),
        "not_available_sources": counts.get("not_available", 0),
        "input_policy": FULL_BODY_REQUIRED_POLICY,
        "body_coverage": (
            "all_sources" if total and full == total
            else "some_sources" if full
            else "no_full_body"
        ),
    }


def article_ids_from_release(release: dict[str, Any]) -> list[str]:
    found: set[str] = set()
    for row in release.get("units", {}).get("coverage_articles", []) or []:
        if isinstance(row, dict) and row.get("article_id"):
            found.add(str(row["article_id"]))
    for event in release.get("evidence") or []:
        if not isinstance(event, dict):
            continue
        for article_id in event.get("member_article_ids") or []:
            if article_id:
                found.add(str(article_id))
        for source in event.get("sources") or []:
            if isinstance(source, dict) and source.get("article_id"):
                found.add(str(source["article_id"]))
    return sorted(found)


def load_observation_meta(client: Client, article_ids: list[str]) -> dict[str, dict[str, Any]]:
    meta: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"search_markets": set(), "search_languages": set(), "min_rank": 9999}
    )
    for start in range(0, len(article_ids), 150):
        response = (
            client.table("article_observations")
            .select("article_id,search_country_iso3,search_language,search_rank")
            .in_("article_id", article_ids[start:start + 150])
            .execute()
        )
        for row in getattr(response, "data", None) or []:
            article_id = str(row["article_id"])
            if row.get("search_country_iso3"):
                meta[article_id]["search_markets"].add(str(row["search_country_iso3"]))
            if row.get("search_language"):
                meta[article_id]["search_languages"].add(str(row["search_language"]))
            if row.get("search_rank") is not None:
                meta[article_id]["min_rank"] = min(meta[article_id]["min_rank"], int(row["search_rank"]))
    return meta


def load_articles(client: Client, article_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not article_ids:
        return {}
    rows: list[dict[str, Any]] = []
    for start in range(0, len(article_ids), 150):
        response = (
            client.table("articles")
            .select(
                "article_id,canonical_url,headline,publisher,published_at,displayed_date,"
                "language,first_seen_at,last_seen_at,source_metadata"
            )
            .in_("article_id", article_ids[start:start + 150])
            .execute()
        )
        rows.extend(getattr(response, "data", None) or [])
    translations = load_translation_map(client, article_ids)
    observation_meta = load_observation_meta(client, article_ids)
    full_text_map = load_full_text_map(client, article_ids)
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        article_id = str(row["article_id"])
        original = str(row.get("headline") or "").strip()
        if not original:
            continue
        translation = translations.get(article_id) or {}
        metadata = parse_metadata(row.get("source_metadata"))
        evidence_text, content_basis = evidence_from_metadata(metadata)
        full_text = full_text_map.get(article_id) or {}
        # Relationship labels are deliberately full-body-only. A discovery
        # summary remains useful operational metadata, but cannot become model
        # evidence when the publisher body was not collected.
        if full_text.get("body_text"):
            evidence_text = str(full_text["body_text"])
            content_basis = "full_text"
        else:
            evidence_text = ""
            content_basis = "not_available"
        result[article_id] = {
            "article_id": article_id,
            "headline_original": original,
            "headline_english": str(translation.get("translated_headline") or original).strip(),
            "publisher": str(row.get("publisher") or "Unknown source"),
            "url": str(row.get("canonical_url") or ""),
            "date": str(row.get("published_at") or row.get("first_seen_at") or ""),
            "source_language": str(translation.get("source_language_iso2") or row.get("language") or "und"),
            "evidence_text": evidence_text,
            "content_basis": content_basis,
            "evidence_word_count": int(full_text.get("word_count") or 0),
            "retrieval_method": str(full_text.get("retrieval_method") or ""),
            "search_markets": sorted(observation_meta[article_id]["search_markets"]),
            "search_languages": sorted(observation_meta[article_id]["search_languages"]),
            "min_rank": observation_meta[article_id]["min_rank"],
        }
    return result


def fallback_article(source: dict[str, Any]) -> dict[str, Any] | None:
    article_id = str(source.get("article_id") or "").strip()
    headline = str(source.get("headline") or "").strip()
    if not article_id or not headline:
        return None
    return {
        "article_id": article_id,
        "headline_original": headline,
        "headline_english": headline,
        "publisher": str(source.get("publisher") or "Unknown source"),
        "url": str(source.get("url") or ""),
        "date": str(source.get("published_date") or ""),
        "source_language": str(source.get("source_language") or "und"),
        "evidence_text": "",
        "content_basis": "not_available",
        "search_markets": [],
        "search_languages": [],
        "min_rank": 9999,
    }


def release_units(
    client: Client,
    release: dict[str, Any],
    *,
    lens: str,
) -> list[tuple[str, dict[str, Any]]]:
    release_id = str(release["release_id"])
    period_start = str(release.get("period_start") or "")
    period_end = str(release.get("period_end") or "")
    article_ids = article_ids_from_release(release)
    article_map = load_articles(client, article_ids)

    # Fill a missing private article row from the deliberately public release
    # evidence so that the review queue remains complete and auditable.
    for event in release.get("evidence") or []:
        if not isinstance(event, dict):
            continue
        for source in event.get("sources") or []:
            if not isinstance(source, dict):
                continue
            article_id = str(source.get("article_id") or "")
            if article_id and article_id not in article_map:
                fallback = fallback_article(source)
                if fallback:
                    article_map[article_id] = fallback

    units: list[tuple[str, dict[str, Any]]] = []
    if lens in {"both", "coverage"}:
        included_ids: set[str] = set()
        coverage_rows = release.get("units", {}).get("coverage_articles", []) or []
        for row in coverage_rows:
            if not isinstance(row, dict) or not row.get("article_id"):
                continue
            if row.get("classification", {}).get("ai_relevant") is False:
                continue
            included_ids.add(str(row["article_id"]))
        if not coverage_rows:
            included_ids.update(article_map)
        for article_id in sorted(included_ids):
            article = article_map.get(article_id)
            if not article:
                continue
            units.append(
                (
                    "coverage",
                    {
                        **article,
                        "unit_key": f"coverage:{release_id}:{article_id}",
                        "release_id": release_id,
                        "period_start": period_start,
                        "period_end": period_end,
                        "evidence_basis_summary": evidence_basis_summary([article]),
                    },
                )
            )

    if lens in {"both", "event"}:
        for event in release.get("evidence") or []:
            if not isinstance(event, dict):
                continue
            if event.get("classification", {}).get("ai_relevant") is False:
                continue
            event_id = str(event.get("effective_event_id") or event.get("event_id") or "").strip()
            if not event_id:
                continue
            member_ids = [str(value) for value in (event.get("member_article_ids") or []) if value]
            if not member_ids:
                member_ids = [
                    str(source.get("article_id"))
                    for source in (event.get("sources") or [])
                    if isinstance(source, dict) and source.get("article_id")
                ]
            members = [article_map[value] for value in member_ids if value in article_map]
            if not members:
                continue
            event_summary = str(event.get("event_summary") or "").strip()
            full_text_members = [
                article
                for article in members
                if str(article.get("content_basis") or "") == "full_text"
            ]
            # Do not give the model an event summary that may itself have been
            # created from headlines. A multi-source event can be classified
            # only from the source bodies that are actually stored.
            if not full_text_members:
                content_basis = "not_available"
            elif len(full_text_members) == 1:
                content_basis = "full_text"
            else:
                content_basis = "multiple_sources"
            units.append(
                (
                    "event",
                    {
                        "unit_key": f"event:{release_id}:{event_id}",
                        "release_id": release_id,
                        "period_start": period_start,
                        "period_end": period_end,
                        "event_id": event_id,
                        "event_title": str(event.get("event_title") or "Untitled development"),
                        "event_summary": event_summary,
                        "event_date": str(event.get("event_date") or period_end),
                        "content_basis": content_basis,
                        "evidence_basis_summary": evidence_basis_summary(members),
                        "member_articles": members,
                        "model_member_articles": full_text_members,
                    },
                )
            )
    return units


def successful_existing_unit_keys(
    client: Client,
    units: list[tuple[str, dict[str, Any]]],
) -> set[str]:
    """Return only successful rows that cover the unit's current evidence.

    A matching unit key is not enough: when a full body arrives after a
    headline-only classification, the old success must be replaced.
    """
    current_by_key = {str(unit["unit_key"]): unit for _, unit in units}
    rows: list[dict[str, Any]] = []
    unit_keys = sorted(current_by_key)
    for start in range(0, len(unit_keys), 100):
        selected_keys = unit_keys[start:start + 100]
        rows.extend(
            paged_table(
                client,
                "symbiosis_classifications",
                "unit_key,content_basis,raw_output,codebook_version,symbiosis_run_id,"
                "symbiosis_classification_runs!inner(status,classifier_version)",
                apply=lambda query, keys=selected_keys: (
                    query.eq("codebook_version", CODEBOOK_VERSION)
                    .eq("symbiosis_classification_runs.status", "success")
                    .eq("symbiosis_classification_runs.classifier_version", CLASSIFIER_VERSION)
                    .in_("unit_key", keys)
                ),
            )
        )
    existing: set[str] = set()
    for row in rows:
        unit_key = str(row.get("unit_key") or "")
        current = current_by_key.get(unit_key)
        if not current:
            continue
        stored_basis, stored_summary = classification_input_evidence(row)
        if evidence_basis_covers(
            stored_content_basis=stored_basis,
            stored_evidence_summary=stored_summary,
            current_content_basis=current.get("content_basis"),
            current_evidence_summary=current.get("evidence_basis_summary"),
        ):
            existing.add(unit_key)
    return existing


def start_run(
    client: Client,
    *,
    scope: str,
    target_release_id: str | None,
    collection_run_id: str | None,
    empowerment_run_id: str | None,
    model_revision: str,
) -> tuple[str, str]:
    now = utc_now()
    run_key = now.strftime("symbiosis_%Y%m%dT%H%M%SZ")
    response = (
        client.table("symbiosis_classification_runs")
        .insert(
            {
                "run_key": run_key,
                "scope": scope,
                "target_release_id": target_release_id,
                "collection_run_id": collection_run_id,
                "empowerment_classification_run_id": empowerment_run_id,
                "started_at": iso_z(now),
                "status": "running",
                "classifier_version": CLASSIFIER_VERSION,
                "codebook_version": CODEBOOK_VERSION,
                "evidence_policy_version": EVIDENCE_POLICY_VERSION,
                "model_name": QWEN_REPO,
                "model_revision": model_revision,
                "notes": "Release-specific relationship classifications. The classifier uses stored full article bodies only; unavailable bodies receive a transparent non-model evidence state.",
            }
        )
        .select("symbiosis_run_id")
        .execute()
    )
    return str(first_row(response, "starting symbiosis run")["symbiosis_run_id"]), run_key


def run_progress_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarise durable rows already written for a symbiosis run."""
    configurations = [str(row.get("model_configuration") or "") for row in rows]
    complete = {"mutualism", "ai_benefiting_parasitism", "human_benefiting_parasitism", "competition"}
    partial = {"human_enabling_only", "human_constraining_only", "ai_enabling_only", "ai_constraining_only"}
    return {
        "coverage_unit_count": sum(1 for row in rows if row.get("lens") == "coverage"),
        "event_unit_count": sum(1 for row in rows if row.get("lens") == "event"),
        "complete_configuration_count": sum(value in complete for value in configurations),
        "partial_signal_count": sum(value in partial for value in configurations),
        "no_clear_signal_count": configurations.count("no_clear_relational_signal"),
        "insufficient_evidence_count": configurations.count("insufficient_evidence"),
        "review_required_count": len(rows),
    }


def finish_run(client: Client, run_id: str, *, status: str, rows: list[dict[str, Any]]) -> None:
    payload = {
        **run_progress_payload(rows),
        "completed_at": iso_z(utc_now()),
        "status": status,
    }
    client.table("symbiosis_classification_runs").update(payload).eq("symbiosis_run_id", run_id).execute()


def checkpoint_run(client: Client, run_id: str, *, rows: list[dict[str, Any]]) -> None:
    """Persist a resumable checkpoint without marking the run complete."""
    payload = {**run_progress_payload(rows), "status": "running", "completed_at": None}
    client.table("symbiosis_classification_runs").update(payload).eq("symbiosis_run_id", run_id).execute()


def resume_or_start_run(
    client: Client,
    *,
    scope: str,
    target_release_id: str | None,
    collection_run_id: str | None,
    empowerment_run_id: str | None,
    model_revision: str,
    resume_only: bool = False,
) -> tuple[str, str, bool]:
    """Reuse the latest interrupted run with the same durable codebook contract."""
    query = (
        client.table("symbiosis_classification_runs")
        .select("symbiosis_run_id,run_key,status")
        .eq("scope", scope)
        .eq("classifier_version", CLASSIFIER_VERSION)
        .eq("codebook_version", CODEBOOK_VERSION)
        .in_("status", ["running", "failed"])
    )
    if target_release_id:
        query = query.eq("target_release_id", target_release_id)
    else:
        query = query.is_("target_release_id", "null")
    if collection_run_id:
        query = query.eq("collection_run_id", collection_run_id)
    response = query.order("started_at", desc=True).limit(1).execute()
    rows = getattr(response, "data", None) or []
    if rows:
        row = rows[0]
        run_id = str(row["symbiosis_run_id"])
        run_key = str(row["run_key"])
        (
            client.table("symbiosis_classification_runs")
            .update({"status": "running", "completed_at": None})
            .eq("symbiosis_run_id", run_id)
            .execute()
        )
        print(f"Resuming durable symbiosis run {run_key}.", flush=True)
        return run_id, run_key, True

    if resume_only:
        raise SymbiosisClassificationError(
            "No interrupted relationship run matches the current release and full-body "
            "classification lineage. Refusing to start a new multi-hour run in --resume-only mode."
        )

    run_id, run_key = start_run(
        client,
        scope=scope,
        target_release_id=target_release_id,
        collection_run_id=collection_run_id,
        empowerment_run_id=empowerment_run_id,
        model_revision=model_revision,
    )
    return run_id, run_key, False


def saved_rows_for_run(client: Client, run_id: str) -> list[dict[str, Any]]:
    """Load rows written before a timeout or recoverable infrastructure error."""
    return paged_table(
        client,
        "symbiosis_classifications",
        "symbiosis_classification_id,unit_key,lens,model_configuration,content_basis,raw_output",
        apply=lambda query: query.eq("symbiosis_run_id", run_id),
    )


def reusable_saved_rows(
    rows: list[dict[str, Any]],
    units: list[tuple[str, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep interrupted rows only when their recorded input still covers now."""
    current_by_key = {str(unit["unit_key"]): unit for _, unit in units}
    reusable: list[dict[str, Any]] = []
    stale: list[str] = []
    for row in rows:
        unit_key = str(row.get("unit_key") or "")
        current = current_by_key.get(unit_key)
        if not current:
            continue
        stored_basis, stored_summary = classification_input_evidence(row)
        if evidence_basis_covers(
            stored_content_basis=stored_basis,
            stored_evidence_summary=stored_summary,
            current_content_basis=current.get("content_basis"),
            current_evidence_summary=current.get("evidence_basis_summary"),
        ):
            reusable.append(row)
        else:
            stale.append(unit_key)
    return reusable, stale


def start_server() -> tuple[subprocess.Popen[Any], Any]:
    log_path = Path("/tmp/aieo-symbiosis-qwen.log")
    handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            LLAMA_SERVER_BIN,
            "-hf",
            f"{QWEN_REPO}:{QWEN_QUANT}",
            "--host",
            "127.0.0.1",
            "--port",
            "8080",
            "-c",
            "12288",
            "-np",
            "1",
            "--jinja",
            "-ngl",
            "0",
        ],
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 480
    while time.time() < deadline:
        if process.poll() is not None:
            handle.flush()
            try:
                print(log_path.read_text(encoding="utf-8")[-12000:], file=sys.stderr)
            except Exception:
                pass
            raise SymbiosisClassificationError("Qwen server exited during startup.")
        try:
            response = requests.get(HEALTH_URL, timeout=3)
            if response.ok:
                return process, handle
        except requests.RequestException:
            pass
        time.sleep(2)
    raise SymbiosisClassificationError("Qwen server did not become healthy.")


def stop_server(process: subprocess.Popen[Any] | None, handle: Any | None) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    if handle is not None:
        handle.close()


def extract_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        payload = json.loads(raw[start:end + 1])
        if isinstance(payload, dict):
            return payload
    raise SymbiosisClassificationError("Model response did not contain a JSON object.")


def _gold_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(text or "").casefold())
        if len(token) >= 3 and token not in {"artificial", "intelligence", "generative", "model"}
    }


def _gold_example_text(row: dict[str, Any]) -> str:
    title = str(row.get("title") or "").strip()
    headlines = [str(value).strip() for value in (row.get("source_headlines") or []) if str(value).strip()]
    parts = [title] + [value for value in headlines if value != title]
    return " | ".join(parts)[:600]


def owner_gold_calibration_block(*, lens: str, evidence: str, limit: int = 4) -> str:
    """Return a small, provenance-safe calibration block from owner-adjudicated QC.

    The gold examples teach coding boundaries only. They are never used as a
    same-event lookup or automatic override, because a recurring development can
    be framed differently in a later weekly release.
    """
    if not OWNER_GOLD_PATH.exists():
        return ""
    try:
        payload = json.loads(OWNER_GOLD_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Warning: owner gold could not be read: {exc}", file=sys.stderr)
        return ""
    rows = [row for row in (payload.get("records") or []) if isinstance(row, dict) and row.get("lens") == lens]
    if not rows:
        return ""
    target = _gold_tokens(evidence)
    scored: list[tuple[float, str, dict[str, Any]]] = []
    for row in rows:
        example_text = _gold_example_text(row)
        tokens = _gold_tokens(example_text)
        union = target | tokens
        score = (len(target & tokens) / len(union)) if union else 0.0
        configuration = str((row.get("final") or {}).get("configuration") or "")
        scored.append((score, configuration, row))
    scored.sort(key=lambda item: (item[0], item[1], str(item[2].get("gold_id") or "")), reverse=True)

    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    # Prefer genuinely similar examples first.
    for score, _configuration, row in scored:
        if score <= 0:
            continue
        gold_id = str(row.get("gold_id") or row.get("unit_key") or "")
        if gold_id in seen_ids:
            continue
        selected.append(row)
        seen_ids.add(gold_id)
        if len(selected) >= limit:
            break
    # If lexical overlap is weak, add diverse decision-boundary examples rather
    # than an arbitrary cluster of one label.
    if len(selected) < limit:
        preferred = [
            "no_clear_relational_signal",
            "insufficient_evidence",
            "ai_enabling_only",
            "ai_constraining_only",
            "mutualism",
            "ai_benefiting_parasitism",
            "competition",
        ]
        for configuration in preferred:
            for _score, candidate_config, row in scored:
                if candidate_config != configuration:
                    continue
                gold_id = str(row.get("gold_id") or row.get("unit_key") or "")
                if gold_id in seen_ids:
                    continue
                selected.append(row)
                seen_ids.add(gold_id)
                break
            if len(selected) >= limit:
                break
    if not selected:
        return ""

    lines = [
        "OWNER-ADJUDICATED CALIBRATION EXAMPLES",
        "Use these only to learn coding boundaries. Do not copy an example label unless the new evidence supports it.",
    ]
    for index, row in enumerate(selected, start=1):
        final = row.get("final") or {}
        lines.extend(
            [
                f"Example {index} evidence: {_gold_example_text(row)}",
                (
                    f"Example {index} labels: human={final.get('human_experience_type')}; "
                    f"AI={final.get('ai_expressive_role')}; evidence_status={final.get('evidence_status')}; "
                    f"configuration={final.get('configuration')}; "
                    f"relationship_patterns={json.dumps(final.get('relationship_patterns') or {}, sort_keys=True)}; "
                    f"distribution_signal={final.get('distribution_signal') or 'not_shown'}"
                ),
            ]
        )
    return "\n".join(lines)


def classifier_prompt(*, lens: str, evidence: str, content_basis: str) -> str:
    lens_note = (
        "COVERAGE LENS: classify how this one published page represents the relationship."
        if lens == "coverage"
        else "EVENT LENS: classify the resolved development from the evidence represented in this weekly release. Do not copy the Coverage Lens label automatically."
    )
    calibration = owner_gold_calibration_block(lens=lens, evidence=evidence)
    calibration_section = f"\n\n{calibration}" if calibration else ""
    return f"""
/no_think

You are a conservative research coder for the AI Empowerment Observatory.
Classify how the supplied source evidence represents the relation between
people and the AI system, operator, or ecosystem side. This is a discourse
classification, not an objective claim about system performance, consciousness,
intentions, or biological fitness.

{lens_note}

Code two dimensions independently.

HUMAN EXPERIENCE TYPE
- extension: people project or exercise an existing capacity through AI or through a response to AI
- expansion: people gain a new capability, resource, access, protection, or opportunity
- restriction: autonomy, control, ownership, choice, consent, or participation is limited
- reduction: an existing skill, capacity, livelihood, protection, or outcome is diminished
- neutral: enough evidence is available, but no human enabling or constraining signal is described
- unclear: a human-side relation is suggested, but its direction cannot be supported

AI EXPRESSIVE ROLE
- ai_extension: the AI system is represented as functioning, producing useful output, or extending its operative reach
- ai_expansion: the AI system or operator side gains data, learning, adoption, resources, market reach, or capability
- ai_restriction: the AI system or operator is blocked, limited, contained, appealed, regulated, or otherwise constrained
- ai_reduction: the AI system is represented as degraded, failing, hallucinating, losing capability, or being withdrawn
- neutral: enough evidence is available, but no AI-side enabling or constraining signal is described
- unclear: an AI-side relation is suggested, but its direction cannot be supported

EVIDENCE STATUS
- sufficient: the available evidence supports both selected component labels, including neutral/neutral when the evidence clearly describes a non-relational development
- partial: the evidence supports one directional side while the other side is neutral
- insufficient: the available headline or text is too opaque to determine whether a defensible directional or neutral/no-clear judgement can be made

DECISION BOUNDARY POLICY
1. Do not force every AI story into mutualism, parasitism, or competition.
2. Do not use insufficient as a default for a story that is clearly non-relational. If the headline clearly describes a stock/valuation story, conference announcement, ranking/list, corporate transaction, or other AI-themed item but establishes no human or AI directional relation, code human=neutral, AI=neutral, evidence_status=sufficient. That yields no_clear_relational_signal.
3. A model launch, investment, institutional announcement, conference, or policy mention is not automatically a gain. Require an explicit capability, adoption, operative-reach, access, productivity, constraint, or other directional cue.
4. If one side is directional and the other side is not established, use the directional label plus neutral and evidence_status=partial. Do not mark the whole unit insufficient merely because the second side is neutral.
5. An explicit ban, halt, withdrawal, blocking rule, or operational limitation can support an AI-side restriction even when no people-side outcome is stated. A stated failure/degradation can support AI-side reduction.
6. Explicit deployment/use/application can support AI-side extension. Code a people-side gain only when the evidence says or clearly entails that people gain capacity, access, protection, productivity, opportunity, or another defined benefit.
7. Human extension requires an observable human capacity or action. Mere exposure to AI, growing up with AI, discussing AI, or saying that AI is changing a domain does not by itself establish human extension or gain.
8. Distinguish attitudes from outcomes. Dislike, concern, controversy, or split opinion does not by itself mean people or AI are constrained.
9. If the evidence explicitly presents both a human gain and a human cost and no single direction can be supported, use human=unclear rather than neutral. A genuine directional conflict is ambiguous, not no-clear.
10. Governance artifacts are not automatically AI gains. A policy, standard, proposed guidance, legal analysis, or regulatory discussion supports AI restriction only when it actually limits or blocks the AI/operator; otherwise keep the AI side neutral unless a separate capability/adoption cue is present.
11. Allegations remain allegations. A lawsuit or complaint is a real human action, but it does not prove liability or a court outcome.
12. When a source reports that human work, data, likeness, or creative output feeds AI training without consent, control, or compensation, human restriction or reduction and AI expansion may be supported.
13. For a lawsuit event, the filing itself may show human extension or expansion while the AI side remains neutral unless the filing has already constrained the system or operator.
14. Do not use the search market as story location.
15. A development can contain several relationship patterns at once. Mark every pattern directly supported by the evidence. Do not force the whole development into one compromise pattern.
16. Mark unequal human outcomes only when the evidence says that some groups benefit more, face different conditions, or are put at a disadvantage relative to others.
17. Write public_takeaway as one short sentence in everyday language. State what the development means for people; avoid method labels and academic terminology.
18. Write people_evidence in at most 280 characters. If you choose a people benefit or downside, identify the specific source-supported fact behind it. Otherwise write "no people outcome stated" or "not enough evidence". Do not quote long passages.
19. The collected full article body may be written in any language. Treat the original-language body as evidence. English headline normalisation is only an aid for matching and review. Do not mark evidence insufficient merely because the source is not English.
20. Return only one JSON object.{calibration_section}

Required keys:
ai_relevant, evidence_status, relational_signal, human_experience_type,
ai_expressive_role, human_reasoning, ai_reasoning, summary, confidence,
topic, geographic_scope, country_iso3s, relationship_patterns,
distribution_signal, public_takeaway, people_evidence.

confidence must be a JSON number from 0 through 1, for example 0.85. Do not
return a word such as high, medium, or low.

relationship_patterns must be an object with exactly these boolean keys:
- mutualism: AI works, spreads, or grows while people gain
- ai_benefiting_parasitism: AI works, spreads, or grows while people lose ground
- human_benefiting_parasitism: people gain while AI is limited, corrected, blocked, or fails
- competition: people lose ground while AI is limited, corrected, blocked, or fails

More than one relationship_patterns value may be true. If the evidence is insufficient,
all four values must be false.

Allowed distribution_signal values: broadly_shared, unequal, not_shown, unclear.

Allowed relational_signal values: complete, human_only, ai_only, none, unclear.
Allowed geographic_scope values: country, multi_country, global, unclear.
Use three-letter ISO country codes only when directly supported by the evidence.

Content basis: {content_basis}

EVIDENCE
{evidence}
""".strip()

def classification_audit(unit: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Persist a compact, inspectable claim-and-provenance record with each row."""
    evidence_summary = unit.get("evidence_basis_summary") or {}
    full_sources = int(evidence_summary.get("full_text_sources") or 0)
    signals = result.get("public_signals") or {}
    directional = bool(signals.get("people_gaining") or signals.get("people_losing_ground"))
    model_not_called = bool((result.get("raw_output") or {}).get("classification_not_run"))
    flags: list[str] = []
    if model_not_called:
        flags.extend(["full_article_body_unavailable", "model_not_called"])
    if directional and full_sources == 0:
        flags.append("invalid_directional_result_without_full_article_body")
    if directional and result.get("evidence_status") != "sufficient":
        flags.append("directional_result_with_partial_evidence")
    if result.get("evidence_status") == "insufficient":
        flags.append("insufficient_evidence")
    evidence_claim = " ".join(str(result.get("people_evidence") or "").split())[:280]
    if directional and not evidence_claim:
        flags.append("missing_people_evidence_claim")
    return {
        "schema_version": "aieo_relationship_classification_audit_v1",
        "input": {
            "content_basis": str(unit.get("content_basis") or "headline_only"),
            "source_count": int(evidence_summary.get("source_count") or 0),
            "full_text_sources": full_sources,
            "body_coverage": str(evidence_summary.get("body_coverage") or "not_recorded"),
            "input_policy": str(evidence_summary.get("input_policy") or FULL_BODY_REQUIRED_POLICY),
        },
        "people_evidence": evidence_claim,
        "directional_people_result": directional,
        "classification_not_run": model_not_called,
        "flags": flags,
    }


def call_classifier(*, lens: str, evidence: str, content_basis: str) -> dict[str, Any]:
    prompt = classifier_prompt(lens=lens, evidence=evidence, content_basis=content_basis)
    modes = [
        ("json_object", {"response_format": {"type": "json_object"}}, 0.1),
        ("prompt_only", {}, 0.1),
        ("retry", {}, 0.4),
    ]
    last_error: Exception | None = None
    for index, (name, extra, temperature) in enumerate(modes, start=1):
        try:
            response = requests.post(
                SERVER_URL,
                json={
                    "model": f"{QWEN_REPO}:{QWEN_QUANT}",
                    "messages": [
                        {
                            "role": "system",
                            "content": "Return only valid JSON. Use insufficient evidence only when the available evidence is genuinely too thin for either a directional or a defensible neutral/no-clear judgment.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": temperature,
                    "top_p": 0.8,
                    "max_tokens": 900,
                    "stream": False,
                    **extra,
                },
                timeout=300,
            )
            response.raise_for_status()
            text = str(response.json()["choices"][0]["message"]["content"])
            raw = extract_json(text)
            normalized = validate_model_payload(raw)
            normalized["raw_output"] = {
                "model_response": raw,
                "relationship_patterns": normalized["relationship_patterns"],
                "distribution_signal": normalized["distribution_signal"],
                "public_takeaway": normalized["public_takeaway"],
                "people_evidence": normalized["people_evidence"],
                "public_signal_schema_version": normalized["schema_version"],
                "normalization_warnings": normalized.get("normalization_warnings") or [],
            }
            normalized["structured_output_mode"] = name
            return normalized
        except Exception as exc:
            last_error = exc
            print(f"Warning: symbiosis request {index}/{len(modes)} failed: {exc}", file=sys.stderr)
            if index < len(modes):
                time.sleep(2)
    raise SymbiosisClassificationError(f"Model failed after all modes: {last_error}")


def article_evidence(article: dict[str, Any]) -> str:
    lines = [
        f"Publisher: {article['publisher']}",
        f"Publication date: {article['date']}",
        f"Source language: {article.get('source_language') or 'not confidently detected'}",
        f"Headline: {article['headline_english']}",
    ]
    if article.get("evidence_text"):
        label = {
            "full_text": "Collected article body",
            "article_summary": "Reviewed or stored article summary",
            "headline_and_snippet": "Source snippet",
        }.get(str(article.get("content_basis") or ""), "Available source evidence")
        lines.append(f"{label}: {article['evidence_text']}")
    else:
        lines.append("No full article body is available. Do not classify this source from the headline.")
    return "\n".join(lines)


def event_evidence(event: dict[str, Any]) -> str:
    lines = [
        f"Development title: {event['event_title']}",
        f"Development date: {event['event_date']}",
    ]
    lines.append("Collected full article bodies represented in this weekly release:")
    members = event.get("model_member_articles") or []
    per_source_limit = max(2600, min(MAX_ARTICLE_EVIDENCE_CHARS, MAX_EVENT_EVIDENCE_CHARS // max(1, len(members))))
    for article in members:
        line = (
            f"- {article['publisher']}: {article['headline_english']} "
            f"({article['date']}; source language: "
            f"{article.get('source_language') or 'not confidently detected'})"
        )
        if article.get("evidence_text"):
            excerpt = compact_evidence_text(article["evidence_text"], per_source_limit)
            line += f" | Evidence basis: {article.get('content_basis')} | {excerpt}"
        lines.append(line)
    return "\n".join(lines)


def unavailable_full_body_result(unit: dict[str, Any]) -> dict[str, Any]:
    """Create a transparent non-model row when no full source body exists."""
    title = str(unit.get("headline_english") or unit.get("event_title") or "this development")
    result = validate_model_payload(
        {
            "ai_relevant": True,
            "human_experience_type": "unclear",
            "ai_expressive_role": "unclear",
            "evidence_status": "insufficient",
            "relational_signal": "unclear",
            "confidence": 0.0,
            "topic": "other",
            "geographic_scope": "unclear",
            "country_iso3s": [],
            "summary": "No full article body was available, so the source was not model-classified.",
            "public_takeaway": "The full source article was not available to read, so no conclusion about people was made.",
            "people_evidence": "",
            "relationship_patterns": {
                "mutualism": False,
                "ai_benefiting_parasitism": False,
                "human_benefiting_parasitism": False,
                "competition": False,
            },
            "distribution_signal": "not_shown",
        }
    )
    result["raw_output"] = {
        "classification_not_run": True,
        "reason": "full_article_body_unavailable",
        "input_policy": FULL_BODY_REQUIRED_POLICY,
        "unit_title": title,
        "relationship_patterns": result["relationship_patterns"],
        "distribution_signal": result["distribution_signal"],
        "public_takeaway": result["public_takeaway"],
        "people_evidence": "",
    }
    return result


def insert_result(
    client: Client,
    *,
    run_id: str,
    lens: str,
    unit: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    storage_content_basis = content_basis_for_storage(unit["content_basis"])
    payload = {
        "symbiosis_run_id": run_id,
        "codebook_version": CODEBOOK_VERSION,
        "lens": lens,
        "unit_key": unit["unit_key"],
        "release_id": unit["release_id"],
        "period_start": unit["period_start"],
        "period_end": unit["period_end"],
        "article_id": unit.get("article_id") if lens == "coverage" else None,
        "event_id": unit.get("event_id") if lens == "event" else None,
        "ai_relevant": result["ai_relevant"],
        "content_basis": storage_content_basis,
        "evidence_status": result["evidence_status"],
        "relational_signal": result["relational_signal"],
        "model_human_experience_type": result["human_experience_type"],
        "model_ai_expressive_role": result["ai_expressive_role"],
        "model_human_direction": result["human_direction"],
        "model_ai_direction": result["ai_direction"],
        "model_configuration": result["configuration"],
        "model_plain_label": result["plain_label"],
        "model_human_reasoning": result["human_reasoning"],
        "model_ai_reasoning": result["ai_reasoning"],
        "model_summary": result["summary"],
        "model_confidence": result["confidence"],
        "topic": result["topic"],
        "geographic_scope": result["geographic_scope"],
        "country_iso3s": result["country_iso3s"],
        "raw_output": {
            **result["raw_output"],
            "input_evidence": unit.get("evidence_basis_summary") or {},
            "content_basis": unit["content_basis"],
            "storage_content_basis": storage_content_basis,
            "input_policy": FULL_BODY_REQUIRED_POLICY,
            "classification_audit": classification_audit(unit, result),
        },
        "review_status": "pending",
        "updated_at": iso_z(utc_now()),
    }
    response = client.table("symbiosis_classifications").insert(payload).select("*").execute()
    return first_row(response, f"writing {lens} classification")


def write_output(payload: dict[str, Any]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT_PATH)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=["latest", "history"], default="latest")
    parser.add_argument("--lens", choices=["both", "coverage", "event"], default="both")
    parser.add_argument("--release-id", default="", help="Optional weekly release ID")
    parser.add_argument("--limit", type=int, default=0, help="Maximum units in this batch; 0 means all selected units")
    parser.add_argument("--replace", action="store_true", help="Reclassify release-specific units already coded under this codebook")
    parser.add_argument(
        "--resume-only",
        action="store_true",
        help="Resume a matching interrupted run; fail instead of starting a new run.",
    )
    parser.add_argument(
        "--time-budget-minutes",
        type=float,
        default=0.0,
        help="Checkpoint safely after this many minutes; 0 means no script-level budget.",
    )
    parser.add_argument(
        "--status-output",
        default="",
        help="Optional JSON path containing complete=true/false for a resumable workflow pass.",
    )
    return parser.parse_args()


def write_pass_status(path: str, payload: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def pass_status_payload(
    *,
    complete: bool,
    run_id: str | None,
    run_key: str | None,
    selected_units: int,
    saved_units: int,
    new_units: int,
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": "aieo_symbiosis_pass_status_v1",
        "complete": complete,
        "symbiosis_run_id": run_id,
        "run_key": run_key,
        "selected_units": selected_units,
        "saved_units": saved_units,
        "new_units": new_units,
        "remaining_units": max(0, selected_units - saved_units),
        "reason": reason,
    }


def main() -> int:
    args = parse_args()
    if args.time_budget_minutes < 0:
        raise SymbiosisClassificationError("--time-budget-minutes must be zero or greater.")
    if args.resume_only and not args.replace:
        raise SymbiosisClassificationError("--resume-only requires --replace so all saved run units are considered.")
    releases, skipped_references = selected_releases(args.scope, args.release_id)
    client: Client = create_client(required_env("SUPABASE_URL"), required_env("SUPABASE_SECRET_KEY"))

    units: list[tuple[str, dict[str, Any]]] = []
    for release in releases:
        units.extend(release_units(client, release, lens=args.lens))

    if not args.replace:
        existing = successful_existing_unit_keys(client, units)
        units = [(lens, unit) for lens, unit in units if unit["unit_key"] not in existing]

    units.sort(key=lambda item: item[1]["unit_key"])
    if args.limit > 0:
        units = units[:args.limit]

    if not units:
        payload = {
            "status": "nothing_to_classify",
            "scope": args.scope,
            "selected_release_ids": [str(release["release_id"]) for release in releases],
            "excluded_aggregate_references": skipped_references,
            "codebook_version": CODEBOOK_VERSION,
            "remaining_note": (
                "All reviewable release-specific units already have a successful classification "
                "under this codebook. Aggregate publication references without item-level "
                "evidence are disclosed but are not forced into a relationship classification."
            ),
        }
        write_output(payload)
        write_pass_status(
            args.status_output,
            pass_status_payload(
                complete=True,
                run_id=None,
                run_key=None,
                selected_units=0,
                saved_units=0,
                new_units=0,
                reason="nothing_to_classify",
            ),
        )
        print(json.dumps(payload, indent=2))
        return 0

    try:
        model_revision = HfApi().model_info(QWEN_REPO).sha or "unknown"
    except Exception as exc:
        print(f"Warning: could not resolve model revision: {exc}", file=sys.stderr)
        model_revision = "unknown"

    one_release = len({unit["release_id"] for _, unit in units}) == 1
    target_release_id = units[0][1]["release_id"] if one_release else None
    selected_release = next((release for release in releases if release["release_id"] == target_release_id), None)
    lineage = selected_release.get("lineage", {}) if selected_release else {}
    run_id, run_key, resumed = resume_or_start_run(
        client,
        scope="latest_release" if args.scope == "latest" else "historical_releases",
        target_release_id=target_release_id,
        collection_run_id=str(lineage.get("collection_run_id")) if lineage.get("collection_run_id") else None,
        empowerment_run_id=str(lineage.get("classification_run_id")) if lineage.get("classification_run_id") else None,
        model_revision=model_revision,
        resume_only=args.resume_only,
    )

    loaded_saved_rows = saved_rows_for_run(client, run_id)
    saved_rows, stale_saved_keys = reusable_saved_rows(loaded_saved_rows, units)
    if stale_saved_keys:
        (
            client.table("symbiosis_classification_runs")
            .update({"status": "failed", "completed_at": iso_z(utc_now())})
            .eq("symbiosis_run_id", run_id)
            .execute()
        )
        if args.resume_only:
            raise SymbiosisClassificationError(
                "The interrupted run contains "
                f"{len(stale_saved_keys)} rows classified from weaker evidence. "
                "Refusing to restart or reuse them in --resume-only mode."
            )
        run_id, run_key = start_run(
            client,
            scope="latest_release" if args.scope == "latest" else "historical_releases",
            target_release_id=target_release_id,
            collection_run_id=str(lineage.get("collection_run_id")) if lineage.get("collection_run_id") else None,
            empowerment_run_id=str(lineage.get("classification_run_id")) if lineage.get("classification_run_id") else None,
            model_revision=model_revision,
        )
        resumed = False
        saved_rows = []
    saved_keys = {str(row.get("unit_key") or "") for row in saved_rows}
    pending_units = [(lens, unit) for lens, unit in units if unit["unit_key"] not in saved_keys]
    if resumed:
        print(
            f"Recovered {len(saved_rows)} saved symbiosis classifications; "
            f"{len(pending_units)} remain in this run.",
            flush=True,
        )

    def unit_has_full_body_model_evidence(lens: str, unit: dict[str, Any]) -> bool:
        if lens == "coverage":
            return str(unit.get("content_basis") or "") == "full_text"
        return bool(unit.get("model_member_articles"))

    process: subprocess.Popen[Any] | None = None
    handle: Any | None = None
    newly_written: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    deadline = (
        time.monotonic() + args.time_budget_minutes * 60
        if args.time_budget_minutes > 0
        else None
    )
    try:
        if any(unit_has_full_body_model_evidence(lens, unit) for lens, unit in pending_units):
            process, handle = start_server()
        for position, (lens, unit) in enumerate(pending_units, start=1):
            if deadline is not None and time.monotonic() >= deadline:
                all_rows = saved_rows + newly_written
                checkpoint_run(client, run_id, rows=all_rows)
                status = pass_status_payload(
                    complete=False,
                    run_id=run_id,
                    run_key=run_key,
                    selected_units=len(units),
                    saved_units=len(all_rows),
                    new_units=len(newly_written),
                    reason="time_budget_reached",
                )
                write_pass_status(args.status_output, status)
                print(json.dumps(status, indent=2))
                return 0
            print(f"[{position}/{len(pending_units)}] {lens}: {unit['unit_key']}", flush=True)
            if unit_has_full_body_model_evidence(lens, unit):
                evidence = article_evidence(unit) if lens == "coverage" else event_evidence(unit)
                result = call_classifier(lens=lens, evidence=evidence, content_basis=unit["content_basis"])
            else:
                result = unavailable_full_body_result(unit)
            row = insert_result(client, run_id=run_id, lens=lens, unit=unit, result=result)
            newly_written.append(row)
            review_rows.append(
                {
                    "symbiosis_classification_id": row["symbiosis_classification_id"],
                    "release_id": unit["release_id"],
                    "lens": lens,
                    "unit_key": unit["unit_key"],
                    "title": unit.get("headline_english") or unit.get("event_title"),
                    "sources": (
                        [{"publisher": unit["publisher"], "url": unit["url"]}]
                        if lens == "coverage"
                        else [
                            {"publisher": article["publisher"], "url": article["url"]}
                            for article in unit["member_articles"]
                        ]
                    ),
                    "content_basis": unit["content_basis"],
                    "model": result,
                }
            )
        all_rows = saved_rows + newly_written
        finish_run(client, run_id, status="success", rows=all_rows)
        payload = {
            "status": "success",
            "scope": args.scope,
            "run_key": run_key,
            "symbiosis_run_id": run_id,
            "selected_release_ids": sorted({unit["release_id"] for _, unit in units}),
            "excluded_aggregate_references": skipped_references,
            "codebook_version": CODEBOOK_VERSION,
            "classifier_version": CLASSIFIER_VERSION,
            "classified_units": len(all_rows),
            "newly_classified_units": len(newly_written),
            "resumed_saved_units": len(saved_rows),
            "coverage_units": sum(row.get("lens") == "coverage" for row in all_rows),
            "event_units": sum(row.get("lens") == "event" for row in all_rows),
            "all_require_human_review": True,
            "review_rows": review_rows,
        }
        write_output(payload)
        write_pass_status(
            args.status_output,
            pass_status_payload(
                complete=True,
                run_id=run_id,
                run_key=run_key,
                selected_units=len(units),
                saved_units=len(all_rows),
                new_units=len(newly_written),
                reason="all_selected_units_saved",
            ),
        )
        print(json.dumps({key: value for key, value in payload.items() if key != "review_rows"}, indent=2))
        return 0
    except Exception as exc:
        # Preserve committed rows. A later pass can resume this run after a
        # transient model or infrastructure failure instead of redoing hours
        # of classification or silently replacing already reviewable evidence.
        try:
            all_rows = saved_rows + newly_written
            finish_run(client, run_id, status="failed", rows=all_rows)
        except Exception as checkpoint_exc:
            print(f"Warning: could not checkpoint failed symbiosis run: {checkpoint_exc}", file=sys.stderr)
        write_pass_status(
            args.status_output,
            pass_status_payload(
                complete=False,
                run_id=run_id,
                run_key=run_key,
                selected_units=len(units),
                saved_units=len(saved_rows) + len(newly_written),
                new_units=len(newly_written),
                reason=f"failed: {type(exc).__name__}",
            ),
        )
        raise
    finally:
        stop_server(process, handle)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SymbiosisClassificationError as exc:
        print(f"Symbiosis classification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
