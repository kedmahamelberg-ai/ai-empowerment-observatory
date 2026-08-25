#!/usr/bin/env python3
"""Reconcile newly resolved events against the full AIEO event registry.

This stage runs after the precision-first resolver and before Stage 7C.
It adds long-term memory without making automatic merging permissive:

* a new article about an old concrete event adds coverage, not a new event;
* a genuine next step stays a separate event but joins a story family;
* same-topic reporting is never merged merely because actors or themes match;
* uncertain long-gap matches remain separate and enter the review ledger;
* every article/event appearance is recorded for replication-delay analysis.

Only very strong, non-competing ``same_event`` decisions are applied
automatically. Applied changes are appended to ``event_revisions``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable
from urllib.parse import urlparse

import numpy as np
import requests
import torch
import torch.nn.functional as F
from huggingface_hub import HfApi
from sentence_transformers import SentenceTransformer
from supabase import Client, create_client
from transformers import AutoModelForSequenceClassification, AutoTokenizer

# Reuse the exact models and llama.cpp server used by the production resolver.
from resolve_events import (  # type: ignore
    EMBEDDING_MODEL,
    METHOD_NAME,
    MODERNBERT_MODEL,
    QWEN_QUANT,
    QWEN_REPO,
    SERVER_URL,
    extract_json,
    iso_z,
    parse_source_metadata,
    start_llama_server,
    stop_llama_server,
    utc_now,
)

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_OUTPUT = ROOT / "data" / "releases" / "reconciliation" / "latest.json"
PRIVATE_REVIEW = ROOT / "review" / "events" / "longitudinal" / "latest.json"

RECONCILER_VERSION = "longitudinal_v1.3"
MANUAL_DECISIONS_PATH = ROOT / "review" / "events" / "longitudinal" / "manual-decisions.json"
RECENT_TRACK_DAYS = float(os.environ.get("AIEO_RECENT_TRACK_DAYS", "21"))
RESURFACE_DAYS = float(os.environ.get("AIEO_RESURFACE_DAYS", "28"))
TOP_CANDIDATES = int(os.environ.get("AIEO_LONGITUDINAL_TOP_CANDIDATES", "5"))
MAX_EVIDENCE = int(os.environ.get("AIEO_LONGITUDINAL_MAX_EVIDENCE", "4"))

RECENT_MIN_SIMILARITY = float(os.environ.get("AIEO_RECENT_MIN_SIMILARITY", "0.60"))
HISTORICAL_MIN_SIMILARITY = float(os.environ.get("AIEO_HISTORICAL_MIN_SIMILARITY", "0.70"))
QWEN_CALL_SIMILARITY = float(os.environ.get("AIEO_LONGITUDINAL_QWEN_SIMILARITY", "0.67"))
QWEN_CALL_PAIR = float(os.environ.get("AIEO_LONGITUDINAL_QWEN_PAIR", "0.42"))
AUTO_MERGE_QWEN = float(os.environ.get("AIEO_LONGITUDINAL_QWEN_CONF", "0.95"))
AUTO_MERGE_SIMILARITY = float(os.environ.get("AIEO_LONGITUDINAL_MERGE_SIM", "0.72"))
AUTO_MERGE_PAIR = float(os.environ.get("AIEO_LONGITUDINAL_MERGE_PAIR", "0.55"))
TOKEN_MERGE_PAIR = float(os.environ.get("AIEO_LONGITUDINAL_TOKEN_PAIR", "0.45"))
FOLLOW_ON_QWEN = float(os.environ.get("AIEO_FOLLOW_ON_QWEN_CONF", "0.86"))
# Follow-on links remain human-gated during the pilot. Model output can propose
# a relationship, but cannot create a story family unless this is explicitly
# enabled or an accepted manual decision exists.
AUTO_LINK_MODEL_FOLLOW_ON = str(
    os.environ.get("AIEO_AUTO_LINK_MODEL_FOLLOW_ON", "false")
).strip().casefold() in {"1", "true", "yes", "on"}

# During the pilot, even strict model-proposed same-event merges remain
# proposals unless a versioned human decision accepts the pair. This prevents
# semantically similar commentary, opinion or generic AI headlines from being
# collapsed into one real-world event merely because they share a date/topic.
AUTO_APPLY_MODEL_SAME_EVENT = str(
    os.environ.get("AIEO_AUTO_APPLY_MODEL_SAME_EVENT", "false")
).strip().casefold() in {"1", "true", "yes", "on"}
COMPETING_MARGIN = float(os.environ.get("AIEO_LONGITUDINAL_COMPETING_MARGIN", "0.03"))


class ReconciliationError(RuntimeError):
    """Longitudinal reconciliation could not complete safely."""


@dataclass
class ArticleEvidence:
    article_id: str
    original_headline: str
    english_headline: str
    publisher: str
    canonical_url: str | None
    published_at: str | None
    first_seen_at: str
    story_token: str | None
    vector: np.ndarray

    @property
    def date(self) -> datetime:
        return parse_datetime(self.published_at) or parse_datetime(self.first_seen_at) or utc_now()


@dataclass
class EventMemory:
    event_id: str
    event_title: str
    event_date: str | None
    first_seen_at: str
    last_seen_at: str
    resolution_run_id: str | None
    story_family_id: str | None
    canonical_event_id: str | None
    requires_review: bool
    evidence: list[ArticleEvidence] = field(default_factory=list)
    representation: np.ndarray | None = None

    def rebuild(self) -> None:
        if not self.evidence:
            self.representation = None
            return
        matrix = np.stack([item.vector for item in self.evidence], axis=0)
        vector = matrix.mean(axis=0)
        norm = float(np.linalg.norm(vector))
        self.representation = (vector / norm if norm else vector).astype(np.float32)

    @property
    def first_evidence_at(self) -> datetime:
        return min((item.date for item in self.evidence), default=parse_datetime(self.first_seen_at) or utc_now())

    @property
    def latest_evidence_at(self) -> datetime:
        return max((item.date for item in self.evidence), default=parse_datetime(self.last_seen_at) or utc_now())

    @property
    def story_tokens(self) -> set[str]:
        return {item.story_token for item in self.evidence if item.story_token}


@dataclass(frozen=True)
class Candidate:
    event: EventMemory
    similarity: float
    gap_days: float
    track: str
    token_match: bool


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise ReconciliationError(f"{name} is missing.")
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


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def chunks(values: list[str], size: int = 150) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def first_row(response: Any, context: str) -> dict[str, Any]:
    data = getattr(response, "data", None)
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict) and data:
        return data
    raise ReconciliationError(f"No Supabase row while {context}.")


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def decision_pair_key(left_event_id: str, right_event_id: str) -> tuple[str, str]:
    return tuple(sorted((str(left_event_id), str(right_event_id))))


def load_manual_decisions(path: Path = MANUAL_DECISIONS_PATH) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    if not path.exists():
        return {}, {"schema_version": None, "decision_count": 0, "path": str(path.relative_to(ROOT))}
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("decisions") or []
    if not isinstance(rows, list):
        raise ReconciliationError(f"Invalid manual decision list: {path}")
    allowed = {"same_event", "follow_on_development", "same_topic_only", "keep_separate"}
    decisions: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or str(row.get("status") or "").strip() != "accepted":
            continue
        left = str(row.get("event_a_id") or "").strip()
        right = str(row.get("event_b_id") or "").strip()
        relationship = str(row.get("relationship") or "").strip()
        if not left or not right or left == right or relationship not in allowed:
            raise ReconciliationError(f"Invalid accepted manual decision: {row}")
        key = decision_pair_key(left, right)
        if key in decisions:
            raise ReconciliationError(f"Duplicate manual decision for {key}")
        canonical = str(row.get("canonical_event_id") or "").strip() or None
        if relationship == "same_event" and canonical not in {left, right}:
            raise ReconciliationError(
                f"same_event decision for {key} must name one pair member as canonical_event_id"
            )
        decisions[key] = {**row, "canonical_event_id": canonical}
    return decisions, {
        "schema_version": payload.get("schema_version"),
        "decision_count": len(decisions),
        "reviewed_at": payload.get("reviewed_at"),
        "review_basis": payload.get("review_basis"),
        "path": str(path.relative_to(ROOT)),
    }


def event_descriptor(event: EventMemory) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "title": event.event_title,
        "event_date": event.event_date,
        "first_seen_at": event.first_seen_at,
        "last_seen_at": event.last_seen_at,
        "article_count": len(event.evidence),
        "sources": [
            {
                "article_id": item.article_id,
                "publisher": item.publisher,
                "headline": item.english_headline,
                "published_at": item.published_at,
                "url": item.canonical_url,
            }
            for item in event.evidence[:MAX_EVIDENCE]
        ],
    }


def choose_merge_direction(
    left: EventMemory,
    right: EventMemory,
    preferred_canonical_event_id: str | None = None,
) -> tuple[EventMemory, EventMemory]:
    if preferred_canonical_event_id:
        if preferred_canonical_event_id == left.event_id:
            return right, left
        if preferred_canonical_event_id == right.event_id:
            return left, right
        raise ReconciliationError(
            f"Preferred canonical event {preferred_canonical_event_id} is not in the merge pair"
        )
    # The underlying occurrence is anchored to the earliest published evidence.
    # Observation time is the secondary criterion, then event_id for stability.
    canonical = min(
        (left, right),
        key=lambda event: (
            event.first_evidence_at,
            parse_datetime(event.first_seen_at) or event.first_evidence_at,
            event.event_id,
        ),
    )
    alias = right if canonical.event_id == left.event_id else left
    return alias, canonical


def latest_resolution(client: Client) -> dict[str, Any]:
    response = (
        client.table("event_resolution_runs")
        .select(
            "resolution_run_id,collection_run_id,run_key,started_at,completed_at,"
            "status,resolver_version"
        )
        .eq("status", "success")
        .order("completed_at", desc=True)
        .limit(1)
        .execute()
    )
    return first_row(response, "reading latest successful event resolution")


def collection_row(client: Client, run_id: str) -> dict[str, Any]:
    response = (
        client.table("collection_runs")
        .select("run_id,run_key,started_at,completed_at,status")
        .eq("run_id", run_id)
        .limit(1)
        .execute()
    )
    return first_row(response, "reading collection run")


def choose_mode(requested: str, now: datetime) -> str:
    if requested != "auto":
        return requested
    if now.day <= 7 and now.month == 1:
        return "annual"
    if now.day <= 7 and now.month in {4, 7, 10}:
        return "quarterly"
    if now.day <= 7:
        return "monthly"
    return "weekly"


def lookback_start(mode: str, now: datetime) -> datetime | None:
    days = {"weekly": 45, "monthly": 120, "quarterly": 400, "annual": None, "manual": None}[mode]
    return now - timedelta(days=days) if days else None


def registry_pool_start(client: Client) -> datetime | None:
    response = (
        client.table("events")
        .select("first_seen_at")
        .eq("event_state", "active")
        .eq("clustering_method", METHOD_NAME)
        .is_("canonical_event_id", "null")
        .order("first_seen_at", desc=False)
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return parse_datetime(rows[0].get("first_seen_at")) if rows else None


def start_run(
    client: Client,
    *,
    mode: str,
    collection_run_id: str,
    resolution_run_id: str,
    pool_start: datetime | None,
    considered_through: datetime,
    dry_run: bool,
    candidate_lookback_start: datetime | None,
) -> tuple[str, str]:
    started = utc_now()
    run_key = started.strftime("reconcile_%Y%m%dT%H%M%SZ")
    response = (
        client.table("event_reconciliation_runs")
        .insert(
            {
                "run_key": run_key,
                "mode": mode,
                "collection_run_id": collection_run_id,
                "resolution_run_id": resolution_run_id,
                "pool_start_at": iso_z(pool_start) if pool_start else None,
                "pool_considered_through": iso_z(considered_through),
                "dry_run": dry_run,
                "started_at": iso_z(started),
                "status": "running",
                "notes": "All-time event memory with precision-first automatic merge thresholds.",
                "metadata": {
                    "reconciler_version": RECONCILER_VERSION,
                    "candidate_lookback_start": (
                        iso_z(candidate_lookback_start) if candidate_lookback_start else None
                    ),
                },
            }
        )
        .select("reconciliation_run_id")
        .execute()
    )
    return str(first_row(response, "starting reconciliation")["reconciliation_run_id"]), run_key


def finish_run(
    client: Client,
    run_id: str,
    *,
    status: str,
    counts: dict[str, int],
    snapshot_id: str | None,
    metadata: dict[str, Any],
) -> None:
    (
        client.table("event_reconciliation_runs")
        .update(
            {
                "completed_at": iso_z(utc_now()),
                "status": status,
                "candidate_count": counts.get("candidates", 0),
                "auto_merge_count": counts.get("auto_merges", 0),
                "follow_on_count": counts.get("follow_on", 0),
                "review_count": counts.get("review", 0),
                "occurrence_count": counts.get("occurrences", 0),
                "registry_snapshot_id": snapshot_id,
                "metadata": metadata,
            }
        )
        .eq("reconciliation_run_id", run_id)
        .execute()
    )


def load_event_rows(client: Client) -> list[dict[str, Any]]:
    response = (
        client.table("events")
        .select(
            "event_id,event_title,event_date,first_seen_at,last_seen_at,"
            "resolution_run_id,story_family_id,canonical_event_id,"
            "requires_cluster_review,updated_at,event_state,clustering_method"
        )
        .eq("event_state", "active")
        .eq("clustering_method", METHOD_NAME)
        .execute()
    )
    return getattr(response, "data", None) or []


def load_translations(client: Client, article_ids: list[str]) -> dict[str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for batch in chunks(article_ids):
        response = (
            client.table("article_translations")
            .select("article_id,translated_headline,source_language_iso2,created_at")
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


def load_memories(
    client: Client,
    embedder: SentenceTransformer,
    event_rows: list[dict[str, Any]],
) -> dict[str, EventMemory]:
    # Alias events are not candidates. Their historical articles have already
    # been moved to the canonical event.
    canonical_rows = [row for row in event_rows if not row.get("canonical_event_id")]
    event_ids = [str(row["event_id"]) for row in canonical_rows]
    links: list[dict[str, Any]] = []
    for batch in chunks(event_ids, 100):
        response = (
            client.table("event_articles")
            .select("event_id,article_id")
            .in_("event_id", batch)
            .execute()
        )
        links.extend(getattr(response, "data", None) or [])
    article_ids = sorted({str(row["article_id"]) for row in links})
    articles: list[dict[str, Any]] = []
    for batch in chunks(article_ids):
        response = (
            client.table("articles")
            .select(
                "article_id,headline,publisher,canonical_url,published_at,"
                "first_seen_at,source_metadata"
            )
            .in_("article_id", batch)
            .execute()
        )
        articles.extend(getattr(response, "data", None) or [])
    article_map = {str(row["article_id"]): row for row in articles}
    translations = load_translations(client, article_ids)

    texts: dict[str, str] = {}
    for article_id, row in article_map.items():
        translation = translations.get(article_id) or {}
        texts[article_id] = clean_text(translation.get("translated_headline") or row.get("headline"))
    vector_ids = [article_id for article_id in article_ids if texts.get(article_id)]
    vectors = embedder.encode(
        [texts[article_id] for article_id in vector_ids],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    ) if vector_ids else []
    vector_map = {
        article_id: np.asarray(vector, dtype=np.float32)
        for article_id, vector in zip(vector_ids, vectors)
    }

    links_by_event: dict[str, list[str]] = defaultdict(list)
    for link in links:
        links_by_event[str(link["event_id"])].append(str(link["article_id"]))

    memories: dict[str, EventMemory] = {}
    for row in canonical_rows:
        event_id = str(row["event_id"])
        memory = EventMemory(
            event_id=event_id,
            event_title=clean_text(row.get("event_title")),
            event_date=str(row.get("event_date") or "") or None,
            first_seen_at=str(row.get("first_seen_at") or ""),
            last_seen_at=str(row.get("last_seen_at") or ""),
            resolution_run_id=str(row.get("resolution_run_id") or "") or None,
            story_family_id=str(row.get("story_family_id") or "") or None,
            canonical_event_id=None,
            requires_review=bool(row.get("requires_cluster_review")),
        )
        for article_id in links_by_event.get(event_id, []):
            article = article_map.get(article_id)
            vector = vector_map.get(article_id)
            if article is None or vector is None:
                continue
            metadata = parse_source_metadata(article.get("source_metadata"))
            memory.evidence.append(
                ArticleEvidence(
                    article_id=article_id,
                    original_headline=clean_text(article.get("headline")),
                    english_headline=texts[article_id],
                    publisher=clean_text(article.get("publisher")) or "Unknown source",
                    canonical_url=str(article.get("canonical_url") or "") or None,
                    published_at=str(article.get("published_at") or "") or None,
                    first_seen_at=str(article.get("first_seen_at") or ""),
                    story_token=clean_text(metadata.get("story_token")) or None,
                    vector=vector,
                )
            )
        memory.rebuild()
        if memory.representation is not None:
            memories[event_id] = memory
    return memories


def raw_pair_score(tokenizer, model, left: str, right: str) -> float:
    inputs = tokenizer(text=left, text_pair=right, return_tensors="pt", truncation=True, max_length=160)
    with torch.inference_mode():
        logits = model(**inputs).logits
        return float(F.softmax(logits, dim=-1)[0][1].item())


def pair_scores(
    tokenizer,
    model,
    candidate: EventMemory,
    target: EventMemory,
) -> tuple[float, float, list[ArticleEvidence]]:
    candidate_text = " | ".join(item.english_headline for item in candidate.evidence[:MAX_EVIDENCE])
    ranked = sorted(
        target.evidence,
        key=lambda item: float(np.dot(candidate.representation, item.vector)),
        reverse=True,
    )[:MAX_EVIDENCE]
    scores = [raw_pair_score(tokenizer, model, candidate_text, item.english_headline) for item in ranked]
    return (
        max(scores) if scores else 0.0,
        sum(scores) / len(scores) if scores else 0.0,
        ranked,
    )


def retrieve(candidate: EventMemory, pool: dict[str, EventMemory]) -> list[Candidate]:
    result: list[Candidate] = []
    for target in pool.values():
        if target.event_id == candidate.event_id or target.representation is None:
            continue
        if target.first_evidence_at > candidate.first_evidence_at:
            continue
        gap = abs((candidate.first_evidence_at - target.latest_evidence_at).total_seconds()) / 86400.0
        track = "recent" if gap <= RECENT_TRACK_DAYS else "historical"
        token_match = bool(candidate.story_tokens.intersection(target.story_tokens))
        similarity = float(np.dot(candidate.representation, target.representation))
        minimum = RECENT_MIN_SIMILARITY if track == "recent" else HISTORICAL_MIN_SIMILARITY
        if token_match or similarity >= minimum:
            result.append(Candidate(target, similarity, gap, track, token_match))
    result.sort(key=lambda item: (item.token_match, item.similarity), reverse=True)
    return result[:TOP_CANDIDATES]


def verify_relationship(
    *,
    candidate: EventMemory,
    target: EventMemory,
    target_evidence: list[ArticleEvidence],
    similarity: float,
    pair_max: float,
    pair_mean: float,
    token_match: bool,
    gap_days: float,
) -> dict[str, Any]:
    new_evidence = "\n".join(
        f"- {item.publisher}: {item.english_headline} ({item.date.date().isoformat()})"
        for item in candidate.evidence[:MAX_EVIDENCE]
    )
    old_evidence = "\n".join(
        f"- {item.publisher}: {item.english_headline} ({item.date.date().isoformat()})"
        for item in target_evidence
    )
    prompt = f"""
