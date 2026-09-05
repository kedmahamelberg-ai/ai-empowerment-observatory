#!/usr/bin/env python3
"""Stage 7C — classify Coverage Lens + Event Lens and calculate indices.

Efficiency:
- classify each current article with a stored full body once for Coverage Lens;
- singleton events reuse their article classification deterministically;
- only multi-article events with a stored full body receive an additional
  event-level Qwen call.

Sources without a full article body are retained as unavailable evidence. They
are not sent to the model and do not receive a substantive classifier result.

This preserves the conceptual difference:
- Coverage Lens = article weighted.
- Event Lens = each resolved event weighted once.

The empowerment score is deterministic and transparent:
  expanding      + degree / 3
  contracting    - degree / 3
  mixed          0
  non_empowerment 0
  unclear        excluded from index denominator

Confidence is reported separately and NEVER multiplied into the substantive
score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests
from huggingface_hub import HfApi
from supabase import Client, create_client

from brief_content_common import MIN_FULL_BODY_EVIDENCE_UNITS, evidence_unit_count
from translation_policy import SUPPORTED_TRANSLATION_PROFILES, preferred_translation_rows
from symbiosis_model_output import CONFIDENCE_VALUES, ModelOutputError, TRANSPORT_VERSION, dimension_schema, response_result

ROOT = Path(__file__).resolve().parents[1]

REVIEW_OUTPUT = ROOT / "review" / "classification" / "latest.json"
PUBLIC_OUTPUT = ROOT / "data" / "lenses" / "latest.json"

CLASSIFIER_VERSION = "7C.5_full_body_required"
CODEBOOK_VERSION = "observatory_dual_lens_v1.1"
EVENT_METHOD = "article_to_event_v1"
FULL_BODY_REQUIRED_POLICY = "full_article_body_required_v1"

QWEN_REPO = "Qwen/Qwen3-4B-GGUF"
QWEN_QUANT = "Q4_K_M"

LLAMA_SERVER_BIN = os.environ.get(
    "LLAMA_SERVER_BIN",
    "/tmp/llama.cpp/build/bin/llama-server",
)

SERVER_URL = "http://127.0.0.1:8080/v1/chat/completions"
HEALTH_URL = "http://127.0.0.1:8080/health"

MIN_COUNTRY_SIGNAL_N = 3
AUDIT_TARGET = 12
MULTI_EVENT_AUDIT_MAX = 5
MAX_ARTICLE_EVIDENCE_CHARS = 14000
MAX_EVENT_EVIDENCE_CHARS = 22000
MIN_FULL_TEXT_WORDS = MIN_FULL_BODY_EVIDENCE_UNITS
SUPABASE_WRITE_MAX_ATTEMPTS = 6
SUPABASE_RETRY_DELAYS_SECONDS = (2, 4, 8, 16, 30)

VALID_STATUS = {
    "expanding",
    "contracting",
    "mixed",
    "non_empowerment",
    "unclear",
}

VALID_FRAME = {
    "opportunity",
    "threat",
    "contested",
    "descriptive_neutral",
    "unclear",
}

VALID_BREADTH = {
    "broad",
    "targeted",
    "concentrated",
    "unclear",
}

VALID_DIMENSIONS = {
    "operational",
    "creative",
    "agentic",
    "normative",
}

VALID_AUTHORITY = {
    "increasing",
    "decreasing",
    "unchanged",
    "unclear",
}

VALID_TOPIC = {
    "work_employment",
    "business_productivity",
    "consumer_services",
    "creativity_ip",
    "education_research",
    "healthcare",
    "government_regulation",
    "privacy_security",
    "infrastructure_investment",
    "other",
}

VALID_SCOPE = {
    "country",
    "multi_country",
    "global",
    "unclear",
}

VALID_CONTENT_BASIS = {
    "headline_only",
    "headline_and_snippet",
    "article_summary",
    "multiple_sources",
    "full_text",
}

CLASSIFICATION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "ai_relevant": {"type": "boolean"},
        "empowerment_status": {
            "type": "string",
            "enum": ["expanding", "contracting", "mixed", "non_empowerment", "unclear"]
        },
        "empowerment_degree": {"type": "integer", "minimum": 0, "maximum": 3},
        "narrative_frame": {
            "type": "string",
            "enum": ["opportunity", "threat", "contested", "descriptive_neutral", "unclear"]
        },
        "distribution_breadth": {
            "type": "string",
            "enum": ["broad", "targeted", "concentrated", "unclear"]
        },
        "dominant_dimension": {
            "type": "string",
            "enum": ["operational", "creative", "agentic", "normative", "none"]
        },
        "dimensions": {
            "type": "object",
            "properties": {
                name: {
                    "type": "object",
                    "properties": {
                        "present": {"type": "boolean"},
                        "direction": {
                            "type": "string",
                            "enum": ["expanding", "contracting", "mixed", "unclear", "not_present"]
                        },
                        "degree": {"type": "integer", "minimum": 0, "maximum": 3},
                        "confidence": {
                            "type": "number",
                            "enum": CONFIDENCE_VALUES,
                            "minimum": 0,
                            "maximum": 1,
                            "description": (
                                "Diagnostic certainty about this dimension's "
                                "categorical coding. Use 0 only when the model "
                                "cannot support any dimension-level judgement."
                            ),
                        },
                        "reasoning": {"type": "string"}
                    },
                    "required": ["present", "direction", "degree", "confidence", "reasoning"],
                    "additionalProperties": False
                }
                for name in ["operational", "creative", "agentic", "normative"]
            },
            "required": ["operational", "creative", "agentic", "normative"],
            "additionalProperties": False
        },
        "ai_authority_shift": {
            "type": "string",
            "enum": ["increasing", "decreasing", "unchanged", "unclear"]
        },
        "topic": {
            "type": "string",
            "enum": [
                "work_employment", "business_productivity", "consumer_services",
                "creativity_ip", "education_research", "healthcare",
                "government_regulation", "privacy_security",
                "infrastructure_investment", "other"
            ]
        },
        "geographic_scope": {
            "type": "string",
            "enum": ["country", "multi_country", "global", "unclear"]
        },
        "country_iso3s": {"type": "array", "items": {"type": "string"}},
        "confidence": {
            "type": "number",
            "enum": CONFIDENCE_VALUES,
            "minimum": 0,
            "maximum": 1,
            "description": (
                "Diagnostic self-rating of categorical certainty. Headline-only "
                "evidence is never supplied to this classifier. Use 0 only "
                "when no defensible categorical judgement can be made from "
                "the supplied full article body."
            ),
        },
        "reasoning": {"type": "string"}
    },
    "required": [
        "ai_relevant", "empowerment_status", "empowerment_degree",
        "narrative_frame", "distribution_breadth", "dominant_dimension",
        "dimensions", "ai_authority_shift", "topic", "geographic_scope",
        "country_iso3s", "confidence", "reasoning"
    ],
    "additionalProperties": False
}


# Enforce interdependent fields in the decoding grammar, not only after a
# generation has already contradicted itself. Keep the documented schema above
# as the field reference and replace each dimension with its valid alternatives.
for _dimension in ("operational", "creative", "agentic", "normative"):
    CLASSIFICATION_JSON_SCHEMA["properties"]["dimensions"]["properties"][_dimension] = dimension_schema()


class ClassificationError(RuntimeError):
    pass


class PassBudgetReached(RuntimeError):
    """Stop a pass cleanly so a later job can resume the same run."""

    pass


class TransientSupabaseError(ClassificationError):
    """A temporary database/API failure that should pause rather than fail a pass."""

    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()

    if not value:
        raise ClassificationError(f"{name} is missing.")

    return value


def first_row(response: Any, context: str) -> dict[str, Any]:
    data = getattr(response, "data", None)

    if isinstance(data, list) and data:
        return data[0]

    if isinstance(data, dict) and data:
        return data

    raise ClassificationError(f"No row while {context}.")


def _error_text(exc: Exception) -> str:
    return " ".join(
        str(value)
        for value in (exc, getattr(exc, "message", ""))
        if value
    ).casefold()


def is_transient_supabase_error(exc: Exception) -> bool:
    """Recognize reverse-proxy and transport failures from the Supabase API."""

    text = _error_text(exc)
    markers = (
        "'code': '525'",
        '\"code\": \"525\"',
        "ssl handshake failed",
        "json could not be generated",
        "cloudflare",
        "bad gateway",
        "gateway timeout",
        "service unavailable",
        "too many requests",
        "connection reset",
        "connection aborted",
        "connection timed out",
        "read timed out",
    )
    return any(marker in text for marker in markers)


def supabase_execute_with_retry(
    label: str,
    operation: Callable[[], Any],
) -> Any:
    """Retry only transient Supabase/API failures with bounded backoff."""

    last_error: Exception | None = None
    for attempt in range(1, SUPABASE_WRITE_MAX_ATTEMPTS + 1):
        try:
            return operation()
        except Exception as exc:
            if not is_transient_supabase_error(exc):
                raise
            last_error = exc
            if attempt == SUPABASE_WRITE_MAX_ATTEMPTS:
                break
            delay = SUPABASE_RETRY_DELAYS_SECONDS[
                min(attempt - 1, len(SUPABASE_RETRY_DELAYS_SECONDS) - 1)
            ]
            print(
                f"Warning: transient Supabase error while {label}; "
                f"retrying in {delay}s ({attempt}/{SUPABASE_WRITE_MAX_ATTEMPTS}).",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)

    raise TransientSupabaseError(
        f"Supabase remained temporarily unavailable while {label}: {last_error}"
    ) from last_error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Classify the current Observatory collection with resumable "
            "Coverage and Event lens passes."
        )
    )
    parser.add_argument(
        "--time-budget-minutes",
        type=float,
        default=0,
        help=(
            "Stop cleanly after this many minutes and leave the run "
            "resumable. Zero disables the application-level budget."
        ),
    )
    parser.add_argument(
        "--status-output",
        default="",
        help="Optional JSON path reporting whether the full run completed.",
    )
    args = parser.parse_args()
    if args.time_budget_minutes < 0:
        parser.error("--time-budget-minutes cannot be negative")
    return args


def write_pass_status(
    path_value: str,
    *,
    complete: bool,
    classification_run_id: str,
    run_key: str,
    classified: int,
    attempted: int,
    reason: str,
) -> None:
    if not path_value:
        return

    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "stage7c_pass_status_v1",
                "complete": complete,
                "classification_run_id": classification_run_id,
                "run_key": run_key,
                "classified_count": classified,
                "attempted_qwen_count": attempted,
                "reason": reason,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def latest_collection(client: Client) -> dict[str, Any]:
    response = (
        client.table("collection_runs")
        .select("run_id,run_key,started_at,status")
        .in_("status", ["success", "partial"])
        .order("started_at", desc=True)
        .limit(1)
        .execute()
    )

    return first_row(
        response,
        "reading latest collection run",
    )


def assert_event_resolution_complete(client: Client) -> None:
    response = (
        client.table("events")
        .select("event_id", count="exact")
        .eq("clustering_method", EVENT_METHOD)
        .eq("event_state", "pending_review")
        .execute()
    )

    count = int(
        getattr(response, "count", None)
        or len(getattr(response, "data", None) or [])
    )

    if count:
        raise ClassificationError(
            f"{count} Stage 7B.3 event(s) are still pending review. "
            "Upload validation/event_assignment_reviews.csv and run "
            "'Apply Event Assignment Reviews' before Stage 7C."
        )


def load_translations(
    client: Client,
    article_ids: list[str],
) -> dict[str, dict[str, Any]]:
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


def parse_source_metadata(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def compact_evidence_text(value: Any, max_chars: int = MAX_ARTICLE_EVIDENCE_CHARS) -> str:
    text = re.sub(r"[ \t]+", " ", str(value or ""))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= max_chars:
        return text
    head_chars = int(max_chars * 0.72)
    tail_chars = max_chars - head_chars
    return f"{text[:head_chars].rstrip()}\n\n[Middle shortened for classification]\n\n{text[-tail_chars:].lstrip()}"


def load_current_full_text(client: Client, article_ids: list[str]) -> dict[str, dict[str, Any]]:
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
        # Recount the supplied multilingual text; legacy whitespace counts can
        # be 1 for an entire Chinese article.
        words = evidence_unit_count(body)
        if article_id and article_id not in result and body and words >= MIN_FULL_TEXT_WORDS:
            result[article_id] = {**row, "body_text": body, "word_count": words}
    return result


def load_current_articles(
    client: Client,
    run_id: str,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    obs_response = (
        client.table("article_observations")
        .select(
            "article_id,search_rank,search_country_iso3,"
            "search_language"
        )
        .eq("run_id", run_id)
        .execute()
    )

    observations = getattr(obs_response, "data", None) or []

    if not observations:
        raise ClassificationError(
            "Latest collection contains no article observations."
        )

    meta: dict[str, dict[str, Any]] = {}

    for row in observations:
        aid = str(row["article_id"])

        item = meta.setdefault(
            aid,
            {
                "rank": 9999,
                "search_markets": set(),
                "search_languages": set(),
            },
        )

        if row.get("search_rank") is not None:
            item["rank"] = min(
                item["rank"],
                int(row["search_rank"]),
            )

        if row.get("search_country_iso3"):
            item["search_markets"].add(
                str(row["search_country_iso3"])
            )

        if row.get("search_language"):
            item["search_languages"].add(
                str(row["search_language"])
            )

    ids = sorted(meta)
    rows: list[dict[str, Any]] = []

    for start in range(0, len(ids), 150):
        response = (
            client.table("articles")
            .select(
                "article_id,headline,publisher,canonical_url,"
                "published_at,first_seen_at,last_seen_at,"
                "source_metadata"
            )
            .in_("article_id", ids[start:start + 150])
            .execute()
        )

        rows.extend(getattr(response, "data", None) or [])

    translations = load_translations(client, ids)
    full_text_map = load_current_full_text(client, ids)

    articles = []

    for row in rows:
        aid = str(row["article_id"])
        original = str(row.get("headline") or "").strip()

        if not original:
            continue

        trans = translations.get(aid) or {}

        english = str(
            trans.get("translated_headline")
            or original
        ).strip()

        md = parse_source_metadata(
            row.get("source_metadata")
        )

        snippet = ""

        for key in (
            "snippet",
            "description",
            "summary",
            "source_snippet",
        ):
            value = md.get(key)

            if value and str(value).strip():
                snippet = str(value).strip()
                break

        full_text = full_text_map.get(aid) or {}
        if full_text.get("body_text"):
            evidence_text = str(full_text["body_text"])
            content_basis = "full_text"
        else:
            # Preserve snippets for the owner's private audit, but never pass
            # them to the model.  A missing full article body is a source
            # availability state, not permission to fall back to a headline.
            evidence_text = ""
            content_basis = "headline_and_snippet" if snippet else "headline_only"

        date_value = row.get("published_at") or row.get("first_seen_at")

        articles.append(
            {
                "article_id": aid,
                "headline_original": original,
                "headline_english": english or original,
                "source_language": str(
                    trans.get("source_language_iso2")
                    or "und"
                ),
                "publisher": str(
                    row.get("publisher")
                    or "Unknown source"
                ),
                "url": row.get("canonical_url"),
                "date": str(date_value),
                "snippet": snippet,
                "evidence_text": evidence_text,
                "content_basis": content_basis,
                "evidence_word_count": int(full_text.get("word_count") or 0),
                "retrieval_method": str(full_text.get("retrieval_method") or ""),
                "search_rank": meta[aid]["rank"],
                # Search markets are preserved for audit but NEVER supplied to
                # the classifier as event-country evidence.
                "search_markets": sorted(
                    meta[aid]["search_markets"]
                ),
            }
        )

    articles.sort(
        key=lambda row: (
            row["date"],
            row["search_rank"],
            row["headline_english"].casefold(),
        )
    )

    return articles, meta


def load_active_current_events(
    client: Client,
    current_article_ids: list[str],
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    links: list[dict[str, Any]] = []

    for start in range(0, len(current_article_ids), 150):
        response = (
            client.table("event_articles")
            .select("event_id,article_id,is_canonical_source")
            .in_(
                "article_id",
                current_article_ids[start:start + 150],
            )
            .execute()
        )

        links.extend(getattr(response, "data", None) or [])

    event_ids = sorted(
        {
            str(row["event_id"])
            for row in links
        }
    )

    if not event_ids:
        raise ClassificationError(
            "No production events are linked to the latest collection."
        )

    event_rows: list[dict[str, Any]] = []

    for start in range(0, len(event_ids), 100):
        response = (
            client.table("events")
            .select(
                "event_id,event_title,event_summary,event_date,"
                "first_seen_at,last_seen_at,event_state,"
                "clustering_method"
            )
            .in_("event_id", event_ids[start:start + 100])
            .eq("event_state", "active")
            .eq("clustering_method", EVENT_METHOD)
            .execute()
        )

        event_rows.extend(getattr(response, "data", None) or [])

    active_ids = {
        str(row["event_id"])
        for row in event_rows
    }

    membership: dict[str, list[str]] = defaultdict(list)

    for row in links:
        eid = str(row["event_id"])
        aid = str(row["article_id"])

        if eid in active_ids:
            membership[eid].append(aid)

    assigned_articles = {
        aid
        for aids in membership.values()
        for aid in aids
    }

    missing = sorted(
        set(current_article_ids)
        - assigned_articles
    )

    if missing:
        raise ClassificationError(
            f"{len(missing)} current article(s) have no active production "
            "event after Stage 7B.3 review resolution. "
            "Do not classify until event assignments are complete."
        )

    event_rows.sort(
        key=lambda row: (
            str(row.get("event_date") or ""),
            str(row.get("event_title") or ""),
        )
    )

    return event_rows, membership


def load_codebook(client: Client) -> dict[str, Any]:
    response = (
        client.table("codebook_versions")
        .select(
            "codebook_version_id,version_name,prompt_text,hierarchy"
        )
        .eq("version_name", CODEBOOK_VERSION)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )

    return first_row(
        response,
        f"loading active codebook {CODEBOOK_VERSION}",
    )


def register_model(client: Client) -> tuple[str, str]:
    revision = (
        HfApi().model_info(QWEN_REPO).sha
        or "unknown"
    )

    response = (
        client.table("model_versions")
        .upsert(
            {
                "provider": "huggingface",
                "model_name": QWEN_REPO,
                "model_revision": revision,
                "task": "dual_lens_empowerment_classification",
                "language_scope": "original_plus_english",
                "notes": (
                    "Qwen3-4B GGUF Q4_K_M via llama.cpp. "
                    "JSON-object mode with prompt-only fallback and client-side validation; "
                    "diagnostic self-confidence is reported but does not "
                    "control the index or review queue. "
                    "Full article body evidence is required for every model "
                    "call; unavailable sources are recorded without a model "
                    "classification. Coverage articles classified once; "
                    "singleton events inherit article classification."
                ),
            },
            on_conflict=(
                "provider,model_name,model_revision,task"
            ),
        )
        .select("model_version_id")
        .execute()
    )

    return (
        str(
            first_row(
                response,
                "registering Qwen classification model",
            )["model_version_id"]
        ),
        revision,
    )


def start_classification_run(
    client: Client,
    *,
    collection_run_id: str,
    codebook_version_id: str,
    model_version_id: str,
) -> tuple[str, str]:
    now = utc_now()
    run_key = now.strftime("dual_lens_%Y%m%dT%H%M%SZ")

    response = (
        client.table("classification_runs")
        .insert(
            {
                "collection_run_id": collection_run_id,
                "codebook_version_id": codebook_version_id,
                "model_version_id": model_version_id,
                "run_key": run_key,
                "started_at": iso_z(now),
                "status": "running",
                "classifier_version": CLASSIFIER_VERSION,
            }
        )
        .select("classification_run_id")
        .execute()
    )

    return (
        str(
            first_row(
                response,
                "starting classification run",
            )["classification_run_id"]
        ),
        run_key,
    )


def resume_or_start_classification_run(
    client: Client,
    *,
    collection_run_id: str,
    codebook_version_id: str,
    model_version_id: str,
) -> tuple[str, str, bool]:
    """Resume the newest interrupted run for this exact classifier lineage."""

    response = (
        client.table("classification_runs")
        .select("classification_run_id,run_key,started_at,status,classified_count")
        .eq("collection_run_id", collection_run_id)
        .eq("codebook_version_id", codebook_version_id)
        .eq("model_version_id", model_version_id)
        .eq("classifier_version", CLASSIFIER_VERSION)
        .in_("status", ["running", "paused", "failed"])
        .order("started_at", desc=True)
        .execute()
    )
    rows = getattr(response, "data", None) or []

    for row in rows:
        status = str(row.get("status") or "")
        has_saved_work = int(row.get("classified_count") or 0) > 0
        if status not in {"running", "paused"} and not (
            status == "failed" and has_saved_work
        ):
            continue
        classification_run_id = str(row["classification_run_id"])
        run_key = str(row["run_key"])
        (
            client.table("classification_runs")
            .update(
                {
                    "status": "running",
                    "completed_at": None,
                    "classifier_version": CLASSIFIER_VERSION,
                }
            )
            .eq("classification_run_id", classification_run_id)
            .execute()
        )
        if status == "failed":
            print(
                f"Recovering partial Stage 7C run {run_key} after an earlier "
                "infrastructure failure.",
                flush=True,
            )
        return classification_run_id, run_key, True

    classification_run_id, run_key = start_classification_run(
        client,
        collection_run_id=collection_run_id,
        codebook_version_id=codebook_version_id,
        model_version_id=model_version_id,
    )
    return classification_run_id, run_key, False


def checkpoint_classification_run(
    client: Client,
    *,
    classification_run_id: str,
    attempted: int,
    classified: int,
    review_required: int,
) -> None:
    """Persist progress while keeping the run eligible for the next pass."""

    supabase_execute_with_retry(
        "checkpointing Stage 7C progress",
        lambda: (
            client.table("classification_runs")
            .update(
                {
                    "completed_at": None,
                    "status": "running",
                    "attempted_count": attempted,
                    "classified_count": classified,
                    "review_required_count": review_required,
                    "classifier_version": CLASSIFIER_VERSION,
                }
            )
            .eq("classification_run_id", classification_run_id)
            .execute()
        ),
    )


def finish_classification_run(
    client: Client,
    *,
    classification_run_id: str,
    status: str,
    attempted: int,
    classified: int,
    review_required: int,
) -> None:
    supabase_execute_with_retry(
        "finishing Stage 7C progress",
        lambda: (
            client.table("classification_runs")
            .update(
                {
                    "completed_at": iso_z(utc_now()),
                    "status": status,
                    "attempted_count": attempted,
                    "classified_count": classified,
                    "review_required_count": review_required,
                    "classifier_version": CLASSIFIER_VERSION,
                }
            )
            .eq(
                "classification_run_id",
                classification_run_id,
            )
            .execute()
        ),
    )


def load_saved_classifications(
    client: Client,
    *,
    classification_run_id: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Load complete rows already written by an earlier bounded pass."""

    fields = (
        "lens_classification_id,lens,article_id,event_id,ai_relevant,"
        "empowerment_status,empowerment_degree,unit_score,narrative_frame,"
        "distribution_breadth,dominant_dimension,ai_authority_shift,topic,"
        "geographic_scope,primary_country_iso3,country_iso3s,content_basis,"
        "confidence,reasoning,requires_review,review_reason,raw_output"
    )
    response = (
        client.table("lens_classifications")
        .select(fields)
        .eq("classification_run_id", classification_run_id)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    classification_ids = [
        str(row["lens_classification_id"])
        for row in rows
    ]

    dimensions: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for offset in range(0, len(classification_ids), 100):
        batch = classification_ids[offset : offset + 100]
        dimension_response = (
            client.table("lens_dimensions")
            .select(
                "lens_classification_id,dimension,present,direction,degree,"
                "confidence,reasoning"
            )
            .in_("lens_classification_id", batch)
            .execute()
        )
        for item in getattr(dimension_response, "data", None) or []:
            classification_id = str(item["lens_classification_id"])
            dimensions[classification_id][str(item["dimension"])] = {
                "present": bool(item.get("present")),
                "direction": str(item.get("direction") or "not_present"),
                "degree": int(item.get("degree") or 0),
                "confidence": float(item.get("confidence") or 0.0),
                "reasoning": str(item.get("reasoning") or ""),
            }

    coverage: dict[str, dict[str, Any]] = {}
    events: dict[str, dict[str, Any]] = {}

    for stored in rows:
        classification_id = str(stored["lens_classification_id"])
        stored_dimensions = dimensions.get(classification_id, {})
        if set(stored_dimensions) != VALID_DIMENSIONS:
            print(
                "Warning: ignoring incomplete saved classification until its "
                f"dimensions are repaired: {classification_id} has "
                f"{len(stored_dimensions)} of {len(VALID_DIMENSIONS)} dimensions.",
                file=sys.stderr,
                flush=True,
            )
            continue

        result = {
            "ai_relevant": bool(stored.get("ai_relevant")),
            "empowerment_status": str(stored["empowerment_status"]),
            "empowerment_degree": int(stored.get("empowerment_degree") or 0),
            "unit_score": float(stored.get("unit_score") or 0.0),
            "narrative_frame": str(stored["narrative_frame"]),
            "distribution_breadth": str(stored["distribution_breadth"]),
            "dominant_dimension": stored.get("dominant_dimension"),
            "dimensions": stored_dimensions,
            "ai_authority_shift": str(stored["ai_authority_shift"]),
            "topic": str(stored["topic"]),
            "geographic_scope": str(stored["geographic_scope"]),
            "country_iso3s": list(stored.get("country_iso3s") or []),
            "primary_country_iso3": stored.get("primary_country_iso3"),
            "content_basis": str(stored["content_basis"]),
            "confidence": float(stored.get("confidence") or 0.0),
            "reasoning": str(stored.get("reasoning") or ""),
            "requires_review": bool(stored.get("requires_review")),
            "review_reason": str(stored.get("review_reason") or ""),
            "_raw_model_output": stored.get("raw_output") or {},
            "_classification_id": classification_id,
        }

        lens = str(stored.get("lens") or "")
        if lens == "coverage" and stored.get("article_id"):
            unit_id = str(stored["article_id"])
            if unit_id in coverage:
                raise ClassificationError(
                    f"Duplicate saved coverage classification for {unit_id}."
                )
            coverage[unit_id] = result
        elif lens == "event" and stored.get("event_id"):
            unit_id = str(stored["event_id"])
            if unit_id in events:
                raise ClassificationError(
                    f"Duplicate saved event classification for {unit_id}."
                )
            events[unit_id] = result

    return coverage, events


def start_server() -> tuple[subprocess.Popen, Any]:
    log_path = Path(
        "/tmp/stage7c-qwen.log"
    )

    handle = log_path.open(
        "w",
        encoding="utf-8",
    )

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
            "32768",
            "-np",
            "1",
            "--jinja",
            "--chat-template-kwargs",
            '{"enable_thinking": false}',
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
                print(
                    log_path.read_text(
                        encoding="utf-8"
                    )[-12000:],
                    file=sys.stderr,
                )
            except Exception:
                pass

            raise ClassificationError(
                "Qwen llama.cpp server exited during startup."
            )

        try:
            response = requests.get(
                HEALTH_URL,
                timeout=3,
            )

            if response.ok:
                return process, handle

        except requests.RequestException:
            pass

        time.sleep(2)

    raise ClassificationError(
        "Qwen server did not become healthy."
    )


def stop_server(
    process: subprocess.Popen | None,
    handle: Any | None,
) -> None:
    if process is not None and process.poll() is None:
        process.terminate()

        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)

    if handle is not None:
        try:
            handle.close()
        except Exception:
            pass


