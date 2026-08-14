#!/usr/bin/env python3
"""Stage 7B.3 — precision-first article-to-event assignment.

Coverage Lens:
    every collected article remains untouched.

Event Lens:
    articles are resolved into unique real-world developments.

Decision policy:
    AUTO MERGE:
      Qwen says same_event with high confidence AND
      ModernBERT/date evidence is sufficiently strong AND
      event-representation similarity is credible AND
      no competing event is nearly as plausible.

    REVIEW:
      meaningful positive signal, model disagreement, story-token evidence,
      competing candidates, or Qwen uncertainty.

    NEW EVENT:
      no convincing event match.

For ambiguous cases the article becomes a separate `pending_review` event.
This intentionally prefers false splits over false merges.

Existing Stage 7B.2 clusters are preserved as `legacy_provisional` and are
never used as production candidate events.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np
import requests
import torch
import torch.nn.functional as F
from huggingface_hub import HfApi
from sentence_transformers import SentenceTransformer
from supabase import Client, create_client
from transformers import AutoModelForSequenceClassification, AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]

REVIEW_PATH = ROOT / "review" / "events" / "assignments" / "latest.json"
PUBLIC_EVENTS_PATH = ROOT / "data" / "events" / "latest.json"

RESOLVER_VERSION = "7B.4-launch"
METHOD_NAME = "article_to_event_v1"
TRANSLATION_PROFILE = "validated_language_routing_v3"

EMBEDDING_MODEL = (
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
MODERNBERT_MODEL = "Juanillaberia/articles-pairs-event-detection"

QWEN_REPO = "Qwen/Qwen3-4B-GGUF"
QWEN_QUANT = "Q4_K_M"

LLAMA_SERVER_BIN = os.environ.get(
    "LLAMA_SERVER_BIN",
    "/tmp/llama.cpp/build/bin/llama-server",
)
SERVER_URL = "http://127.0.0.1:8080/v1/chat/completions"
HEALTH_URL = "http://127.0.0.1:8080/health"

MAX_EVENT_GAP_DAYS = 7.0
TOP_EVENT_CANDIDATES = 3
MAX_EVENT_EVIDENCE = 3

# Retrieval / verifier gating.
MIN_EVENT_REP_SIMILARITY = 0.55
QWEN_MIN_REP_SIMILARITY = 0.68
QWEN_MIN_MODERNBERT = 0.30
QWEN_STRONG_MODERNBERT = 0.60

# Auto-merge is intentionally strict.
AUTO_MERGE_QWEN_CONFIDENCE = 0.90
AUTO_MERGE_MODERNBERT = 0.55
AUTO_MERGE_REP_SIMILARITY = 0.70

# Google News Full Coverage was empirically too broad to be ground truth.
STORY_TOKEN_MODERNBERT_FLOOR = 0.35
STORY_TOKEN_QWEN_CONFIDENCE = 0.88

# Competing candidates this close trigger review instead of an auto-merge.
COMPETING_SIMILARITY_MARGIN = 0.035

DATE_DECAY_LAMBDA = 0.20


class ResolverError(RuntimeError):
    pass


@dataclass
class Article:
    article_id: str
    original_headline: str
    english_headline: str
    publisher: str
    published_at: str | None
    first_seen_at: str
    last_seen_at: str
    story_token: str | None
    source_language: str
    search_rank: int
    search_markets: list[str]
    snippet: str
    vector: np.ndarray | None = None

    @property
    def date(self) -> datetime:
        for value in (self.published_at, self.first_seen_at):
            if value:
                try:
                    return datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass
        return datetime.now(timezone.utc)


@dataclass
class EvidenceArticle:
    article_id: str
    original_headline: str
    english_headline: str
    publisher: str
    date: datetime
    story_token: str | None
    vector: np.ndarray


@dataclass
class EventState:
    event_id: str
    event_title: str
    event_date: str
    first_seen_at: str
    last_seen_at: str
    evidence: list[EvidenceArticle] = field(default_factory=list)
    representation: np.ndarray | None = None

    def rebuild_representation(self) -> None:
        if not self.evidence:
            self.representation = None
            return

        matrix = np.stack(
            [item.vector for item in self.evidence],
            axis=0,
        )
        vector = matrix.mean(axis=0)
        norm = float(np.linalg.norm(vector))
        if norm:
            vector = vector / norm
        self.representation = vector.astype(np.float32)

    @property
    def story_tokens(self) -> set[str]:
        return {
            item.story_token
            for item in self.evidence
            if item.story_token
        }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise ResolverError(f"{name} is missing.")
    return value


def first_row(response: Any, context: str) -> dict[str, Any]:
    data = getattr(response, "data", None)

    if isinstance(data, list) and data:
        return data[0]

    if isinstance(data, dict) and data:
        return data

    raise ResolverError(f"No Supabase row while {context}.")


def stable_event_key(article_id: str) -> str:
    digest = hashlib.sha256(
        article_id.encode("utf-8")
    ).hexdigest()[:24]
    return f"evt3_{digest}"


def parse_source_metadata(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def latest_collection(client: Client) -> dict[str, Any]:
    response = (
        client.table("collection_runs")
        .select("run_id,run_key,started_at,completed_at,status")
        .in_("status", ["success", "partial"])
        .order("started_at", desc=True)
        .limit(1)
        .execute()
    )

    return first_row(
        response,
        "reading latest collection run",
    )


def load_translations(
    client: Client,
    article_ids: list[str],
) -> dict[str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for start in range(0, len(article_ids), 150):
        batch = article_ids[start:start + 150]

        response = (
            client.table("article_translations")
            .select(
                "article_id,source_language_iso2,"
                "translated_headline,requires_review,"
                "review_reason,created_at"
            )
            .eq("translation_profile", TRANSLATION_PROFILE)
            .in_("article_id", batch)
            .order("created_at", desc=True)
            .execute()
        )

        rows.extend(getattr(response, "data", None) or [])

    newest: dict[str, dict[str, Any]] = {}

    for row in rows:
        aid = str(row["article_id"])
        if aid not in newest:
            newest[aid] = row

    return newest


def load_latest_articles(
    client: Client,
    run_id: str,
) -> list[Article]:
    observations_response = (
        client.table("article_observations")
        .select(
            "article_id,search_rank,search_country_iso3,"
            "search_language,observed_at"
        )
        .eq("run_id", run_id)
        .execute()
    )

    observations = getattr(
        observations_response,
        "data",
        None,
    ) or []

    if not observations:
        raise ResolverError(
            "Latest collection contains no article observations."
        )

    meta: dict[str, dict[str, Any]] = {}

    for row in observations:
        aid = str(row["article_id"])

        item = meta.setdefault(
            aid,
            {
                "rank": 9999,
                "markets": set(),
                "languages": set(),
            },
        )

        if row.get("search_rank") is not None:
            item["rank"] = min(
                item["rank"],
                int(row["search_rank"]),
            )

        if row.get("search_country_iso3"):
            item["markets"].add(
                str(row["search_country_iso3"])
            )

        if row.get("search_language"):
            item["languages"].add(
                str(row["search_language"]).lower()
            )

    article_ids = sorted(meta)
    rows: list[dict[str, Any]] = []

    for start in range(0, len(article_ids), 150):
        batch = article_ids[start:start + 150]

        response = (
            client.table("articles")
            .select(
                "article_id,headline,publisher,canonical_url,"
                "published_at,first_seen_at,last_seen_at,source_metadata"
            )
            .in_("article_id", batch)
            .execute()
        )

        rows.extend(getattr(response, "data", None) or [])

    translations = load_translations(
        client,
        article_ids,
    )

    result: list[Article] = []

    for row in rows:
        aid = str(row["article_id"])
        original = str(row.get("headline") or "").strip()

        if not original:
            continue

        translation = translations.get(aid) or {}

        english = str(
            translation.get("translated_headline")
            or original
        ).strip()

        source_language = str(
            translation.get("source_language_iso2")
            or "en"
        )

        metadata = parse_source_metadata(
            row.get("source_metadata")
        )

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

        result.append(
            Article(
                article_id=aid,
                original_headline=original,
                english_headline=english or original,
                publisher=str(
                    row.get("publisher") or "Unknown source"
                ).strip(),
                published_at=row.get("published_at"),
                first_seen_at=str(row.get("first_seen_at")),
                last_seen_at=str(row.get("last_seen_at")),
                story_token=(
                    str(metadata["story_token"]).strip()
                    if metadata.get("story_token")
                    else None
                ),
                source_language=source_language,
                search_rank=meta[aid]["rank"],
                search_markets=sorted(meta[aid]["markets"]),
                snippet=snippet,
            )
        )

    result.sort(
        key=lambda article: (
            article.date,
            article.search_rank,
            article.english_headline.casefold(),
        )
    )

    return result


def register_model(
    client: Client,
    *,
    name: str,
    revision: str,
    task: str,
    language_scope: str,
    notes: str,
) -> str:
    response = (
        client.table("model_versions")
        .upsert(
            {
                "provider": "huggingface",
                "model_name": name,
                "model_revision": revision,
                "task": task,
                "language_scope": language_scope,
                "notes": notes,
            },
            on_conflict="provider,model_name,model_revision,task",
        )
        .select("model_version_id")
        .execute()
    )

    return str(
        first_row(
            response,
            f"registering model {name}",
        )["model_version_id"]
    )


def start_resolution_run(
    client: Client,
    *,
    collection_run_id: str,
    embedding_model_version_id: str,
    pair_model_version_id: str,
    verifier_model_version_id: str,
) -> tuple[str, str]:
    started = utc_now()
    run_key = started.strftime("resolve_%Y%m%dT%H%M%SZ")

    response = (
        client.table("event_resolution_runs")
        .insert(
            {
                "collection_run_id": collection_run_id,
                "embedding_model_version_id": embedding_model_version_id,
                "pair_model_version_id": pair_model_version_id,
                "verifier_model_version_id": verifier_model_version_id,
                "run_key": run_key,
                "started_at": iso_z(started),
                "status": "running",
                "resolver_version": RESOLVER_VERSION,
                "notes": (
                    "Precision-first event resolution. "
                    "Ambiguous assignments are kept separate pending review."
                ),
            }
        )
        .select("resolution_run_id")
        .execute()
    )

    return (
        str(
            first_row(
                response,
                "starting event resolution run",
            )["resolution_run_id"]
        ),
        run_key,
    )


def finish_resolution_run(
    client: Client,
    *,
    resolution_run_id: str,
    status: str,
    article_count: int,
    counts: dict[str, int],
    active_event_count: int,
    pending_event_count: int,
) -> None:
    (
        client.table("event_resolution_runs")
        .update(
            {
                "completed_at": iso_z(utc_now()),
                "status": status,
                "article_count": article_count,
                "already_assigned_count": counts["existing_assignment"],
                "auto_merge_count": counts["auto_merge"],
                "new_event_count": counts["new_event"],
                "review_count": counts["review"],
                "verifier_call_count": counts["verifier_calls"],
                "active_event_count": active_event_count,
                "pending_event_count": pending_event_count,
            }
        )
        .eq("resolution_run_id", resolution_run_id)
        .execute()
    )


def start_llama_server() -> tuple[subprocess.Popen, Any]:
    log_path = Path("/tmp/event-resolution-qwen.log")
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
            "4096",
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
                print(
                    log_path.read_text(
                        encoding="utf-8"
                    )[-10000:],
                    file=sys.stderr,
                )
            except Exception:
                pass

            raise ResolverError(
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

    raise ResolverError(
        "Qwen llama.cpp server did not become healthy."
    )


def stop_llama_server(
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

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(
        r"\{.*\}",
        text,
        flags=re.S,
    )

    if not match:
        raise ResolverError(
            f"Qwen output contained no JSON: {text}"
        )

    return json.loads(match.group(0))


def qwen_verify_event(
    *,
    article: Article,
    event: EventState,
    evidence: list[EvidenceArticle],
    rep_similarity: float,
    modernbert_max: float,
    story_token_match: bool,
) -> dict[str, Any]:
    evidence_text = []

    for index, item in enumerate(evidence, start=1):
        evidence_text.append(
            f"""