/no_think

Compare a newly resolved news event with an earlier event in a longitudinal
registry. Time distance must not make repeated reporting look new, but a shared
actor or topic is not enough to merge events.

Choose exactly one:
- same_event: the same concrete occurrence, announcement, decision, launch,
  incident, study result, meeting or policy action is being reported again. A
  later article that adds detail, reaction, criticism, confirmation, a video
  treatment, or a different headline is still the same event unless a new actor
  performs a new dated action.
- follow_on_development: a genuinely new, separately datable occurrence that
  implements, changes, reverses, legally responds to, or materially advances the
  earlier event. It requires a new action or outcome, not merely another article.
- same_topic_only: related subject or actor, but not the same occurrence and not
  a concrete follow-on development. Commentary, explainers, television episodes,
  and consecutive parts of an editorial series belong here unless they report a
  new real-world action.
- unclear: the available evidence does not support a safe decision.

Decision checks:
1. Ask whether both headlines could truthfully be citations for one underlying
   occurrence. If yes, choose same_event.
2. Do not use publication date, semantic similarity, or shared actors as proof of
   a follow-on. Identify the new actor action/outcome explicitly.
3. "More detail", "backlash", "criticism", "confirmation", or a new publisher
   does not by itself create a follow-on development.

NEW EVENT
Title: {candidate.event_title}
Event date: {candidate.event_date}
{new_evidence}