def extract_json(text: str) -> dict[str, Any]:
    text = re.sub(
        r"<think>.*?</think>",
        "",
        text,
        flags=re.S,
    ).strip()

    # Remove common Markdown fences without assuming the model used them.
    text = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        text,
        flags=re.I | re.S,
    ).strip()

    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    # Do not use a greedy {.*} match: braces or quoted text inside reasoning
    # can otherwise make an invalid oversized fragment. Instead, scan for the
    # first independently decodable JSON object.
    decoder = json.JSONDecoder()

    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue

        if isinstance(value, dict):
            return value

    raise ClassificationError(
        f"No valid JSON object in model output: {text[:1000]}"
    )

def clean_iso3s(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []

    result = []

    for value in values:
        code = re.sub(
            r"[^A-Za-z]",
            "",
            str(value),
        ).upper()

        if len(code) == 3 and code not in result:
            result.append(code)

    return result


def clean_confidence(value: Any, *, field_name: str = "confidence") -> float:
    if value is None:
        raise ClassificationError(
            f"Structured output omitted required {field_name}."
        )

    try:
        value = float(value)
    except Exception as exc:
        raise ClassificationError(
            f"Structured output returned non-numeric {field_name}: {value!r}"
        ) from exc

    if not (0.0 <= value <= 1.0):
        raise ClassificationError(
            f"Structured output returned out-of-range {field_name}: {value}"
        )

    return value


def score_unit(
    status: str,
    degree: int,
) -> float | None:
    if status == "expanding":
        return round(degree / 3.0, 6)

    if status == "contracting":
        return round(-degree / 3.0, 6)

    if status in {
        "mixed",
        "non_empowerment",
    }:
        return 0.0

    return None


def validate_output(
    output: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    issues = []

    status = str(
        output.get("empowerment_status")
        or "unclear"
    ).strip()

    if status not in VALID_STATUS:
        issues.append("invalid empowerment_status")
        status = "unclear"

    ai_relevant = bool(
        output.get("ai_relevant", True)
    )

    try:
        degree = int(
            output.get(
                "empowerment_degree",
                0,
            )
        )
    except Exception:
        degree = 0
        issues.append("invalid empowerment_degree")

    degree = max(0, min(3, degree))

    if status in {"non_empowerment", "unclear"}:
        degree = 0
    elif degree == 0:
        degree = 1
        issues.append(
            "empowerment degree normalized from 0 to 1"
        )

    frame = str(
        output.get("narrative_frame")
        or "unclear"
    ).strip()

    if frame not in VALID_FRAME:
        frame = "unclear"
        issues.append("invalid narrative_frame")

    breadth = str(
        output.get("distribution_breadth")
        or "unclear"
    ).strip()

    if breadth not in VALID_BREADTH:
        breadth = "unclear"
        issues.append("invalid distribution_breadth")

    dominant_raw = str(
        output.get("dominant_dimension") or "none"
    ).strip()

    dominant = None if dominant_raw == "none" else dominant_raw

    if dominant is not None and dominant not in VALID_DIMENSIONS:
        raise ClassificationError(
            f"Structured output returned invalid dominant_dimension: {dominant_raw!r}"
        )

    authority = str(
        output.get("ai_authority_shift")
        or "unclear"
    ).strip()

    if authority not in VALID_AUTHORITY:
        authority = "unclear"
        issues.append("invalid ai_authority_shift")

    topic = str(
        output.get("topic")
        or "other"
    ).strip()

    if topic not in VALID_TOPIC:
        topic = "other"
        issues.append("invalid topic")

    scope = str(
        output.get("geographic_scope")
        or "unclear"
    ).strip()

    if scope not in VALID_SCOPE:
        scope = "unclear"
        issues.append("invalid geographic_scope")

    countries = clean_iso3s(
        output.get("country_iso3s")
    )

    if scope == "global":
        countries = []

    if scope == "country" and len(countries) > 1:
        countries = countries[:1]

    if scope in {"country", "multi_country"} and not countries:
        scope = "unclear"
        issues.append(
            "country scope without supported ISO3 country"
        )

    content_basis = "headline_only"

    if content_basis not in VALID_CONTENT_BASIS:
        content_basis = "headline_only"
        issues.append("invalid content_basis")

    confidence = clean_confidence(
        output.get("confidence"),
        field_name="overall confidence",
    )

    reasoning = str(
        output.get("reasoning")
        or ""
    ).strip()[:3000]

    dimensions_raw = (
        output.get("dimensions")
        if isinstance(
            output.get("dimensions"),
            dict,
        )
        else {}
    )

    dimensions: dict[str, dict[str, Any]] = {}

    for dimension in sorted(VALID_DIMENSIONS):
        item = (
            dimensions_raw.get(dimension)
            if isinstance(
                dimensions_raw.get(dimension),
                dict,
            )
            else {}
        )

        present = bool(
            item.get("present", False)
        )

        direction = item.get("direction")

        if direction is not None:
            direction = str(direction).strip()

        if not present:
            direction = None
            dim_degree = 0

        else:
            if direction == "not_present":
                raise ClassificationError(
                    f"{dimension} is present but direction=not_present."
                )

            if direction not in {
                "expanding",
                "contracting",
                "mixed",
                "unclear",
            }:
                raise ClassificationError(
                    f"Structured output returned invalid {dimension} direction: {direction!r}"
                )

            try:
                dim_degree = int(item.get("degree", 1))
            except Exception as exc:
                raise ClassificationError(
                    f"Structured output returned invalid {dimension} degree."
                ) from exc

            dim_degree = max(1, min(3, dim_degree))

        dimensions[dimension] = {
            "present": present,
            "direction": direction,
            "degree": dim_degree,
            "confidence": clean_confidence(
                item.get("confidence"),
                field_name=f"{dimension} confidence",
            ),
            "reasoning": str(
                item.get("reasoning")
                or ""
            ).strip()[:1500],
        }

    if status == "non_empowerment":
        for dimension in dimensions:
            if dimensions[dimension]["present"]:
                issues.append(
                    "non_empowerment output contained a present dimension; "
                    "dimensions normalized to absent"
                )

            dimensions[dimension] = {
                "present": False,
                "direction": None,
                "degree": 0,
                "confidence": dimensions[dimension][
                    "confidence"
                ],
                "reasoning": dimensions[dimension][
                    "reasoning"
                ],
            }

        dominant = None

    if dominant and not dimensions[dominant]["present"]:
        issues.append(
            "dominant_dimension was not marked present"
        )
        dominant = None

    # Confidence is an uncalibrated model self-rating. It remains visible for
    # diagnostics and stratified quality assessment, but it does not create a
    # routine manual-review burden or change the substantive score.
    requires_review = bool(
        status == "unclear"
        or issues
    )

    review_parts = list(issues)

    if status == "unclear":
        review_parts.append(
            "empowerment status unclear"
        )

    normalized = {
        "ai_relevant": ai_relevant,
        "empowerment_status": status,
        "empowerment_degree": degree,
        "unit_score": score_unit(
            status,
            degree,
        ),
        "narrative_frame": frame,
        "distribution_breadth": breadth,
        "dominant_dimension": dominant,
        "dimensions": dimensions,
        "ai_authority_shift": authority,
        "topic": topic,
        "geographic_scope": scope,
        "country_iso3s": countries,
        "primary_country_iso3": (
            countries[0]
            if countries
            else None
        ),
        "content_basis": content_basis,
        "confidence": confidence,
        "reasoning": reasoning,
        "requires_review": requires_review,
        "review_reason": "; ".join(
            dict.fromkeys(
                part
                for part in review_parts
                if part
            )
        ),
    }

    return normalized, issues


def _http_error_detail(response: requests.Response) -> str:
    """Return a compact llama.cpp error body for actionable CI logs."""
    body = str(response.text or "").strip()

    if not body:
        return "<empty response body>"

    try:
        parsed = response.json()
        body = json.dumps(
            parsed,
            ensure_ascii=False,
        )
    except Exception:
        pass

    return body[:3000]


def call_classifier(
    *,
    codebook_prompt: str,
    lens: str,
    evidence_text: str,
    content_basis: str,
) -> dict[str, Any]:
    lens_note = (
        "COVERAGE LENS: classify this one article exactly as communicated."
        if lens == "coverage"
        else
        "EVENT LENS: classify this one unique real-world development from "
        "all supplied source evidence. Do not give extra weight to repeated "
        "coverage. If sources frame the same event differently, narrative "
        "frame may be contested."
    )

    prompt = f"""
/no_think

{codebook_prompt}

## UNIT-SPECIFIC INSTRUCTION

{lens_note}

Do not use external knowledge.
Do not use publisher location as event geography unless the evidence itself
locates the development there.

Full article body evidence is required for this model call. A headline may
orient the reader, but it cannot independently support a classification.

The full article body may be written in any language. Treat the original-
language body as the evidence. English headline normalisation is an aid for
matching and review only. Never reduce the evidence status merely because the
source body is not English.

Confidence is a diagnostic self-rating of categorical certainty, not a rating
of how much source text was available. Use confidence 0 only when no
defensible categorical judgement can be made from the supplied full body.
All confidence fields, including each dimension, must be numbers from 0 to 1
in steps of 0.05, for example 0.85. Never use percentages or the degree scale
for confidence. Degree uses integers from 0 to 3; confidence does not.
When a dimension is absent, use present=false, direction=not_present, degree=0.
When it is present, use present=true, a directional label, and degree=1, 2 or 3.

Return exactly one JSON object and no surrounding prose.

Required top-level keys:
ai_relevant, empowerment_status, empowerment_degree, narrative_frame,
distribution_breadth, dominant_dimension, dimensions, ai_authority_shift,
topic, geographic_scope, country_iso3s, confidence, reasoning.

The dimensions object must contain exactly:
operational, creative, agentic, normative.

Each dimension must contain:
present, direction, degree, confidence, reasoning.

## EVIDENCE

{evidence_text}
""".strip()

    if content_basis not in VALID_CONTENT_BASIS:
        raise ClassificationError(
            f"Invalid deterministic content_basis: {content_basis}"
        )

    # Keep every attempt structured and disable Qwen3 thinking at the API
    # boundary. /no_think alone did not prevent empty/truncated final answers.
    request_modes = [
        {
            "name": "schema",
            "extra": {
                "response_format": {
                    "type": "json_object",
                    "schema": CLASSIFICATION_JSON_SCHEMA,
                },
            },
            "temperature": 0.2,
        },
        {
            "name": "schema_retry",
            "extra": {"response_format": {"type": "json_object", "schema": CLASSIFICATION_JSON_SCHEMA}},
            "temperature": 0.2,
        },
        {
            "name": "schema_retry_larger",
            "extra": {"response_format": {"type": "json_object", "schema": CLASSIFICATION_JSON_SCHEMA}},
            "temperature": 0.5,
        },
    ]

    last_error: Exception | None = None

    for attempt, mode in enumerate(
        request_modes,
        start=1,
    ):
        try:
            payload = {
                "model": f"{QWEN_REPO}:{QWEN_QUANT}",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a conservative research coder for the "
                            "AI Empowerment Observatory. Follow the supplied "
                            "codebook exactly and return only JSON."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                "temperature": mode["temperature"],
                "top_p": 0.8,
                "max_tokens": 1600 + (attempt - 1) * 800,
                "chat_template_kwargs": {"enable_thinking": False},
                "stream": False,
                **mode["extra"],
            }

            response = requests.post(
                SERVER_URL,
                json=payload,
                timeout=300,
            )

            if not response.ok:
                raise ClassificationError(
                    f"llama.cpp HTTP {response.status_code} "
                    f"in {mode['name']} mode: "
                    f"{_http_error_detail(response)}"
                )

            response_data = response.json()

            raw_json, diagnostics = response_result(response_data, CLASSIFICATION_JSON_SCHEMA)
            normalized, _ = validate_output(raw_json)
            normalized["content_basis"] = content_basis
            normalized["_raw_model_output"] = raw_json
            normalized["_raw_model_output"]["generation"] = {**diagnostics, "transport_version": TRANSPORT_VERSION, "thinking": False}
            normalized["_structured_output_mode"] = mode["name"]

            if attempt > 1:
                print(
                    "Stage 7C structured output recovered with "
                    f"{mode['name']} mode.",
                    file=sys.stderr,
                    flush=True,
                )

            return normalized

        except (
            requests.RequestException,
            KeyError,
            TypeError,
            ValueError,
            ClassificationError,
            ModelOutputError,
        ) as exc:
            last_error = exc
            print(
                "Warning: Stage 7C classification request "
                f"{attempt}/{len(request_modes)} "
                f"({mode['name']}) failed: {exc}",
                file=sys.stderr,
                flush=True,
            )

            if attempt < len(request_modes):
                time.sleep(2)

    raise ClassificationError(
        "Qwen failed to return a valid classification after all "
        f"fallback modes: {last_error}"
    )


def unavailable_full_body_result(
    *,
    title: str,
    source_basis: str,
    lens: str,
) -> dict[str, Any]:
    """Record an unavailable source without asking the model to guess.

    The source remains in the current collection so the release and private
    audit can account for it.  Its substantive classification is deliberately
    not inferred from its title, snippet, event summary, or external knowledge.
    ``content_basis`` stays within the live database's existing vocabulary;
    ``raw_output`` records that no model input was supplied.
    """
    safe_basis = source_basis if source_basis in VALID_CONTENT_BASIS else "headline_only"
    dimensions = {
        dimension: {
            "present": False,
            "direction": None,
            "degree": 0,
            "confidence": 0.0,
            "reasoning": "No full article body was available for this dimension.",
        }
        for dimension in sorted(VALID_DIMENSIONS)
    }
    reason = "Full article body unavailable; model classification was not run."
    return {
        # The source passed the collection's AI-news scope.  This is not a
        # model judgement about its substance, and keeps the unit visible in
        # the release-level provenance and full-body recovery audit.
        "ai_relevant": True,
        "empowerment_status": "unclear",
        "empowerment_degree": 0,
        "unit_score": None,
        "narrative_frame": "unclear",
        "distribution_breadth": "unclear",
        "dominant_dimension": None,
        "dimensions": dimensions,
        "ai_authority_shift": "unclear",
        "topic": "other",
        "geographic_scope": "unclear",
        "country_iso3s": [],
        "primary_country_iso3": None,
        "content_basis": safe_basis,
        "confidence": 0.0,
        "reasoning": reason,
        "requires_review": True,
        "review_reason": reason,
        "_raw_model_output": {
            "classification_not_run": True,
            "reason": "full_article_body_unavailable",
            "input_policy": FULL_BODY_REQUIRED_POLICY,
            "lens": lens,
            "title_for_audit": title,
            "source_basis": safe_basis,
        },
    }


def article_evidence(article: dict[str, Any]) -> str:
    basis = str(article.get("content_basis") or "headline_only")
    evidence = str(article.get("evidence_text") or "")
    if basis != "full_text":
        return f"""
Lens unit: one news article
Publisher: {article["publisher"]}
Date: {article["date"]}
Source language: {article.get("source_language") or "not confidently detected"}
Original headline: {article["headline_original"]}
English normalization: {article["headline_english"]}
Full article body: unavailable
Model input policy: {FULL_BODY_REQUIRED_POLICY}
""".strip()

    return f"""
Lens unit: one news article
Publisher: {article["publisher"]}
Date: {article["date"]}
Source language: {article.get("source_language") or "not confidently detected"}
Original headline: {article["headline_original"]}
English normalization: {article["headline_english"]}
Collected full article body: {evidence}
Evidence basis available: full_text
""".strip()


def event_evidence(
    event: dict[str, Any],
    members: list[dict[str, Any]],
) -> str:
    if any(str(article.get("content_basis") or "") != "full_text" for article in members):
        raise ClassificationError(
            "Event evidence may include only sources with a stored full article body."
        )

    blocks = []

    per_source_limit = max(2600, min(MAX_ARTICLE_EVIDENCE_CHARS, MAX_EVENT_EVIDENCE_CHARS // max(1, len(members))))
    for index, article in enumerate(
        members,
        start=1,
    ):
        blocks.append(
            f"""
Source {index}
Publisher: {article["publisher"]}
Date: {article["date"]}
Source language: {article.get("source_language") or "not confidently detected"}
Original headline: {article["headline_original"]}
English normalization: {article["headline_english"]}
Full article body: {compact_evidence_text(article.get("evidence_text") or "", per_source_limit)}
""".strip()
        )

    return f"""
Lens unit: one unique real-world event
Canonical event title: {event.get("event_title") or ""}
Event date: {event.get("event_date") or ""}
Number of full-body source articles: {len(members)}

{chr(10).join(blocks)}

Model input policy: {FULL_BODY_REQUIRED_POLICY}
""".strip()


def insert_classification(
    client: Client,
    *,
    classification_run_id: str,
    lens: str,
    unit_id: str,
    result: dict[str, Any],
    derived_from_id: str | None = None,
) -> str:
    payload = {
        "classification_run_id": classification_run_id,
        "lens": lens,
        "article_id": (
            unit_id
            if lens == "coverage"
            else None
        ),
        "event_id": (
            unit_id
            if lens == "event"
            else None
        ),
        "derived_from_lens_classification_id": derived_from_id,
        "ai_relevant": result["ai_relevant"],
        "empowerment_status": result[
            "empowerment_status"
        ],
        "empowerment_degree": result[
            "empowerment_degree"
        ],
        "unit_score": result["unit_score"],
        "narrative_frame": result[
            "narrative_frame"
        ],
        "distribution_breadth": result[
            "distribution_breadth"
        ],
        "dominant_dimension": result[
            "dominant_dimension"
        ],
        "ai_authority_shift": result[
            "ai_authority_shift"
        ],
        "topic": result["topic"],
        "geographic_scope": result[
            "geographic_scope"
        ],
        "primary_country_iso3": result[
            "primary_country_iso3"
        ],
        "country_iso3s": result[
            "country_iso3s"
        ],
        "content_basis": result[
            "content_basis"
        ],
        "confidence": result["confidence"],
        "reasoning": result["reasoning"],
        "requires_review": result[
            "requires_review"
        ],
        "review_reason": (
            result["review_reason"]
            or None
        ),
        "raw_output": result.get(
            "_raw_model_output",
            result,
        ),
    }

    unit_field = "article_id" if lens == "coverage" else "event_id"

    def existing_classification_id() -> str | None:
        response = supabase_execute_with_retry(
            f"checking for an existing {lens} classification",
            lambda: (
                client.table("lens_classifications")
                .select("lens_classification_id")
                .eq("classification_run_id", classification_run_id)
                .eq("lens", lens)
                .eq(unit_field, unit_id)
                .limit(2)
                .execute()
            ),
        )
        rows = getattr(response, "data", None) or []
        if len(rows) > 1:
            raise ClassificationError(
                f"Duplicate {lens} classifications already exist for {unit_id}."
            )
        return str(rows[0]["lens_classification_id"]) if rows else None

    classification_id = existing_classification_id()
    if classification_id is None:
        try:
            response = supabase_execute_with_retry(
                f"inserting {lens} classification",
                lambda: (
                    client.table("lens_classifications")
                    .insert(payload)
                    .select("lens_classification_id")
                    .execute()
                ),
            )
            classification_id = str(
                first_row(
                    response,
                    f"inserting {lens} classification",
                )["lens_classification_id"]
            )
        except Exception:
            # A gateway can lose the response after PostgreSQL accepted the
            # insert. Read before deciding that this unit has failed.
            classification_id = existing_classification_id()
            if classification_id is None:
                raise

    dimension_rows: list[dict[str, Any]] = []

    for dimension, item in result[
        "dimensions"
    ].items():
        dimension_rows.append(
            dimension_row_for_storage(
                classification_id=classification_id,
                dimension=dimension,
                item=item,
            )
        )

    existing_dimensions_response = supabase_execute_with_retry(
        f"checking dimensions for {lens} classification",
        lambda: (
            client.table("lens_dimensions")
            .select("dimension")
            .eq("lens_classification_id", classification_id)
            .execute()
        ),
    )
    existing_dimensions = {
        str(row.get("dimension") or "")
        for row in (getattr(existing_dimensions_response, "data", None) or [])
    }
    missing_dimensions = [
        row for row in dimension_rows
        if str(row["dimension"]) not in existing_dimensions
    ]
    if missing_dimensions:
        try:
            supabase_execute_with_retry(
                f"inserting dimensions for {lens} classification",
                lambda: client.table("lens_dimensions").insert(missing_dimensions).execute(),
            )
        except Exception:
            # As above, recover cleanly if the write reached PostgreSQL but the
            # HTTP response did not reach the runner.
            verify_response = supabase_execute_with_retry(
                f"verifying dimensions for {lens} classification",
                lambda: (
                    client.table("lens_dimensions")
                    .select("dimension")
                    .eq("lens_classification_id", classification_id)
                    .execute()
                ),
            )
            verified = {
                str(row.get("dimension") or "")
                for row in (getattr(verify_response, "data", None) or [])
            }
            expected = {str(row["dimension"]) for row in dimension_rows}
            if not expected.issubset(verified):
                raise

    return classification_id


def dimension_row_for_storage(
    *,
    classification_id: str,
    dimension: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    """Translate a classifier dimension into the live database shape.

    Saved dimensions are read as ``not_present`` so in-memory classification
    code has one explicit state for every dimension.  PostgreSQL stores the
    same absent state as a NULL ``direction`` with a *non-null* degree of 0.
    The other values remain ordinary diagnostic fields.  That is also the
    shape used by the already-successful coverage classifications; this
    adapter prevents a resumed singleton event from writing the display value
    ``not_present`` back to the database.
    """
    present = bool(item.get("present"))
    row: dict[str, Any] = {
        "lens_classification_id": classification_id,
        "dimension": dimension,
        "present": present,
    }

    if not present:
        row.update(
            {
                "direction": None,
                # degree is NOT NULL in the live table.  An absent dimension
                # is represented by degree 0, not a NULL measurement.
                "degree": 0,
                "confidence": float(item.get("confidence") or 0.0),
                "reasoning": item.get("reasoning") or None,
            }
        )
        return row

    row.update(
        {
            "direction": item.get("direction"),
            "degree": item.get("degree"),
            "confidence": item.get("confidence"),
            "reasoning": item.get("reasoning") or None,
        }
    )
    return row


def copy_for_singleton_event(
    source: dict[str, Any],
) -> dict[str, Any]:
    result = {
        key: source[key]
        for key in (
            "ai_relevant",
            "empowerment_status",
            "empowerment_degree",
            "unit_score",
            "narrative_frame",
            "distribution_breadth",
            "dominant_dimension",
            "dimensions",
            "ai_authority_shift",
            "topic",
            "geographic_scope",
            "country_iso3s",
            "primary_country_iso3",
            "content_basis",
            "confidence",
            "reasoning",
            "requires_review",
            "review_reason",
        )
    }

    result["_raw_model_output"] = {
        "derived_from_singleton_article": True,
        "source_article_id": source[
            "_unit_id"
        ],
        "source_classification_id": source[
            "_classification_id"
        ],
        "source_output": source.get(
            "_raw_model_output",
            {},
        ),
    }

    return result


def deterministic_hash(value: str) -> str:
    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def select_audit(
    coverage: list[dict[str, Any]],
    event_rows: list[dict[str, Any]],
) -> set[str]:
    selected: set[str] = set()

    eligible = [
        row
        for row in coverage
        if (
            row["ai_relevant"]
            and not row["requires_review"]
        )
    ]

    strata = [
        "expanding",
        "contracting",
        "mixed",
        "non_empowerment",
    ]

    for status in strata:
        candidates = sorted(
            [
                row
                for row in eligible
                if row["empowerment_status"] == status
            ],
            key=lambda row: deterministic_hash(
                row["_classification_id"]
            ),
        )

        for row in candidates[:2]:
            selected.add(
                row["_classification_id"]
            )

    # Ensure dimension diversity where available.
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
                for row in eligible
                if (
                    row["_classification_id"]
                    not in selected
                    and row["dimensions"][
                        dimension
                    ]["present"]
                )
            ],
            key=lambda row: deterministic_hash(
                row["_classification_id"]
            ),
        )

        if candidates:
            selected.add(
                candidates[0][
                    "_classification_id"
                ]
            )

    for row in sorted(
        eligible,
        key=lambda item: deterministic_hash(
            item["_classification_id"]
        ),
    ):
        if len(selected) >= AUDIT_TARGET:
            break

        selected.add(
            row["_classification_id"]
        )

    # Multi-source resolved events are particularly important to the Event Lens.
    multi_events = sorted(
        [
            row
            for row in event_rows
            if row.get("_member_count", 1) > 1
        ],
        key=lambda item: deterministic_hash(
            item["_classification_id"]
        ),
    )

    for row in multi_events[
        :MULTI_EVENT_AUDIT_MAX
    ]:
        selected.add(
            row["_classification_id"]
        )

    return selected


def share_dict(
    values: list[str],
    allowed: list[str],
) -> dict[str, float]:
    if not values:
        return {
            key: 0.0
            for key in allowed
        }

    counts = Counter(values)
    total = len(values)

    return {
        key: round(
            counts.get(key, 0)
            / total,
            4,
        )
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
            for dimension in sorted(
                VALID_DIMENSIONS
            )
        }

    return {
        dimension: round(
            sum(
                1
                for row in ai_rows
                if row["dimensions"][
                    dimension
                ]["present"]
            )
            / len(ai_rows),
            4,
        )
        for dimension in sorted(
            VALID_DIMENSIONS
        )
    }


def summarize_lens(
    rows: list[dict[str, Any]],
    lens: str,
    country: str | None = None,
) -> dict[str, Any]:
    if country is None:
        scoped = list(rows)
    else:
        scoped = [
            row
            for row in rows
            if row.get(
                "primary_country_iso3"
            ) == country
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
            sum(
                float(row["unit_score"])
                for row in scored
            )
            / len(scored)
            * 100.0,
            4,
        )
        if scored
        else None
    )

    mean_confidence = (
        round(
            sum(
                float(row["confidence"])
                for row in ai_rows
            )
            / len(ai_rows),
            4,
        )
        if ai_rows
        else None
    )

    return {
        "lens": lens,
        "scope": (
            "global"
            if country is None
            else "country"
        ),
        "country_iso3": country,
        "unit_count_total": len(scoped),
        "unit_count_ai_relevant": len(
            ai_rows
        ),
        "unit_count_scored": len(scored),
        "review_required_count": sum(
            1
            for row in scoped
            if row["requires_review"]
        ),
        "empowerment_index": index_value,
        "mean_confidence": mean_confidence,
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
        "dimension_distribution": dimension_share(
            scoped
        ),
        "signal_ready": bool(
            len(scored)
            >= (
                1
                if country is None
                else MIN_COUNTRY_SIGNAL_N
            )
        ),
    }


def write_index_snapshot(
    client: Client,
    classification_run_id: str,
    summary: dict[str, Any],
) -> None:
    (
        client.table("lens_index_snapshots")
        .insert(
            {
                "classification_run_id": classification_run_id,
                **summary,
            }
        )
        .execute()
    )


def amplification(
    coverage: dict[str, Any],
    event: dict[str, Any],
) -> dict[str, Any]:
    cov_index = coverage[
        "empowerment_index"
    ]
    evt_index = event[
        "empowerment_index"
    ]

    gap = (
        round(
            float(cov_index)
            - float(evt_index),
            4,
        )
        if (
            cov_index is not None
            and evt_index is not None
        )
        else None
    )

    event_n = event[
        "unit_count_ai_relevant"
    ]

    ratio = (
        round(
            coverage[
                "unit_count_ai_relevant"
            ]
            / event_n,
            4,
        )
        if event_n
        else None
    )

    return {
        "scope": coverage["scope"],
        "country_iso3": coverage[
            "country_iso3"
        ],
        "coverage_index": cov_index,
        "event_index": evt_index,
        "directional_amplification_gap": gap,
        "coverage_unit_count": coverage[
            "unit_count_ai_relevant"
        ],
        "event_unit_count": event_n,
        "coverage_event_ratio": ratio,
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


def write_amplification(
    client: Client,
    classification_run_id: str,
    row: dict[str, Any],
) -> None:
    (
        client.table("amplification_snapshots")
        .insert(
            {
                "classification_run_id": classification_run_id,
                **row,
            }
        )
        .execute()
    )


def review_card(
    row: dict[str, Any],
) -> dict[str, Any]:
    return {
        "lens_classification_id": row[
            "_classification_id"
        ],
        "lens": row["_lens"],
        "unit_id": row["_unit_id"],
        "title": row["_title"],
        "publisher_or_sources": row.get(
            "_publisher_or_sources",
            "",
        ),
        "date": row.get("_date", ""),
        "evidence": row.get(
            "_review_evidence",
            "",
        ),
        "ai_relevant": row[
            "ai_relevant"
        ],
        "empowerment_status": row[
            "empowerment_status"
        ],
        "empowerment_degree": row[
            "empowerment_degree"
        ],
        "unit_score": row["unit_score"],
        "narrative_frame": row[
            "narrative_frame"
        ],
        "distribution_breadth": row[
            "distribution_breadth"
        ],
        "dominant_dimension": row[
            "dominant_dimension"
        ],
        "dimensions": row["dimensions"],
        "ai_authority_shift": row[
            "ai_authority_shift"
        ],
        "topic": row["topic"],
        "geographic_scope": row[
            "geographic_scope"
        ],
        "country_iso3s": row[
            "country_iso3s"
        ],
        "confidence": row["confidence"],
        "reasoning": row["reasoning"],
        "requires_review": row[
            "requires_review"
        ],
        "review_reason": row[
            "review_reason"
        ],
        "audit_selected": row.get(
            "_audit_selected",
            False,
        ),
        "audit_reason": row.get(
            "_audit_reason",
            "",
        ),
    }


def main() -> int:
    args = parse_args()
    pass_deadline = (
        time.monotonic() + args.time_budget_minutes * 60
        if args.time_budget_minutes > 0
        else None
    )

    client: Client = create_client(
        required_env("SUPABASE_URL"),
        required_env(
            "SUPABASE_SECRET_KEY"
        ),
    )

    assert_event_resolution_complete(
        client
    )

    collection = latest_collection(
        client
    )

    articles, _ = load_current_articles(
        client,
        str(collection["run_id"]),
    )

    article_map = {
        row["article_id"]: row
        for row in articles
    }

    events, membership = load_active_current_events(
        client,
        [
            row["article_id"]
            for row in articles
        ],
    )

    codebook = load_codebook(client)

    model_version_id, model_revision = (
        register_model(client)
    )

    classification_run_id, run_key, resumed = (
        resume_or_start_classification_run(
            client,
            collection_run_id=str(
                collection["run_id"]
            ),
            codebook_version_id=str(
                codebook[
                    "codebook_version_id"
                ]
            ),
            model_version_id=model_version_id,
        )
    )

    saved_coverage, saved_events = load_saved_classifications(
        client,
        classification_run_id=classification_run_id,
    )
    if resumed:
        print(
            "Resuming Stage 7C run "
            f"{run_key}: {len(saved_coverage)} coverage and "
            f"{len(saved_events)} event classifications already saved.",
            flush=True,
        )

    attempted = (
        len(articles)
        + sum(
            1
            for event in events
            if len(
                membership[
                    str(event["event_id"])
                ]
            ) > 1
        )
    )

    classified_count = 0
    qwen_calls = 0
    model_coverage_results: list[dict[str, Any]] = []
    model_sanity_checked = False
    coverage_results: list[
        dict[str, Any]
    ] = []
    event_results: list[
        dict[str, Any]
    ] = []

    process = None
    handle = None

    def require_pass_budget(stage: str) -> None:
        if pass_deadline is not None and time.monotonic() >= pass_deadline:
            raise PassBudgetReached(stage)

    try:
        current_article_ids_requiring_model = {
            str(article["article_id"])
            for article in articles
            if str(article.get("content_basis") or "") == "full_text"
        }
        current_multi_event_ids_requiring_model = {
            str(event["event_id"])
            for event in events
            if (
                len(membership[str(event["event_id"])]) > 1
                and any(
                    str(article_map[article_id].get("content_basis") or "")
                    == "full_text"
                    for article_id in membership[str(event["event_id"])]
                )
            )
        }
        needs_server = bool(
            current_article_ids_requiring_model.difference(saved_coverage)
            or current_multi_event_ids_requiring_model.difference(saved_events)
        )
        if needs_server:
            process, handle = start_server()

        # 1. COVERAGE LENS: every article gets a provenance record, but only
        # full-body sources are sent to the model.
        for index, article in enumerate(
            articles,
            start=1,
        ):
            print(
                f"[Coverage {index}/{len(articles)}] "
                f"{article['headline_english'][:100]}"
            )

            article_id = str(article["article_id"])
            saved_result = saved_coverage.get(article_id)

            if saved_result is not None:
                result = dict(saved_result)
                classification_id = str(result["_classification_id"])
                print(
                    "  -> resumed saved classification "
                    f"status={result['empowerment_status']} "
                    f"frame={result['narrative_frame']} "
                    f"confidence={result['confidence']:.2f}",
                    flush=True,
                )
            else:
                if str(article.get("content_basis") or "") == "full_text":
                    require_pass_budget("coverage")
                    qwen_calls += 1
                    result = call_classifier(
                        codebook_prompt=str(
                            codebook[
                                "prompt_text"
                            ]
                        ),
                        lens="coverage",
                        evidence_text=article_evidence(
                            article
                        ),
                        content_basis="full_text",
                    )
                    model_coverage_results.append(result)

                    print(
                        "  -> "
                        f"status={result['empowerment_status']} "
                        f"frame={result['narrative_frame']} "
                        f"confidence={result['confidence']:.2f}",
                        flush=True,
                    )
                else:
                    result = unavailable_full_body_result(
                        title=str(article.get("headline_english") or ""),
                        source_basis=str(article.get("content_basis") or "headline_only"),
                        lens="coverage",
                    )
                    print(
                        "  -> full article body unavailable; "
                        "model classification not run",
                        flush=True,
                    )

                classification_id = (
                    insert_classification(
                        client,
                        classification_run_id=classification_run_id,
                        lens="coverage",
                        unit_id=article_id,
                        result=result,
                    )
                )

            result.update(
                {
                    "_classification_id": classification_id,
                    "_unit_id": article_id,
                    "_lens": "coverage",
                    "_title": article[
                        "headline_english"
                    ],
                    "_publisher_or_sources": article[
                        "publisher"
                    ],
                    "_date": article["date"],
                    "_review_evidence": article_evidence(
                        article
                    ),
                }
            )

            coverage_results.append(
                result
            )

            classified_count += 1

            if len(model_coverage_results) >= 8 and not model_sanity_checked:
                model_sanity_checked = True
                if max(
                    row["confidence"]
                    for row in model_coverage_results
                ) == 0:
                    print(
                        "Warning: first 8 Qwen confidence self-ratings are 0. "
                        "Confidence is diagnostic only; Stage 7C will continue.",
                        file=sys.stderr,
                        flush=True,
                    )

                def is_default_collapse(row: dict[str, Any]) -> bool:
                    return bool(
                        row["empowerment_status"] == "unclear"
                        and row["narrative_frame"] == "unclear"
                        and row["distribution_breadth"] == "unclear"
                        and row["dominant_dimension"] is None
                        and row["ai_authority_shift"] == "unclear"
                        and row["topic"] == "other"
                        and not row["country_iso3s"]
                        and not row["reasoning"].strip()
                        and all(
                            not item["present"]
                            for item in row["dimensions"].values()
                        )
                    )

                if all(
                    is_default_collapse(row)
                    for row in model_coverage_results
                ):
                    raise ClassificationError(
                        "Structured-output sanity check failed: first 8 "
                        "classifications collapsed to empty/default labels."
                    )

        coverage_by_article = {
            row["_unit_id"]: row
            for row in coverage_results
        }

        # 2. EVENT LENS
        for index, event in enumerate(
            events,
            start=1,
        ):
            event_id = str(
                event["event_id"]
            )

            member_ids = membership[
                event_id
            ]

            members = [
                article_map[aid]
                for aid in member_ids
            ]
            full_body_members = [
                article
                for article in members
                if str(article.get("content_basis") or "") == "full_text"
            ]

            saved_result = saved_events.get(event_id)

            if saved_result is not None:
                result = dict(saved_result)
                classification_id = str(result["_classification_id"])
                print(
                    f"[Event {index}/{len(events)}] "
                    f"resumed saved classification: "
                    f"{event.get('event_title','')[:90]}"
                )
            else:
                if len(member_ids) == 1:
                    source = coverage_by_article[
                        member_ids[0]
                    ]

                    result = copy_for_singleton_event(
                        source
                    )

                    derived_from = source[
                        "_classification_id"
                    ]

                    print(
                        f"[Event {index}/{len(events)}] "
                        f"singleton reuse: "
                        f"{event.get('event_title','')[:90]}"
                    )

                elif not full_body_members:
                    source_basis = (
                        "headline_and_snippet"
                        if any(str(member.get("snippet") or "").strip() for member in members)
                        else "headline_only"
                    )
                    result = unavailable_full_body_result(
                        title=str(event.get("event_title") or ""),
                        source_basis=source_basis,
                        lens="event",
                    )
                    derived_from = None
                    print(
                        f"[Event {index}/{len(events)}] "
                        "no full article body; model classification not run: "
                        f"{event.get('event_title','')[:80]}"
                    )

                else:
                    require_pass_budget("event")
                    qwen_calls += 1
                    event_content_basis = (
                        "full_text"
                        if len(full_body_members) == 1
                        else "multiple_sources"
                    )

                    print(
                        f"[Event {index}/{len(events)}] "
                        f"full-body Qwen ({len(full_body_members)} of "
                        f"{len(member_ids)} sources): "
                        f"{event.get('event_title','')[:80]}"
                    )

                    result = call_classifier(
                        codebook_prompt=str(
                            codebook[
                                "prompt_text"
                            ]
                        ),
                        lens="event",
                        evidence_text=event_evidence(
                            event,
                            full_body_members,
                        ),
                        content_basis=event_content_basis,
                    )

                    derived_from = None

                classification_id = (
                    insert_classification(
                        client,
                        classification_run_id=classification_run_id,
                        lens="event",
                        unit_id=event_id,
                        result=result,
                        derived_from_id=derived_from,
                    )
                )

            # Populate event geography from the event-level classifier.
            (
                client.table("events")
                .update(
                    {
                        "primary_country_iso3": result[
                            "primary_country_iso3"
                        ],
                        "additional_country_iso3": result[
                            "country_iso3s"
                        ][1:],
                        "updated_at": iso_z(
                            utc_now()
                        ),
                    }
                )
                .eq("event_id", event_id)
                .execute()
            )

            result.update(
                {
                    "_classification_id": classification_id,
                    "_unit_id": event_id,
                    "_lens": "event",
                    "_title": str(
                        event.get("event_title")
                        or ""
                    ),
                    "_publisher_or_sources": (
                        ", ".join(
                            sorted(
                                {
                                    member[
                                        "publisher"
                                    ]
                                    for member in members
                                }
                            )
                        )
                    ),
                    "_date": str(
                        event.get("event_date")
                        or ""
                    ),
                    "_review_evidence": (
                        event_evidence(event, full_body_members)
                        if full_body_members
                        else (
                            "Lens unit: one unique real-world event\n"
                            f"Canonical event title: {event.get('event_title') or ''}\n"
                            "Full article body: unavailable\n"
                            f"Model input policy: {FULL_BODY_REQUIRED_POLICY}"
                        )
                    ),
                    "_member_count": len(
                        member_ids
                    ),
                }
            )

            event_results.append(
                result
            )

            classified_count += 1

        # 3. Small, stratified human QA sample.
        audit_ids = select_audit(
            coverage_results,
            event_results,
        )

        for row in (
            coverage_results
            + event_results
        ):
            if row[
                "_classification_id"
            ] in audit_ids:
                row[
                    "_audit_selected"
                ] = True

                row[
                    "_audit_reason"
                ] = (
                    "multi-source event audit"
                    if (
                        row["_lens"] == "event"
                        and row.get(
                            "_member_count",
                            1,
                        ) > 1
                    )
                    else
                    "stratified quality audit"
                )

                (
                    client.table(
                        "lens_classifications"
                    )
                    .update(
                        {
                            "audit_selected": True,
                            "audit_reason": row[
                                "_audit_reason"
                            ],
                        }
                    )
                    .eq(
                        "lens_classification_id",
                        row[
                            "_classification_id"
                        ],
                    )
                    .execute()
                )

        # 4. Index snapshots.
        global_coverage = summarize_lens(
            coverage_results,
            "coverage",
        )

        global_event = summarize_lens(
            event_results,
            "event",
        )

        write_index_snapshot(
            client,
            classification_run_id,
            global_coverage,
        )

        write_index_snapshot(
            client,
            classification_run_id,
            global_event,
        )

        global_amplification = (
            amplification(
                global_coverage,
                global_event,
            )
        )

        write_amplification(
            client,
            classification_run_id,
            global_amplification,
        )

        countries = sorted(
            {
                row[
                    "primary_country_iso3"
                ]
                for row in (
                    coverage_results
                    + event_results
                )
                if row.get(
                    "primary_country_iso3"
                )
            }
        )

        country_rows = []

        for country in countries:
            coverage_summary = (
                summarize_lens(
                    coverage_results,
                    "coverage",
                    country,
                )
            )

            event_summary = (
                summarize_lens(
                    event_results,
                    "event",
                    country,
                )
            )

            write_index_snapshot(
                client,
                classification_run_id,
                coverage_summary,
            )

            write_index_snapshot(
                client,
                classification_run_id,
                event_summary,
            )

            amp = amplification(
                coverage_summary,
                event_summary,
            )

            write_amplification(
                client,
                classification_run_id,
                amp,
            )

            country_rows.append(
                {
                    "country_iso3": country,
                    "coverage": coverage_summary,
                    "event": event_summary,
                    "amplification": amp,
                }
            )

        review_required_count = sum(
            1
            for row in (
                coverage_results
                + event_results
            )
            if row["requires_review"]
        )

        finish_classification_run(
            client,
            classification_run_id=classification_run_id,
            status="success",
            attempted=attempted,
            classified=classified_count,
            review_required=review_required_count,
        )

        review_queue = [
            review_card(row)
            for row in (
                coverage_results
                + event_results
            )
            if (
                row["requires_review"]
                or row.get(
                    "_audit_selected",
                    False,
                )
            )
        ]

        REVIEW_OUTPUT.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        REVIEW_OUTPUT.write_text(
            json.dumps(
                {
                    "meta": {
                        "stage": CLASSIFIER_VERSION,
                        "classification_run_id": classification_run_id,
                        "run_key": run_key,
                        "collection_run_key": collection[
                            "run_key"
                        ],
                        "model": QWEN_REPO,
                        "model_revision": model_revision,
                        "codebook": CODEBOOK_VERSION,
                        "coverage_article_count": len(
                            coverage_results
                        ),
                        "event_count": len(
                            event_results
                        ),
                        "multi_source_event_count": sum(
                            1
                            for row in event_results
                            if row.get(
                                "_member_count",
                                1,
                            ) > 1
                        ),
                        "qwen_call_count": qwen_calls,
                        "model_review_required_count": review_required_count,
                        "audit_selected_count": len(
                            audit_ids
                        ),
                        "review_queue_count": len(
                            review_queue
                        ),
                        "score_formula": (
                            "expanding +degree/3; contracting -degree/3; "
                            "mixed/non-empowerment 0; unclear excluded"
                        ),
                        "confidence_policy": (
                            "confidence is reported separately and does not "
                            "shrink the substantive empowerment score"
                        ),
                    },
                    "global": {
                        "coverage": global_coverage,
                        "event": global_event,
                        "amplification": global_amplification,
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
                        "stage": CLASSIFIER_VERSION,
                        "provisional": True,
                        "classification_run_id": classification_run_id,
                        "run_key": run_key,
                        "collection_run_key": collection[
                            "run_key"
                        ],
                        "codebook": CODEBOOK_VERSION,
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
                            "country_rule": (
                                "primary event country only in pilot country "
                                "aggregation; global/multi-country metadata "
                                "is retained separately"
                            ),
                            "country_signal_min_n": MIN_COUNTRY_SIGNAL_N,
                        },
                    },
                    "global": {
                        "coverage": global_coverage,
                        "event": global_event,
                        "amplification": global_amplification,
                    },
                    "countries": country_rows,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        print()
        print("Stage 7C complete")
        print(
            json.dumps(
                {
                    "coverage_articles": len(
                        coverage_results
                    ),
                    "unique_events": len(
                        event_results
                    ),
                    "multi_source_events": sum(
                        1
                        for row in event_results
                        if row.get(
                            "_member_count",
                            1,
                        ) > 1
                    ),
                    "qwen_calls": qwen_calls,
                    "model_review_required": review_required_count,
                    "audit_selected": len(
                        audit_ids
                    ),
                    "review_queue": len(
                        review_queue
                    ),
                    "coverage_index": global_coverage[
                        "empowerment_index"
                    ],
                    "event_index": global_event[
                        "empowerment_index"
                    ],
                    "directional_amplification_gap": global_amplification[
                        "directional_amplification_gap"
                    ],
                    "coverage_event_ratio": global_amplification[
                        "coverage_event_ratio"
                    ],
                },
                indent=2,
            )
        )

        write_pass_status(
            args.status_output,
            complete=True,
            classification_run_id=classification_run_id,
            run_key=run_key,
            classified=classified_count,
            attempted=attempted,
            reason="complete",
        )

        return 0

    except PassBudgetReached as exc:
        review_required_count = sum(
            1
            for row in coverage_results + event_results
            if row["requires_review"]
        )
        try:
            checkpoint_classification_run(
                client,
                classification_run_id=classification_run_id,
                attempted=attempted,
                classified=classified_count,
                review_required=review_required_count,
            )
        except TransientSupabaseError as checkpoint_error:
            print(
                f"Warning: could not update the pass checkpoint yet: {checkpoint_error}",
                file=sys.stderr,
                flush=True,
            )
        write_pass_status(
            args.status_output,
            complete=False,
            classification_run_id=classification_run_id,
            run_key=run_key,
            classified=classified_count,
            attempted=attempted,
            reason=f"time_budget_reached_before_{exc}",
        )
        print(
            "Stage 7C pass checkpointed cleanly after "
            f"{classified_count} saved classifications; the next pass will "
            f"resume run {run_key}.",
            flush=True,
        )
        return 0

    except TransientSupabaseError as exc:
        review_required_count = sum(
            1
            for row in coverage_results + event_results
            if row["requires_review"]
        )
        try:
            checkpoint_classification_run(
                client,
                classification_run_id=classification_run_id,
                attempted=attempted,
                classified=classified_count,
                review_required=review_required_count,
            )
        except TransientSupabaseError as checkpoint_error:
            print(
                f"Warning: could not update the transient-error checkpoint yet: {checkpoint_error}",
                file=sys.stderr,
                flush=True,
            )
        write_pass_status(
            args.status_output,
            complete=False,
            classification_run_id=classification_run_id,
            run_key=run_key,
            classified=classified_count,
            attempted=attempted,
            reason="transient_supabase_error",
        )
        print(
            "Stage 7C paused after a transient Supabase error. "
            f"The next pass will resume run {run_key} without discarding saved work.",
            file=sys.stderr,
            flush=True,
        )
        return 0

    except Exception as exc:
        if is_transient_supabase_error(exc):
            review_required_count = sum(
                1
                for row in coverage_results + event_results
                if row["requires_review"]
            )
            try:
                checkpoint_classification_run(
                    client,
                    classification_run_id=classification_run_id,
                    attempted=attempted,
                    classified=classified_count,
                    review_required=review_required_count,
                )
            except TransientSupabaseError as checkpoint_error:
                print(
                    f"Warning: could not update the raw transient-error checkpoint yet: {checkpoint_error}",
                    file=sys.stderr,
                    flush=True,
                )
            write_pass_status(
                args.status_output,
                complete=False,
                classification_run_id=classification_run_id,
                run_key=run_key,
                classified=classified_count,
                attempted=attempted,
                reason="transient_supabase_error",
            )
            print(
                "Stage 7C paused after a transient Supabase error. "
                f"The next pass will resume run {run_key} without discarding saved work.",
                file=sys.stderr,
                flush=True,
            )
            return 0
        try:
            finish_classification_run(
                client,
                classification_run_id=classification_run_id,
                status="failed",
                attempted=attempted,
                classified=classified_count,
                review_required=0,
            )
        except TransientSupabaseError as checkpoint_error:
            print(
                f"Warning: could not mark the failed run in Supabase: {checkpoint_error}",
                file=sys.stderr,
                flush=True,
            )
        write_pass_status(
            args.status_output,
            complete=False,
            classification_run_id=classification_run_id,
            run_key=run_key,
            classified=classified_count,
            attempted=attempted,
            reason="error",
        )
        raise

    finally:
        stop_server(
            process,
            handle,
        )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ClassificationError as exc:
        print(
            f"Stage 7C failed: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