Evidence {index}
Publisher: {item.publisher}
Original: {item.original_headline}
English: {item.english_headline}
Date: {item.date.date().isoformat()}
""".strip()
        )

    prompt = f"""
/no_think

You are assigning a NEW news article to an EXISTING real-world event.

Question:
Does the new article report the SAME SPECIFIC REAL-WORLD EVENT represented
by the existing evidence?

Allowed labels:
- same_event
- not_same_event
- unclear

same_event requires the same concrete occurrence, announcement, study result,
decision, launch, incident, meeting, speech, policy action, etc.

Same topic, technology, organization, law, country, trend, or debate is NOT
enough.

NEW ARTICLE
Publisher: {article.publisher}
Original headline: {article.original_headline}
English normalization: {article.english_headline}
Snippet: {article.snippet}
Date: {article.date.date().isoformat()}

EXISTING EVENT
Event title: {event.event_title}
Event date: {event.event_date}

{chr(10).join(evidence_text)}

Supporting retrieval signals are not ground truth:
Event representation similarity: {rep_similarity:.4f}
ModernBERT strongest pair score: {modernbert_max:.4f}
Google News story-token match: {str(story_token_match).lower()}

Return ONLY JSON:
{{
  "relationship": "same_event | not_same_event | unclear",
  "confidence": 0.00,
  "reason": ""
}}
""".strip()

    response = requests.post(
        SERVER_URL,
        json={
            "model": f"{QWEN_REPO}:{QWEN_QUANT}",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a conservative real-world news-event verifier. "
                        "False merges are more harmful than false splits."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 230,
            "stream": False,
        },
        timeout=180,
    )

    response.raise_for_status()

    result = extract_json(
        str(
            response.json()["choices"][0]["message"]["content"]
        )
    )

    relationship = str(
        result.get("relationship") or ""
    ).strip()

    if relationship not in {
        "same_event",
        "not_same_event",
        "unclear",
    }:
        raise ResolverError(
            f"Unexpected Qwen relationship: {relationship}"
        )

    try:
        confidence = float(result.get("confidence", 0.0))
    except Exception:
        confidence = 0.0

    return {
        "relationship": relationship,
        "confidence": max(0.0, min(1.0, confidence)),
        "reason": str(result.get("reason") or ""),
    }


def pair_score(
    tokenizer,
    model,
    article: Article,
    evidence: EvidenceArticle,
) -> tuple[float, float]:
    inputs = tokenizer(
        text=article.english_headline,
        text_pair=evidence.english_headline,
        return_tensors="pt",
        truncation=True,
        max_length=128,
    )

    with torch.inference_mode():
        logits = model(**inputs).logits
        raw = float(
            F.softmax(logits, dim=-1)[0][1].item()
        )

    gap = abs(
        (article.date - evidence.date).total_seconds()
    ) / 86400.0

    adjusted = raw * math.exp(
        -DATE_DECAY_LAMBDA * gap
    )

    return raw, adjusted


def modernbert_event_scores(
    tokenizer,
    model,
    article: Article,
    event: EventState,
) -> tuple[float, float, list[EvidenceArticle]]:
    if not event.evidence:
        return 0.0, 0.0, []

    ranked = sorted(
        event.evidence,
        key=lambda evidence: float(
            np.dot(
                article.vector,
                evidence.vector,
            )
        ),
        reverse=True,
    )[:MAX_EVENT_EVIDENCE]

    adjusted_scores = []

    for evidence in ranked:
        _, adjusted = pair_score(
            tokenizer,
            model,
            article,
            evidence,
        )
        adjusted_scores.append(adjusted)

    return (
        max(adjusted_scores) if adjusted_scores else 0.0,
        mean(adjusted_scores) if adjusted_scores else 0.0,
        ranked,
    )


def create_event(
    client: Client,
    *,
    resolution_run_id: str,
    article: Article,
    state: str,
    requires_review: bool,
    review_reason: str | None,
) -> str:
    response = (
        client.table("events")
        .upsert(
            {
                "canonical_event_key": stable_event_key(
                    article.article_id
                ),
                "event_title": article.english_headline,
                "event_summary": None,
                "event_date": article.date.date().isoformat(),
                "primary_country_iso3": None,
                "additional_country_iso3": [],
                "first_seen_at": article.first_seen_at,
                "last_seen_at": article.last_seen_at,
                "clustering_method": METHOD_NAME,
                "cluster_confidence": None,
                "requires_cluster_review": requires_review,
                "cluster_review_reason": review_reason,
                "resolution_run_id": resolution_run_id,
                "event_state": state,
                "updated_at": iso_z(utc_now()),
            },
            on_conflict="canonical_event_key",
        )
        .select("event_id")
        .execute()
    )

    return str(
        first_row(
            response,
            "creating production event",
        )["event_id"]
    )


def link_article_to_event(
    client: Client,
    *,
    event_id: str,
    article: Article,
    similarity: float | None,
    canonical: bool,
) -> None:
    (
        client.table("event_articles")
        .upsert(
            {
                "event_id": event_id,
                "article_id": article.article_id,
                "is_canonical_source": canonical,
                "similarity_score": (
                    round(float(similarity), 4)
                    if similarity is not None
                    else None
                ),
            },
            on_conflict="event_id,article_id",
        )
        .execute()
    )


def insert_decision(
    client: Client,
    payload: dict[str, Any],
) -> str:
    response = (
        client.table("event_assignment_decisions")
        .upsert(
            {
                **payload,
                "updated_at": iso_z(utc_now()),
            },
            on_conflict="resolution_run_id,article_id",
        )
        .select("assignment_decision_id")
        .execute()
    )

    return str(
        first_row(
            response,
            "recording event assignment decision",
        )["assignment_decision_id"]
    )


def update_event_after_merge(
    client: Client,
    *,
    event: EventState,
    article: Article,
    confidence: float,
) -> None:
    first_seen = min(
        event.first_seen_at,
        article.first_seen_at,
    )

    last_seen = max(
        event.last_seen_at,
        article.last_seen_at,
    )

    event.first_seen_at = first_seen
    event.last_seen_at = last_seen

    (
        client.table("events")
        .update(
            {
                "first_seen_at": first_seen,
                "last_seen_at": last_seen,
                "cluster_confidence": round(confidence, 4),
                "requires_cluster_review": False,
                "cluster_review_reason": None,
                "event_state": "active",
                "updated_at": iso_z(utc_now()),
            }
        )
        .eq("event_id", event.event_id)
        .execute()
    )


def existing_production_assignment(
    client: Client,
    article_id: str,
) -> str | None:
    response = (
        client.table("event_articles")
        .select("event_id,events!inner(event_state,clustering_method)")
        .eq("article_id", article_id)
        .eq("events.event_state", "active")
        .eq("events.clustering_method", METHOD_NAME)
        .limit(1)
        .execute()
    )

    data = getattr(response, "data", None) or []

    if not data:
        return None

    return str(data[0]["event_id"])


def load_active_events(
    client: Client,
    embedder: SentenceTransformer,
) -> dict[str, EventState]:
    events_response = (
        client.table("events")
        .select(
            "event_id,event_title,event_date,"
            "first_seen_at,last_seen_at"
        )
        .eq("event_state", "active")
        .eq("clustering_method", METHOD_NAME)
        .execute()
    )

    event_rows = getattr(
        events_response,
        "data",
        None,
    ) or []

    if not event_rows:
        return {}

    event_ids = [
        str(row["event_id"])
        for row in event_rows
    ]

    links = []

    for start in range(0, len(event_ids), 100):
        response = (
            client.table("event_articles")
            .select("event_id,article_id")
            .in_(
                "event_id",
                event_ids[start:start + 100],
            )
            .execute()
        )
        links.extend(getattr(response, "data", None) or [])

    article_ids = sorted(
        {
            str(row["article_id"])
            for row in links
        }
    )

    article_rows = []

    for start in range(0, len(article_ids), 150):
        response = (
            client.table("articles")
            .select(
                "article_id,headline,publisher,published_at,"
                "first_seen_at,source_metadata"
            )
            .in_(
                "article_id",
                article_ids[start:start + 150],
            )
            .execute()
        )
        article_rows.extend(getattr(response, "data", None) or [])

    translations = load_translations(
        client,
        article_ids,
    )

    article_map = {
        str(row["article_id"]): row
        for row in article_rows
    }

    text_by_article = {}

    for aid, row in article_map.items():
        translation = translations.get(aid) or {}
        text_by_article[aid] = str(
            translation.get("translated_headline")
            or row.get("headline")
            or ""
        ).strip()

    vector_ids = [
        aid
        for aid in article_ids
        if text_by_article.get(aid)
    ]

    vectors = embedder.encode(
        [text_by_article[aid] for aid in vector_ids],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )

    vector_map = {
        aid: np.asarray(vector, dtype=np.float32)
        for aid, vector in zip(vector_ids, vectors)
    }

    states = {
        str(row["event_id"]): EventState(
            event_id=str(row["event_id"]),
            event_title=str(row["event_title"]),
            event_date=str(row.get("event_date") or ""),
            first_seen_at=str(row["first_seen_at"]),
            last_seen_at=str(row["last_seen_at"]),
        )
        for row in event_rows
    }

    for link in links:
        eid = str(link["event_id"])
        aid = str(link["article_id"])

        row = article_map.get(aid)
        vector = vector_map.get(aid)

        if not row or vector is None:
            continue

        metadata = parse_source_metadata(
            row.get("source_metadata")
        )

        translation = translations.get(aid) or {}

        english = str(
            translation.get("translated_headline")
            or row.get("headline")
            or ""
        ).strip()

        date_value = (
            row.get("published_at")
            or row.get("first_seen_at")
        )

        try:
            date = datetime.fromisoformat(
                str(date_value).replace("Z", "+00:00")
            )
        except Exception:
            date = utc_now()

        states[eid].evidence.append(
            EvidenceArticle(
                article_id=aid,
                original_headline=str(row.get("headline") or ""),
                english_headline=english,
                publisher=str(row.get("publisher") or "Unknown source"),
                date=date,
                story_token=(
                    str(metadata["story_token"]).strip()
                    if metadata.get("story_token")
                    else None
                ),
                vector=vector,
            )
        )

    for event in states.values():
        event.rebuild_representation()

    return states


def candidate_events(
    article: Article,
    events: dict[str, EventState],
) -> list[tuple[float, EventState]]:
    scored = []

    for event in events.values():
        if event.representation is None:
            continue

        # Temporal screen based on event's latest evidence.
        try:
            event_last = datetime.fromisoformat(
                event.last_seen_at.replace("Z", "+00:00")
            )
        except Exception:
            event_last = article.date

        gap = abs(
            (article.date - event_last).total_seconds()
        ) / 86400.0

        token_match = bool(
            article.story_token
            and article.story_token in event.story_tokens
        )

        if gap > MAX_EVENT_GAP_DAYS and not token_match:
            continue

        similarity = float(
            np.dot(
                article.vector,
                event.representation,
            )
        )

        if (
            similarity >= MIN_EVENT_REP_SIMILARITY
            or token_match
        ):
            scored.append(
                (similarity, event)
            )

    scored.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return scored[:TOP_EVENT_CANDIDATES]


def event_evidence_json(
    event: EventState,
    evidence: list[EvidenceArticle],
) -> list[dict[str, Any]]:
    return [
        {
            "article_id": item.article_id,
            "headline_original": item.original_headline,
            "headline_english": item.english_headline,
            "publisher": item.publisher,
            "date": item.date.date().isoformat(),
        }
        for item in evidence
    ]


def main() -> int:
    client: Client = create_client(
        required_env("SUPABASE_URL"),
        required_env("SUPABASE_SECRET_KEY"),
    )

    collection = latest_collection(client)
    articles = load_latest_articles(
        client,
        str(collection["run_id"]),
    )

    if not articles:
        raise ResolverError(
            "No usable articles found."
        )

    # Exact model provenance.
    embedding_revision = (
        HfApi().model_info(EMBEDDING_MODEL).sha
        or "unknown"
    )
    modernbert_revision = (
        HfApi().model_info(MODERNBERT_MODEL).sha
        or "unknown"
    )
    qwen_revision = (
        HfApi().model_info(QWEN_REPO).sha
        or "unknown"
    )

    embedding_model_version_id = register_model(
        client,
        name=EMBEDDING_MODEL,
        revision=embedding_revision,
        task="event_candidate_retrieval",
        language_scope="multilingual",
        notes="Candidate retrieval for article-to-event assignment.",
    )

    pair_model_version_id = register_model(
        client,
        name=MODERNBERT_MODEL,
        revision=modernbert_revision,
        task="event_pair_verification",
        language_scope="english_normalized",
        notes=(
            "Task-specific news same-event classifier with "
            "post-hoc publication-date adjustment."
        ),
    )

    verifier_model_version_id = register_model(
        client,
        name=QWEN_REPO,
        revision=qwen_revision,
        task="event_cluster_verification",
        language_scope="original_plus_english",
        notes=(
            "Conservative article-to-event verifier. "
            "Original and normalized-English evidence supplied together."
        ),
    )

    resolution_run_id, run_key = start_resolution_run(
        client,
        collection_run_id=str(collection["run_id"]),
        embedding_model_version_id=embedding_model_version_id,
        pair_model_version_id=pair_model_version_id,
        verifier_model_version_id=verifier_model_version_id,
    )

    embedder = SentenceTransformer(
        EMBEDDING_MODEL
    )

    article_vectors = embedder.encode(
        [
            article.english_headline
            for article in articles
        ],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=True,
    )

    for article, vector in zip(
        articles,
        article_vectors,
    ):
        article.vector = np.asarray(
            vector,
            dtype=np.float32,
        )

    tokenizer = AutoTokenizer.from_pretrained(
        MODERNBERT_MODEL,
        revision=modernbert_revision,
    )

    modern_model = (
        AutoModelForSequenceClassification
        .from_pretrained(
            MODERNBERT_MODEL,
            revision=modernbert_revision,
        )
    )

    modern_model.eval()

    events = load_active_events(
        client,
        embedder,
    )

    counts = {
        "existing_assignment": 0,
        "auto_merge": 0,
        "new_event": 0,
        "review": 0,
        "verifier_calls": 0,
    }

    review_items = []
    decision_items = []

    process = None
    llama_handle = None

    try:
        # Start Qwen once. It is only called for plausible/ambiguous matches.
        process, llama_handle = start_llama_server()

        for number, article in enumerate(
            articles,
            start=1,
        ):
            print(
                f"[{number}/{len(articles)}] "
                f"{article.english_headline[:100]}"
            )

            already_event_id = existing_production_assignment(
                client,
                article.article_id,
            )

            if already_event_id:
                counts["existing_assignment"] += 1

                insert_decision(
                    client,
                    {
                        "resolution_run_id": resolution_run_id,
                        "article_id": article.article_id,
                        "decision": "existing_assignment",
                        "candidate_event_id": already_event_id,
                        "assigned_event_id": already_event_id,
                        "requires_review": False,
                        "evidence": {},
                    },
                )

                continue

            candidates = candidate_events(
                article,
                events,
            )

            if not candidates:
                event_id = create_event(
                    client,
                    resolution_run_id=resolution_run_id,
                    article=article,
                    state="active",
                    requires_review=False,
                    review_reason=None,
                )

                link_article_to_event(
                    client,
                    event_id=event_id,
                    article=article,
                    similarity=1.0,
                    canonical=True,
                )

                evidence = EvidenceArticle(
                    article_id=article.article_id,
                    original_headline=article.original_headline,
                    english_headline=article.english_headline,
                    publisher=article.publisher,
                    date=article.date,
                    story_token=article.story_token,
                    vector=article.vector,
                )

                state = EventState(
                    event_id=event_id,
                    event_title=article.english_headline,
                    event_date=article.date.date().isoformat(),
                    first_seen_at=article.first_seen_at,
                    last_seen_at=article.last_seen_at,
                    evidence=[evidence],
                )
                state.rebuild_representation()
                events[event_id] = state

                counts["new_event"] += 1

                decision_id = insert_decision(
                    client,
                    {
                        "resolution_run_id": resolution_run_id,
                        "article_id": article.article_id,
                        "decision": "new_event",
                        "assigned_event_id": event_id,
                        "event_similarity": None,
                        "requires_review": False,
                        "evidence": {
                            "reason": "no credible candidate event",
                        },
                    },
                )

                decision_items.append(
                    {
                        "assignment_decision_id": decision_id,
                        "article_id": article.article_id,
                        "decision": "new_event",
                        "assigned_event_id": event_id,
                        "article_headline": article.english_headline,
                    }
                )

                continue

            top_similarity, top_event = candidates[0]

            second_similarity = (
                candidates[1][0]
                if len(candidates) > 1
                else None
            )

            competing = bool(
                second_similarity is not None
                and (
                    top_similarity - second_similarity
                ) < COMPETING_SIMILARITY_MARGIN
            )

            modern_max, modern_mean, evidence = modernbert_event_scores(
                tokenizer,
                modern_model,
                article,
                top_event,
            )

            story_match = bool(
                article.story_token
                and article.story_token in top_event.story_tokens
            )

            # Only invoke the expensive verifier when there is meaningful
            # evidence of a potential match.
            should_call_qwen = bool(
                story_match
                or modern_max >= QWEN_STRONG_MODERNBERT
                or (
                    top_similarity >= QWEN_MIN_REP_SIMILARITY
                    and modern_max >= QWEN_MIN_MODERNBERT
                )
            )

            qwen = {
                "relationship": None,
                "confidence": None,
                "reason": "",
            }

            if should_call_qwen:
                counts["verifier_calls"] += 1

                qwen = qwen_verify_event(
                    article=article,
                    event=top_event,
                    evidence=evidence,
                    rep_similarity=top_similarity,
                    modernbert_max=modern_max,
                    story_token_match=story_match,
                )

            auto_merge = False

            if qwen["relationship"] == "same_event":
                if (
                    not competing
                    and top_similarity >= AUTO_MERGE_REP_SIMILARITY
                    and modern_max >= AUTO_MERGE_MODERNBERT
                    and float(qwen["confidence"]) >= AUTO_MERGE_QWEN_CONFIDENCE
                ):
                    auto_merge = True

                elif (
                    not competing
                    and story_match
                    and modern_max >= STORY_TOKEN_MODERNBERT_FLOOR
                    and float(qwen["confidence"]) >= STORY_TOKEN_QWEN_CONFIDENCE
                ):
                    auto_merge = True

            if auto_merge:
                link_article_to_event(
                    client,
                    event_id=top_event.event_id,
                    article=article,
                    similarity=top_similarity,
                    canonical=False,
                )

                update_event_after_merge(
                    client,
                    event=top_event,
                    article=article,
                    confidence=float(qwen["confidence"]),
                )

                top_event.evidence.append(
                    EvidenceArticle(
                        article_id=article.article_id,
                        original_headline=article.original_headline,
                        english_headline=article.english_headline,
                        publisher=article.publisher,
                        date=article.date,
                        story_token=article.story_token,
                        vector=article.vector,
                    )
                )
                top_event.rebuild_representation()

                counts["auto_merge"] += 1

                decision = "auto_merge"
                assigned_event_id = top_event.event_id
                requires_review = False
                review_reason = None

            else:
                review_reasons = []

                # REVIEW is reserved for genuinely plausible event matches.
                # Retrieval similarity and competing candidates alone are not
                # evidence that two AI-related articles report the same event.

                if qwen["relationship"] == "same_event":
                    review_reasons.append(
                        "Qwen identifies a possible same event, but auto-merge thresholds are not met"
                    )

                    if competing:
                        review_reasons.append(
                            "more than one event remains plausible"
                        )

                elif qwen["relationship"] == "unclear":
                    if (
                        story_match
                        or top_similarity >= 0.72
                        or modern_max >= 0.55
                    ):
                        review_reasons.append(
                            "Qwen is unclear despite meaningful candidate-event evidence"
                        )

                elif qwen["relationship"] == "not_same_event":
                    if (
                        story_match
                        and modern_max >= 0.60
                    ) or (
                        top_similarity >= 0.82
                        and modern_max >= 0.70
                    ):
                        review_reasons.append(
                            "exceptionally strong non-LLM evidence conflicts with Qwen not-same decision"
                        )

                # If Qwen was never called, retrieval similarity/competition
                # alone never creates human review. The article remains a
                # separate event.

                needs_review = bool(review_reasons)

                if needs_review:
                    # Precision-first: keep the article separate until reviewed.
                    event_id = create_event(
                        client,
                        resolution_run_id=resolution_run_id,
                        article=article,
                        state="active",
                        requires_review=True,
                        review_reason="; ".join(review_reasons),
                    )

                    link_article_to_event(
                        client,
                        event_id=event_id,
                        article=article,
                        similarity=1.0,
                        canonical=True,
                    )

                    # The ambiguous article remains a separate ACTIVE event.
                    # Human governance may merge it later, but weekly publication
                    # is never blocked by the review queue.
                    counts["review"] += 1

                    decision = "review"
                    assigned_event_id = event_id
                    requires_review = True
                    review_reason = "; ".join(review_reasons)

                else:
                    event_id = create_event(
                        client,
                        resolution_run_id=resolution_run_id,
                        article=article,
                        state="active",
                        requires_review=False,
                        review_reason=None,
                    )

                    link_article_to_event(
                        client,
                        event_id=event_id,
                        article=article,
                        similarity=1.0,
                        canonical=True,
                    )

                    evidence_article = EvidenceArticle(
                        article_id=article.article_id,
                        original_headline=article.original_headline,
                        english_headline=article.english_headline,
                        publisher=article.publisher,
                        date=article.date,
                        story_token=article.story_token,
                        vector=article.vector,
                    )

                    new_state = EventState(
                        event_id=event_id,
                        event_title=article.english_headline,
                        event_date=article.date.date().isoformat(),
                        first_seen_at=article.first_seen_at,
                        last_seen_at=article.last_seen_at,
                        evidence=[evidence_article],
                    )
                    new_state.rebuild_representation()
                    events[event_id] = new_state

                    counts["new_event"] += 1

                    decision = "new_event"
                    assigned_event_id = event_id
                    requires_review = False
                    review_reason = None

            decision_id = insert_decision(
                client,
                {
                    "resolution_run_id": resolution_run_id,
                    "article_id": article.article_id,
                    "decision": decision,
                    "candidate_event_id": top_event.event_id,
                    "assigned_event_id": assigned_event_id,
                    "event_similarity": round(top_similarity, 4),
                    "second_event_similarity": (
                        round(second_similarity, 4)
                        if second_similarity is not None
                        else None
                    ),
                    "modernbert_max_probability": round(modern_max, 4),
                    "modernbert_mean_probability": round(modern_mean, 4),
                    "qwen_relationship": qwen["relationship"],
                    "qwen_confidence": (
                        round(float(qwen["confidence"]), 4)
                        if qwen["confidence"] is not None
                        else None
                    ),
                    "qwen_reason": qwen["reason"],
                    "temporal_gap_days": round(
                        min(
                            abs(
                                (article.date - ev.date).total_seconds()
                            ) / 86400.0
                            for ev in evidence
                        )
                        if evidence
                        else 0.0,
                        3,
                    ),
                    "story_token_match": story_match,
                    "competing_candidate": competing,
                    "requires_review": requires_review,
                    "review_reason": review_reason,
                    "evidence": {
                        "candidate_event_title": top_event.event_title,
                        "candidate_event_date": top_event.event_date,
                        "candidate_evidence": event_evidence_json(
                            top_event,
                            evidence,
                        ),
                    },
                },
            )

            decision_record = {
                "assignment_decision_id": decision_id,
                "article_id": article.article_id,
                "decision": decision,
                "candidate_event_id": top_event.event_id,
                "assigned_event_id": assigned_event_id,
                "article": {
                    "headline_original": article.original_headline,
                    "headline_english": article.english_headline,
                    "publisher": article.publisher,
                    "source_language": article.source_language,
                    "snippet": article.snippet,
                    "date": article.date.date().isoformat(),
                },
                "candidate_event": {
                    "event_id": top_event.event_id,
                    "event_title": top_event.event_title,
                    "event_date": top_event.event_date,
                    "evidence": event_evidence_json(
                        top_event,
                        evidence,
                    ),
                },
                "signals": {
                    "event_similarity": round(top_similarity, 4),
                    "second_event_similarity": (
                        round(second_similarity, 4)
                        if second_similarity is not None
                        else None
                    ),
                    "modernbert_max": round(modern_max, 4),
                    "modernbert_mean": round(modern_mean, 4),
                    "qwen_relationship": qwen["relationship"],
                    "qwen_confidence": (
                        round(float(qwen["confidence"]), 4)
                        if qwen["confidence"] is not None
                        else None
                    ),
                    "qwen_reason": qwen["reason"],
                    "story_token_match": story_match,
                    "competing_candidate": competing,
                },
                "requires_review": requires_review,
                "review_reason": review_reason or "",
            }

            decision_items.append(decision_record)

            if requires_review:
                review_items.append(decision_record)

        # Fetch canonical production event counts after all decisions.
        active_response = (
            client.table("events")
            .select("event_id", count="exact")
            .eq("event_state", "active")
            .eq("clustering_method", METHOD_NAME)
            .execute()
        )

        pending_response = (
            client.table("events")
            .select("event_id", count="exact")
            .eq("event_state", "pending_review")
            .eq("clustering_method", METHOD_NAME)
            .execute()
        )

        active_event_count = int(
            getattr(active_response, "count", None)
            or len(getattr(active_response, "data", None) or [])
        )

        pending_event_count = int(
            getattr(pending_response, "count", None)
            or len(getattr(pending_response, "data", None) or [])
        )

        finish_resolution_run(
            client,
            resolution_run_id=resolution_run_id,
            status="success",
            article_count=len(articles),
            counts=counts,
            active_event_count=active_event_count,
            pending_event_count=pending_event_count,
        )

        review_payload = {
            "meta": {
                "stage": "7B.3",
                "status": "production event assignment pilot",
                "collection_run_key": collection["run_key"],
                "resolution_run_id": resolution_run_id,
                "resolution_run_key": run_key,
                "article_count": len(articles),
                "already_assigned_count": counts["existing_assignment"],
                "auto_merge_count": counts["auto_merge"],
                "new_event_count": counts["new_event"],
                "review_count": counts["review"],
                "verifier_call_count": counts["verifier_calls"],
                "active_event_count": active_event_count,
                "pending_event_count": pending_event_count,
                "method": METHOD_NAME,
                "principle": (
                    "Precision first: ambiguous articles remain separate "
                    "pending human review rather than being falsely merged."
                ),
            },
            "review_queue": review_items,
            "decisions": decision_items,
        }

        REVIEW_PATH.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        REVIEW_PATH.write_text(
            json.dumps(
                review_payload,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        # Public Event Lens data: active events only.
        event_rows_response = (
            client.table("events")
            .select(
                "event_id,event_title,event_summary,event_date,"
                "first_seen_at,last_seen_at,cluster_confidence"
            )
            .eq("event_state", "active")
            .eq("clustering_method", METHOD_NAME)
            .order("event_date", desc=True)
            .execute()
        )

        active_rows = getattr(
            event_rows_response,
            "data",
            None,
        ) or []

        public_events = []

        for row in active_rows:
            eid = str(row["event_id"])

            links_response = (
                client.table("event_articles")
                .select(
                    "article_id,is_canonical_source,"
                    "similarity_score,articles("
                    "headline,publisher,canonical_url,published_at)"
                )
                .eq("event_id", eid)
                .execute()
            )

            links = getattr(
                links_response,
                "data",
                None,
            ) or []

            public_events.append(
                {
                    **row,
                    "article_count": len(links),
                    "sources": [
                        {
                            "article_id": link["article_id"],
                            "canonical": link["is_canonical_source"],
                            "similarity_score": link["similarity_score"],
                            "headline": (
                                (link.get("articles") or {}).get("headline")
                            ),
                            "publisher": (
                                (link.get("articles") or {}).get("publisher")
                            ),
                            "url": (
                                (link.get("articles") or {}).get("canonical_url")
                            ),
                            "published_at": (
                                (link.get("articles") or {}).get("published_at")
                            ),
                        }
                        for link in links
                    ],
                }
            )

        PUBLIC_EVENTS_PATH.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        PUBLIC_EVENTS_PATH.write_text(
            json.dumps(
                {
                    "meta": {
                        "stage": "7B.3",
                        "resolution_run_key": run_key,
                        "article_count": len(articles),
                        "active_event_count": active_event_count,
                        "pending_review_count": pending_event_count,
                        "coverage_to_event_ratio": round(
                            len(articles) / active_event_count,
                            3,
                        )
                        if active_event_count
                        else None,
                        "warning": (
                            "Only active resolved events are included. "
                            "Pending-review events are excluded from the Event Lens."
                        ),
                    },
                    "events": public_events,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        print()
        print("Resolution complete")
        print(json.dumps(counts, indent=2))
        print(f"Active events: {active_event_count}")
        print(f"Pending review: {pending_event_count}")
        print(f"Review file: {REVIEW_PATH}")
        print(f"Public Event Lens file: {PUBLIC_EVENTS_PATH}")

        return 0

    except Exception:
        finish_resolution_run(
            client,
            resolution_run_id=resolution_run_id,
            status="failed",
            article_count=len(articles),
            counts=counts,
            active_event_count=0,
            pending_event_count=0,
        )
        raise

    finally:
        stop_llama_server(
            process,
            llama_handle,
        )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ResolverError as exc:
        print(
            f"Event resolution failed: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