EARLIER EVENT
Title: {target.event_title}
Event date: {target.event_date}
{old_evidence}

Diagnostic signals only:
- days between coverage episodes: {gap_days:.1f}
- representation similarity: {similarity:.4f}
- strongest pair score: {pair_max:.4f}
- mean pair score: {pair_mean:.4f}
- Google News story-token overlap: {str(token_match).lower()}

Return ONLY JSON:
{{
  "relationship": "same_event | follow_on_development | same_topic_only | unclear",
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
                        "You are a conservative longitudinal news-event verifier. "
                        "False merges are more harmful than false splits."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 280,
            "stream": False,
        },
        timeout=180,
    )
    response.raise_for_status()
    payload = extract_json(str(response.json()["choices"][0]["message"]["content"]))
    relationship = clean_text(payload.get("relationship"))
    allowed = {"same_event", "follow_on_development", "same_topic_only", "unclear"}
    if relationship not in allowed:
        raise ReconciliationError(f"Unexpected relationship: {relationship}")
    try:
        confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return {"relationship": relationship, "confidence": confidence, "reason": clean_text(payload.get("reason"))}


def upsert_relationship(
    client: Client,
    *,
    from_event_id: str,
    to_event_id: str,
    relationship_type: str,
    confidence: float | None,
    status: str,
    resolution_run_id: str | None,
    reconciliation_run_id: str,
    story_family_id: str | None,
    evidence: dict[str, Any],
) -> None:
    (
        client.table("event_relationships")
        .upsert(
            {
                "from_event_id": from_event_id,
                "to_event_id": to_event_id,
                "story_family_id": story_family_id,
                "relationship_type": relationship_type,
                "confidence": round(confidence, 4) if confidence is not None else None,
                "source": "longitudinal_reconciler",
                "status": status,
                "resolution_run_id": resolution_run_id,
                "reconciliation_run_id": reconciliation_run_id,
                "evidence": evidence,
                "reviewed_at": iso_z(utc_now()) if status == "accepted" else None,
            },
            on_conflict="from_event_id,to_event_id,relationship_type,source",
        )
        .execute()
    )


def record_event_revision(
    client: Client,
    *,
    reconciliation_run_id: str,
    event_id: str,
    prior_effective_event_id: str,
    new_effective_event_id: str,
    revision_type: str,
    reason: str,
    evidence: dict[str, Any],
) -> None:
    (
        client.table("event_revisions")
        .insert(
            {
                "reconciliation_run_id": reconciliation_run_id,
                "event_id": event_id,
                "prior_effective_event_id": prior_effective_event_id,
                "new_effective_event_id": new_effective_event_id,
                "revision_type": revision_type,
                "reason": reason,
                "evidence": evidence,
                "applied_by": "longitudinal_reconciler",
            }
        )
        .execute()
    )


def ensure_story_family(client: Client, anchor: EventMemory) -> str:
    if anchor.story_family_id:
        return anchor.story_family_id
    key = "story_" + hashlib.sha256(anchor.event_id.encode("utf-8")).hexdigest()[:24]
    response = (
        client.table("story_families")
        .upsert(
            {
                "canonical_story_key": key,
                "story_title": anchor.event_title or "Continuing AI story",
                "first_event_date": anchor.event_date or anchor.first_evidence_at.date().isoformat(),
                "first_seen_at": iso_z(parse_datetime(anchor.first_seen_at) or anchor.first_evidence_at),
                "last_seen_at": iso_z(anchor.latest_evidence_at),
                "status": "active",
                "metadata": {"anchor_event_id": anchor.event_id},
                "updated_at": iso_z(utc_now()),
            },
            on_conflict="canonical_story_key",
        )
        .select("story_family_id")
        .execute()
    )
    story_id = str(first_row(response, "creating story family")["story_family_id"])
    (
        client.table("events")
        .update({"story_family_id": story_id, "last_reconciled_at": iso_z(utc_now())})
        .eq("event_id", anchor.event_id)
        .execute()
    )
    anchor.story_family_id = story_id
    return story_id


def merge_event(
    client: Client,
    *,
    candidate: EventMemory,
    target: EventMemory,
    reconciliation_run_id: str,
    confidence: float,
    evidence: dict[str, Any],
) -> list[str]:
    response = (
        client.table("event_articles")
        .select("article_id")
        .eq("event_id", candidate.event_id)
        .execute()
    )
    article_ids = [str(row["article_id"]) for row in (getattr(response, "data", None) or [])]
    for article_id in article_ids:
        (
            client.table("event_articles")
            .upsert(
                {
                    "event_id": target.event_id,
                    "article_id": article_id,
                    "is_canonical_source": False,
                    "similarity_score": round(float(evidence.get("similarity") or confidence), 4),
                },
                on_conflict="event_id,article_id",
            )
            .execute()
        )
    if article_ids:
        (
            client.table("event_articles")
            .delete()
            .eq("event_id", candidate.event_id)
            .in_("article_id", article_ids)
            .execute()
        )
    now = iso_z(utc_now())
    (
        client.table("events")
        .update(
            {
                "canonical_event_id": target.event_id,
                "canonicalized_at": now,
                "canonicalization_reason": evidence.get("reason") or "strict longitudinal same-event match",
                "last_reconciled_at": now,
                "registry_version": "event_registry_v2_longitudinal",
                "story_family_id": target.story_family_id,
                "requires_cluster_review": False,
                "cluster_review_reason": None,
            }
        )
        .eq("event_id", candidate.event_id)
        .execute()
    )
    latest = max(target.latest_evidence_at, candidate.latest_evidence_at)
    first_seen_values = [
        value
        for value in (parse_datetime(target.first_seen_at), parse_datetime(candidate.first_seen_at))
        if value is not None
    ]
    event_date_values = [
        value
        for value in (parse_datetime(target.event_date), parse_datetime(candidate.event_date))
        if value is not None
    ]
    target_update = {
        "last_seen_at": iso_z(latest),
        "last_reconciled_at": now,
        "registry_version": "event_registry_v2_longitudinal",
    }
    if first_seen_values:
        target_update["first_seen_at"] = iso_z(min(first_seen_values))
    if event_date_values:
        target_update["event_date"] = min(event_date_values).date().isoformat()
    (
        client.table("events")
        .update(target_update)
        .eq("event_id", target.event_id)
        .execute()
    )
    (
        client.table("event_occurrences")
        .update({
            "effective_event_id": target.event_id,
            "story_family_id": target.story_family_id,
            "updated_at": now,
            "metadata": {
                "canonicalized_from_event_id": candidate.event_id,
                "canonicalization_reason": evidence.get("reason") or "strict longitudinal same-event match",
            },
        })
        .eq("effective_event_id", candidate.event_id)
        .execute()
    )
    upsert_relationship(
        client,
        from_event_id=candidate.event_id,
        to_event_id=target.event_id,
        relationship_type="same_event_alias",
        confidence=confidence,
        status="accepted",
        resolution_run_id=candidate.resolution_run_id,
        reconciliation_run_id=reconciliation_run_id,
        story_family_id=target.story_family_id,
        evidence=evidence,
    )
    record_event_revision(
        client,
        reconciliation_run_id=reconciliation_run_id,
        event_id=candidate.event_id,
        prior_effective_event_id=candidate.event_id,
        new_effective_event_id=target.event_id,
        revision_type="merge",
        reason=str(evidence.get("reason") or "Strict longitudinal same-event match"),
        evidence=evidence,
    )
    return article_ids


def link_follow_on(
    client: Client,
    *,
    candidate: EventMemory,
    target: EventMemory,
    reconciliation_run_id: str,
    confidence: float,
    evidence: dict[str, Any],
) -> str:
    story_id = ensure_story_family(client, target)
    (
        client.table("events")
        .update(
            {
                "story_family_id": story_id,
                "last_reconciled_at": iso_z(utc_now()),
                "registry_version": "event_registry_v2_longitudinal",
            }
        )
        .eq("event_id", candidate.event_id)
        .execute()
    )
    (
        client.table("story_families")
        .update({
            "last_seen_at": iso_z(max(target.latest_evidence_at, candidate.latest_evidence_at)),
            "updated_at": iso_z(utc_now()),
        })
        .eq("story_family_id", story_id)
        .execute()
    )
    upsert_relationship(
        client,
        from_event_id=candidate.event_id,
        to_event_id=target.event_id,
        relationship_type="follow_on_development",
        confidence=confidence,
        status="accepted",
        resolution_run_id=candidate.resolution_run_id,
        reconciliation_run_id=reconciliation_run_id,
        story_family_id=story_id,
        evidence=evidence,
    )
    record_event_revision(
        client,
        reconciliation_run_id=reconciliation_run_id,
        event_id=candidate.event_id,
        prior_effective_event_id=candidate.event_id,
        new_effective_event_id=candidate.event_id,
        revision_type="story_link",
        reason=str(evidence.get("reason") or "Follow-on development in continuing story"),
        evidence={**evidence, "story_family_id": story_id},
    )
    return story_id


def canonical_map(event_rows: list[dict[str, Any]]) -> dict[str, str]:
    direct = {
        str(row["event_id"]): str(row["canonical_event_id"])
        for row in event_rows
        if row.get("canonical_event_id")
    }
    result: dict[str, str] = {}
    for event_id in {str(row["event_id"]) for row in event_rows}:
        current = event_id
        seen: set[str] = set()
        while current in direct:
            if current in seen:
                raise ReconciliationError(f"Canonical-event cycle detected at {current}")
            seen.add(current)
            current = direct[current]
        result[event_id] = current
    return result


def domain(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.") or None
    except ValueError:
        return None


def collection_articles(
    client: Client,
    collection_run_id: str,
) -> tuple[list[dict[str, Any]], dict[str, set[str]], datetime]:
    response = (
        client.table("article_observations")
        .select("article_id,search_country_iso3,observed_at")
        .eq("run_id", collection_run_id)
        .execute()
    )
    observations = getattr(response, "data", None) or []
    article_ids = sorted({str(row["article_id"]) for row in observations})
    markets: dict[str, set[str]] = defaultdict(set)
    observed_values: list[datetime] = []
    for row in observations:
        article_id = str(row["article_id"])
        if row.get("search_country_iso3"):
            markets[article_id].add(str(row["search_country_iso3"]))
        parsed = parse_datetime(row.get("observed_at"))
        if parsed:
            observed_values.append(parsed)
    rows: list[dict[str, Any]] = []
    for batch in chunks(article_ids):
        response = (
            client.table("articles")
            .select(
                "article_id,headline,publisher,canonical_url,published_at,"
                "first_seen_at,last_seen_at"
            )
            .in_("article_id", batch)
            .execute()
        )
        rows.extend(getattr(response, "data", None) or [])
    return rows, markets, max(observed_values) if observed_values else utc_now()


def prior_occurrences(client: Client, effective_event_id: str) -> list[dict[str, Any]]:
    response = (
        client.table("event_occurrences")
        .select("article_id,observed_at,publisher,search_markets,appearance_type")
        .eq("effective_event_id", effective_event_id)
        .order("observed_at", desc=False)
        .execute()
    )
    return getattr(response, "data", None) or []


def fallback_history(
    client: Client,
    effective_event_id: str,
    current_article_ids: set[str],
) -> tuple[datetime | None, set[str]]:
    response = (
        client.table("event_articles")
        .select("article_id,articles(publisher,published_at,first_seen_at)")
        .eq("event_id", effective_event_id)
        .execute()
    )
    dates: list[datetime] = []
    publishers: set[str] = set()
    for row in getattr(response, "data", None) or []:
        if str(row.get("article_id")) in current_article_ids:
            continue
        article = row.get("articles") or {}
        parsed = parse_datetime(article.get("published_at")) or parse_datetime(article.get("first_seen_at"))
        if parsed:
            dates.append(parsed)
        if article.get("publisher"):
            publishers.add(clean_text(article.get("publisher")))
    return (max(dates) if dates else None, publishers)


def latest_resolution_decisions(
    client: Client,
    resolution_run_id: str,
    article_ids: list[str],
) -> dict[str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for batch in chunks(article_ids, 100):
        response = (
            client.table("event_assignment_decisions")
            .select("article_id,decision,assigned_event_id,candidate_event_id")
            .eq("resolution_run_id", resolution_run_id)
            .in_("article_id", batch)
            .execute()
        )
        rows.extend(getattr(response, "data", None) or [])
    return {str(row["article_id"]): row for row in rows}


def record_occurrences(
    client: Client,
    *,
    collection: dict[str, Any],
    latest_resolution_run_id: str,
    merged_articles: dict[str, dict[str, Any]],
    follow_on_events: dict[str, dict[str, Any]],
    possible_matches: dict[str, dict[str, Any]],
) -> int:
    articles, market_map, observed_at = collection_articles(client, str(collection["run_id"]))
    article_ids = {str(row["article_id"]) for row in articles}
    links: list[dict[str, Any]] = []
    for batch in chunks(sorted(article_ids)):
        response = (
            client.table("event_articles")
            .select("article_id,event_id")
            .in_("article_id", batch)
            .execute()
        )
        links.extend(getattr(response, "data", None) or [])
    link_map = {str(row["article_id"]): str(row["event_id"]) for row in links}
    decisions = latest_resolution_decisions(client, latest_resolution_run_id, sorted(article_ids))

    event_rows = load_event_rows(client)
    cmap = canonical_map(event_rows)
    event_map = {str(row["event_id"]): row for row in event_rows}
    current_event_ids = {
        str(row["event_id"])
        for row in event_rows
        if str(row.get("resolution_run_id") or "") == latest_resolution_run_id
    }

    count = 0
    for article in articles:
        article_id = str(article["article_id"])
        raw_event_id = link_map.get(article_id)
        if not raw_event_id:
            continue
        effective_event_id = cmap.get(raw_event_id, raw_event_id)
        event = event_map.get(effective_event_id) or event_map.get(raw_event_id) or {}
        prior = prior_occurrences(client, effective_event_id)
        prior_for_article = [row for row in prior if str(row.get("article_id")) == article_id]
        prior_dates = [parse_datetime(row.get("observed_at")) for row in prior]
        prior_dates = [value for value in prior_dates if value]
        previous = max(prior_dates) if prior_dates else None
        prior_publishers = {clean_text(row.get("publisher")) for row in prior if row.get("publisher")}
        prior_markets = {
            market
            for row in prior
            for market in (row.get("search_markets") or [])
        }
        if not prior:
            fallback_date, fallback_publishers = fallback_history(client, effective_event_id, article_ids)
            previous = fallback_date
            prior_publishers.update(fallback_publishers)

        resolver_decision = str((decisions.get(article_id) or {}).get("decision") or "")
        article_first_seen = parse_datetime(article.get("first_seen_at"))
        collection_started = parse_datetime(collection.get("started_at"))
        seen_before_collection = bool(
            article_first_seen
            and collection_started
            and article_first_seen < collection_started - timedelta(seconds=1)
        )
        if article_id in merged_articles:
            appearance = "same_event_new_coverage"
            track = str(merged_articles[article_id].get("track") or "historical")
            confidence = merged_articles[article_id].get("confidence")
            metadata = merged_articles[article_id]
        elif raw_event_id in follow_on_events:
            appearance = "follow_on_development"
            track = str(follow_on_events[raw_event_id].get("track") or "historical")
            confidence = follow_on_events[raw_event_id].get("confidence")
            metadata = follow_on_events[raw_event_id]
        elif raw_event_id in possible_matches:
            appearance = "possible_historical_match"
            track = str(possible_matches[raw_event_id].get("track") or "historical")
            confidence = possible_matches[raw_event_id].get("confidence")
            metadata = possible_matches[raw_event_id]
        elif prior_for_article or (resolver_decision == "existing_assignment" and seen_before_collection):
            appearance = "same_article_rediscovered"
            track = "exact"
            confidence = 1.0
            metadata = {"reason": "same persistent article observed in a later collection"}
        elif resolver_decision in {"auto_merge", "existing_assignment"} or raw_event_id not in current_event_ids:
            appearance = "same_event_new_coverage"
            track = "recent" if resolver_decision == "auto_merge" else "exact"
            confidence = None
            metadata = {"reason": f"resolver decision: {resolver_decision or 'historical event assignment'}"}
        else:
            appearance = "first_event_coverage"
            track = "recent"
            confidence = None
            metadata = {"reason": "no accepted prior-event match"}

        event_first = parse_datetime(event.get("first_seen_at"))
        days_since_first = (
            (observed_at - event_first).total_seconds() / 86400.0 if event_first else None
        )
        days_since_previous = (
            (observed_at - previous).total_seconds() / 86400.0
            if previous and observed_at >= previous else None
        )
        publisher = clean_text(article.get("publisher")) or None
        markets = sorted(market_map.get(article_id, set()))
        story_id = str(event.get("story_family_id") or "") or None
        (
            client.table("event_occurrences")
            .upsert(
                {
                    "event_id": raw_event_id,
                    "effective_event_id": effective_event_id,
                    "story_family_id": story_id,
                    "article_id": article_id,
                    "collection_run_id": collection["run_id"],
                    "appearance_type": appearance,
                    "article_published_at": article.get("published_at"),
                    "observed_at": iso_z(observed_at),
                    "previous_event_coverage_at": iso_z(previous) if previous else None,
                    "days_since_event_first_seen": round(days_since_first, 3) if days_since_first is not None else None,
                    "days_since_previous_coverage": round(days_since_previous, 3) if days_since_previous is not None else None,
                    "publisher": publisher,
                    "source_domain": domain(str(article.get("canonical_url") or "") or None),
                    "search_markets": markets,
                    "first_source_appearance": bool(publisher and publisher not in prior_publishers),
                    "first_market_appearances": sorted(set(markets) - prior_markets),
                    "resolution_track": track,
                    "relationship_confidence": round(float(confidence), 4) if confidence is not None else None,
                    "resolver_version": RECONCILER_VERSION,
                    "metadata": metadata,
                    "updated_at": iso_z(utc_now()),
                },
                on_conflict="collection_run_id,article_id",
            )
            .execute()
        )
        count += 1
    return count


def registry_snapshot(client: Client, considered_through: datetime) -> dict[str, Any]:
    rows = load_event_rows(client)
    canonical = [row for row in rows if not row.get("canonical_event_id")]
    first_dates = [parse_datetime(row.get("first_seen_at")) for row in canonical]
    first_dates = [value for value in first_dates if value]
    digest_payload = [
        {
            "event_id": str(row["event_id"]),
            "first_seen_at": row.get("first_seen_at"),
            "last_seen_at": row.get("last_seen_at"),
            "story_family_id": row.get("story_family_id"),
            "updated_at": row.get("updated_at"),
        }
        for row in sorted(canonical, key=lambda row: str(row["event_id"]))
    ]
    return {
        "registry_snapshot_id": "registry_" + stable_hash(digest_payload)[:20],
        "starts_at": iso_z(min(first_dates)) if first_dates else None,
        "considered_through": iso_z(considered_through),
        "canonical_event_count": len(canonical),
        "alias_event_count": len(rows) - len(canonical),
        "story_family_count": len({str(row.get("story_family_id")) for row in canonical if row.get("story_family_id")}),
        "all_prior_active_events_considered": True,
        "reconciler_version": RECONCILER_VERSION,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["auto", "weekly", "monthly", "quarterly", "annual", "manual"],
        default="auto",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--manual-decisions",
        default=str(MANUAL_DECISIONS_PATH),
        help="Versioned accepted human decisions. Missing file is allowed.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client: Client = create_client(
        required_env("SUPABASE_URL"),
        str(os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip(),
    )
    if not str(os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip():
        raise ReconciliationError("SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY is missing.")

    now = utc_now()
    mode = choose_mode(args.mode, now)
    manual_decisions, manual_decision_meta = load_manual_decisions(Path(args.manual_decisions))
    candidate_lookback_start = lookback_start(mode, now)
    pool_start = registry_pool_start(client)
    resolution = latest_resolution(client)
    collection = collection_row(client, str(resolution["collection_run_id"]))
    considered_through = parse_datetime(resolution.get("completed_at")) or now
    run_id, run_key = start_run(
        client,
        mode=mode,
        collection_run_id=str(collection["run_id"]),
        resolution_run_id=str(resolution["resolution_run_id"]),
        pool_start=pool_start,
        considered_through=considered_through,
        dry_run=args.dry_run,
        candidate_lookback_start=candidate_lookback_start,
    )

    counts = {
        "candidates": 0,
        "auto_merges": 0,
        "human_merges": 0,
        "model_merges": 0,
        "same_event_review": 0,
        "follow_on": 0,
        "follow_on_review": 0,
        "review": 0,
        "same_topic": 0,
        "manual_decisions": 0,
        "occurrences": 0,
        "verifier_calls": 0,
    }
    decisions: list[dict[str, Any]] = []
    merged_articles: dict[str, dict[str, Any]] = {}
    follow_on_events: dict[str, dict[str, Any]] = {}
    possible_matches: dict[str, dict[str, Any]] = {}
    process = None
    handle = None
    snapshot_id: str | None = None

    try:
        embedding_revision = HfApi().model_info(EMBEDDING_MODEL).sha or "unknown"
        pair_revision = HfApi().model_info(MODERNBERT_MODEL).sha or "unknown"
        verifier_revision = HfApi().model_info(QWEN_REPO).sha or "unknown"
        embedder = SentenceTransformer(EMBEDDING_MODEL, revision=embedding_revision)
        tokenizer = AutoTokenizer.from_pretrained(MODERNBERT_MODEL, revision=pair_revision)
        pair_model = AutoModelForSequenceClassification.from_pretrained(
            MODERNBERT_MODEL, revision=pair_revision
        )
        pair_model.eval()
        process, handle = start_llama_server()

        event_rows = load_event_rows(client)
        memories = load_memories(client, embedder, event_rows)
        latest_resolution_id = str(resolution["resolution_run_id"])
        if mode == "weekly":
            # The newest resolver output is always checked. In addition, recheck
            # unresolved recent candidates so a week-1 false split can be
            # corrected at T+7 as the evidence pool grows.
            candidate_ids = {
                event_id
                for event_id, event in memories.items()
                if event.resolution_run_id == latest_resolution_id
                or (
                    event.requires_review
                    and (
                        candidate_lookback_start is None
                        or event.first_evidence_at >= candidate_lookback_start
                    )
                )
            }
        else:
            candidate_ids = {
                event_id for event_id, event in memories.items()
                if candidate_lookback_start is None or event.first_evidence_at >= candidate_lookback_start
            }
        candidates = sorted(
            (memories[event_id] for event_id in candidate_ids),
            key=lambda event: (event.first_evidence_at, event.event_id),
        )

        for candidate in candidates:
            # A prior candidate may have been canonicalized earlier in this run.
            if candidate.event_id not in memories:
                continue
            pool = {
                event_id: event
                for event_id, event in memories.items()
                if event_id != candidate.event_id
                and event.first_evidence_at <= candidate.first_evidence_at
            }
            retrieved = retrieve(candidate, pool)
            if not retrieved:
                continue
            counts["candidates"] += 1
            top = retrieved[0]
            manual_override = None
            for retrieved_candidate in retrieved:
                possible_override = manual_decisions.get(
                    decision_pair_key(candidate.event_id, retrieved_candidate.event.event_id)
                )
                if possible_override is not None:
                    top = retrieved_candidate
                    manual_override = possible_override
                    counts["manual_decisions"] += 1
                    break
            remaining = [item for item in retrieved if item.event.event_id != top.event.event_id]
            second = remaining[0] if remaining else None
            competing = bool(
                second
                and not top.token_match
                and (top.similarity - second.similarity) < COMPETING_MARGIN
            )
            pair_max, pair_mean, evidence_articles = pair_scores(
                tokenizer, pair_model, candidate, top.event
            )
            should_verify = bool(
                top.token_match
                or top.similarity >= QWEN_CALL_SIMILARITY
                or pair_max >= QWEN_CALL_PAIR
            )
            result = {
                "relationship": "unclear",
                "confidence": 0.0,
                "reason": "verification gate not reached",
            }
            decision_source = "model"
            if manual_override is not None:
                decision_source = "human_override"
                result = {
                    "relationship": str(manual_override["relationship"]),
                    "confidence": float(manual_override.get("confidence", 1.0)),
                    "reason": clean_text(manual_override.get("reason")),
                }
            elif should_verify:
                counts["verifier_calls"] += 1
                result = verify_relationship(
                    candidate=candidate,
                    target=top.event,
                    target_evidence=evidence_articles,
                    similarity=top.similarity,
                    pair_max=pair_max,
                    pair_mean=pair_mean,
                    token_match=top.token_match,
                    gap_days=top.gap_days,
                )

            evidence = {
                "candidate_event_id": candidate.event_id,
                "target_event_id": top.event.event_id,
                "track": top.track,
                "gap_days": round(top.gap_days, 3),
                "similarity": round(top.similarity, 4),
                "second_similarity": round(second.similarity, 4) if second else None,
                "pair_max": round(pair_max, 4),
                "pair_mean": round(pair_mean, 4),
                "story_token_match": top.token_match,
                "competing_candidate": competing,
                "qwen_relationship": result["relationship"],
                "qwen_confidence": round(float(result["confidence"]), 4),
                "reason": result["reason"],
                "decision_source": decision_source,
                "manual_decision_id": (
                    manual_override.get("decision_id") if manual_override is not None else None
                ),
                "candidate_event": event_descriptor(candidate),
                "target_event": event_descriptor(top.event),
                "reconciler_version": RECONCILER_VERSION,
                "model_revisions": {
                    "embedding": embedding_revision,
                    "pair": pair_revision,
                    "verifier": verifier_revision,
                },
            }
            manual_same = bool(
                manual_override is not None
                and result["relationship"] == "same_event"
            )
            model_same_passes_strict_gate = bool(
                result["relationship"] == "same_event"
                and float(result["confidence"]) >= AUTO_MERGE_QWEN
                and not competing
                and top.similarity >= AUTO_MERGE_SIMILARITY
                and (
                    pair_max >= AUTO_MERGE_PAIR
                    or (top.token_match and pair_max >= TOKEN_MERGE_PAIR)
                )
            )
            strict_same = bool(
                manual_same
                or (AUTO_APPLY_MODEL_SAME_EVENT and model_same_passes_strict_gate)
            )
            manual_follow_on = bool(
                manual_override is not None
                and result["relationship"] == "follow_on_development"
            )
            strict_follow_on = bool(
                manual_follow_on
                or (
                    AUTO_LINK_MODEL_FOLLOW_ON
                    and result["relationship"] == "follow_on_development"
                    and float(result["confidence"]) >= FOLLOW_ON_QWEN
                    and not competing
                )
            )

            action = "kept_separate"
            if strict_same:
                action = (
                    "human_merge_same_event" if manual_same else "auto_merge_same_event"
                )
                merge_from, merge_into = choose_merge_direction(
                    candidate,
                    top.event,
                    (manual_override or {}).get("canonical_event_id"),
                )
                evidence["merge_from_event_id"] = merge_from.event_id
                evidence["merge_into_event_id"] = merge_into.event_id
                if not args.dry_run:
                    moved = merge_event(
                        client,
                        candidate=merge_from,
                        target=merge_into,
                        reconciliation_run_id=run_id,
                        confidence=float(result["confidence"]),
                        evidence=evidence,
                    )
                    # Update the in-memory registry immediately so later
                    # candidates in this reconciliation see one canonical event.
                    merge_into.evidence.extend(merge_from.evidence)
                    first_seen_values = [
                        value
                        for value in (
                            parse_datetime(merge_into.first_seen_at),
                            parse_datetime(merge_from.first_seen_at),
                        )
                        if value is not None
                    ]
                    if first_seen_values:
                        merge_into.first_seen_at = iso_z(min(first_seen_values))
                    event_date_values = [
                        value
                        for value in (
                            parse_datetime(merge_into.event_date),
                            parse_datetime(merge_from.event_date),
                        )
                        if value is not None
                    ]
                    if event_date_values:
                        merge_into.event_date = min(event_date_values).date().isoformat()
                    merge_into.last_seen_at = iso_z(
                        max(merge_into.latest_evidence_at, merge_from.latest_evidence_at)
                    )
                    merge_into.rebuild()
                    memories.pop(merge_from.event_id, None)
                    for article_id in moved:
                        merged_articles[article_id] = {
                            "candidate_event_id": merge_from.event_id,
                            "effective_event_id": merge_into.event_id,
                            "track": top.track,
                            "confidence": float(result["confidence"]),
                            "gap_days": top.gap_days,
                            "reason": result["reason"],
                        }
                counts["auto_merges"] += 1
                if manual_same:
                    counts["human_merges"] += 1
                else:
                    counts["model_merges"] += 1
            elif result["relationship"] == "same_event":
                # Model-only same-event decisions are proposals during the pilot.
                # A manual decision can accept them in a later audited run.
                action = "review_same_event"
                counts["same_event_review"] += 1
                counts["review"] += 1
                possible_matches[candidate.event_id] = {
                    "candidate_event_id": top.event.event_id,
                    "track": top.track,
                    "confidence": float(result["confidence"]),
                    "gap_days": top.gap_days,
                    "reason": result["reason"] or "model-proposed same-event match requires human acceptance",
                    "proposed_relationship": "same_event",
                    "passed_strict_model_gate": model_same_passes_strict_gate,
                }
                if not args.dry_run:
                    upsert_relationship(
                        client,
                        from_event_id=candidate.event_id,
                        to_event_id=top.event.event_id,
                        relationship_type="possible_same_event",
                        confidence=float(result["confidence"]),
                        status="proposed",
                        resolution_run_id=candidate.resolution_run_id,
                        reconciliation_run_id=run_id,
                        story_family_id=None,
                        evidence={
                            **evidence,
                            "passed_strict_model_gate": model_same_passes_strict_gate,
                        },
                    )
                    (
                        client.table("events")
                        .update(
                            {
                                "requires_cluster_review": True,
                                "cluster_review_reason": "model-proposed longitudinal same-event match",
                                "last_reconciled_at": iso_z(utc_now()),
                            }
                        )
                        .eq("event_id", candidate.event_id)
                        .execute()
                    )
            elif strict_follow_on:
                action = "linked_follow_on_development"
                if not args.dry_run:
                    story_id = link_follow_on(
                        client,
                        candidate=candidate,
                        target=top.event,
                        reconciliation_run_id=run_id,
                        confidence=float(result["confidence"]),
                        evidence=evidence,
                    )
                    follow_on_events[candidate.event_id] = {
                        "anchor_event_id": top.event.event_id,
                        "story_family_id": story_id,
                        "track": top.track,
                        "confidence": float(result["confidence"]),
                        "gap_days": top.gap_days,
                        "reason": result["reason"],
                    }
                counts["follow_on"] += 1
            elif result["relationship"] == "follow_on_development":
                action = "review_follow_on_development"
                counts["follow_on_review"] += 1
                counts["review"] += 1
                possible_matches[candidate.event_id] = {
                    "candidate_event_id": top.event.event_id,
                    "track": top.track,
                    "confidence": float(result["confidence"]),
                    "gap_days": top.gap_days,
                    "reason": result["reason"] or "possible follow-on requires human acceptance",
                    "proposed_relationship": "follow_on_development",
                }
                if not args.dry_run:
                    upsert_relationship(
                        client,
                        from_event_id=candidate.event_id,
                        to_event_id=top.event.event_id,
                        relationship_type="follow_on_development",
                        confidence=float(result["confidence"]),
                        status="proposed",
                        resolution_run_id=candidate.resolution_run_id,
                        reconciliation_run_id=run_id,
                        story_family_id=None,
                        evidence=evidence,
                    )
                    (
                        client.table("events")
                        .update(
                            {
                                "requires_cluster_review": True,
                                "cluster_review_reason": "possible longitudinal follow-on development",
                                "last_reconciled_at": iso_z(utc_now()),
                            }
                        )
                        .eq("event_id", candidate.event_id)
                        .execute()
                    )
            elif result["relationship"] == "keep_separate":
                action = "human_keep_separate"
            elif result["relationship"] == "same_topic_only":
                action = (
                    "human_same_topic_only"
                    if manual_override is not None
                    else "same_topic_only"
                )
                counts["same_topic"] += 1
                if not args.dry_run and float(result["confidence"]) >= 0.90:
                    upsert_relationship(
                        client,
                        from_event_id=candidate.event_id,
                        to_event_id=top.event.event_id,
                        relationship_type="same_topic_only",
                        confidence=float(result["confidence"]),
                        status="accepted",
                        resolution_run_id=candidate.resolution_run_id,
                        reconciliation_run_id=run_id,
                        story_family_id=None,
                        evidence=evidence,
                    )
            elif should_verify or top.token_match:
                action = "review_possible_historical_match"
                counts["review"] += 1
                possible_matches[candidate.event_id] = {
                    "candidate_event_id": top.event.event_id,
                    "track": top.track,
                    "confidence": float(result["confidence"]),
                    "gap_days": top.gap_days,
                    "reason": result["reason"] or "longitudinal match remains uncertain",
                }
                if not args.dry_run:
                    upsert_relationship(
                        client,
                        from_event_id=candidate.event_id,
                        to_event_id=top.event.event_id,
                        relationship_type="possible_same_event",
                        confidence=float(result["confidence"]),
                        status="proposed",
                        resolution_run_id=candidate.resolution_run_id,
                        reconciliation_run_id=run_id,
                        story_family_id=None,
                        evidence=evidence,
                    )
                    (
                        client.table("events")
                        .update(
                            {
                                "requires_cluster_review": True,
                                "cluster_review_reason": "possible longitudinal same-event match",
                                "last_reconciled_at": iso_z(utc_now()),
                            }
                        )
                        .eq("event_id", candidate.event_id)
                        .execute()
                    )
            decisions.append({**evidence, "action": action})

        if not args.dry_run:
            counts["occurrences"] = record_occurrences(
                client,
                collection=collection,
                latest_resolution_run_id=latest_resolution_id,
                merged_articles=merged_articles,
                follow_on_events=follow_on_events,
                possible_matches=possible_matches,
            )

        snapshot = registry_snapshot(client, considered_through)
        snapshot_id = str(snapshot["registry_snapshot_id"])
        merge_lags = [
            float(item["gap_days"])
            for item in decisions
            if item.get("action") in {"auto_merge_same_event", "human_merge_same_event"}
        ]
        public_payload = {
            "schema_version": "aieo_reconciliation_v1.0",
            "generated_at": iso_z(utc_now()),
            "run_key": run_key,
            "mode": mode,
            "status": "dry_run" if args.dry_run else "success",
            "historical_pool": snapshot,
            "summary": {
                "candidate_events_checked": counts["candidates"],
                "same_event_merges": counts["auto_merges"],
                "same_event_merges_human": counts["human_merges"],
                "same_event_merges_model": counts["model_merges"],
                "same_event_candidates_for_review": counts["same_event_review"],
                "follow_on_story_links": counts["follow_on"],
                "follow_on_candidates_for_review": counts["follow_on_review"],
                "possible_matches_for_review": counts["review"],
                "manual_decisions_applied": counts["manual_decisions"],
                "same_topic_only": counts["same_topic"],
                "occurrences_recorded": counts["occurrences"],
                "strict_merge_lag_days_median": round(median(merge_lags), 2) if merge_lags else None,
                "resurface_threshold_days": RESURFACE_DAYS,
            },
            "disclosure": (
                "New means new relative to the historical event pool shown here. "
                "Repeated reporting can add coverage without adding a new event. "
                "During the pilot, model-proposed same-event merges and follow-on links "
                "require an accepted human decision before event history is changed."
            ),
        }
        write_json(PUBLIC_OUTPUT, public_payload)
        write_json(
            PRIVATE_REVIEW,
            {
                **public_payload,
                "decisions": decisions,
                "manual_decisions": manual_decision_meta,
                "models": {
                    "embedding": {"name": EMBEDDING_MODEL, "revision": embedding_revision},
                    "pair": {"name": MODERNBERT_MODEL, "revision": pair_revision},
                    "verifier": {"name": QWEN_REPO, "revision": verifier_revision},
                },
            },
        )
        finish_run(
            client,
            run_id,
            status="success",
            counts=counts,
            snapshot_id=snapshot_id,
            metadata={
                "dry_run": args.dry_run,
                "reconciler_version": RECONCILER_VERSION,
                "verifier_calls": counts["verifier_calls"],
                "manual_decisions": manual_decision_meta,
                "auto_link_model_follow_on": AUTO_LINK_MODEL_FOLLOW_ON,
                "auto_apply_model_same_event": AUTO_APPLY_MODEL_SAME_EVENT,
            },
        )
        print(json.dumps({"mode": mode, **counts, "registry_snapshot_id": snapshot_id}, indent=2))
        return 0
    except Exception as exc:
        try:
            finish_run(
                client,
                run_id,
                status="failed",
                counts=counts,
                snapshot_id=snapshot_id,
                metadata={"error": f"{type(exc).__name__}: {str(exc)[:1000]}"},
            )
        except Exception:
            pass
        raise
    finally:
        stop_llama_server(process, handle)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReconciliationError as exc:
        print(f"Longitudinal reconciliation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
