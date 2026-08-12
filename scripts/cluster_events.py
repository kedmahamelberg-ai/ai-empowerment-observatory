#!/usr/bin/env python3
"""Cluster the latest collected AI-news articles into unique real-world events.

Stage 7B.2 is deliberately conservative:
- It preserves every article for the future Coverage Lens.
- It creates event clusters for the Event Lens.
- It does NOT classify empowerment yet.
- Thresholds are provisional and must be reviewed before weekly automation.

Primary semantic signal:
sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2

Supporting signals:
- Google News story_token when available
- publication/observation timing
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np
from huggingface_hub import HfApi
from sentence_transformers import SentenceTransformer
from supabase import Client, create_client

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = ROOT / "review" / "events" / "latest.json"

MODEL_NAME = os.environ.get(
    "EVENT_EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
SIMILARITY_THRESHOLD = float(os.environ.get("EVENT_SIMILARITY_THRESHOLD", "0.82"))
MAX_DAY_GAP = int(os.environ.get("EVENT_MAX_DAY_GAP", "4"))
CLUSTERING_VERSION = "7B.2"
METHOD_NAME = "multilingual_minilm_greedy_v1"


class ClusteringError(RuntimeError):
    pass


@dataclass
class Article:
    article_id: str
    headline: str
    publisher: str
    published_at: str | None
    first_seen_at: str
    last_seen_at: str
    story_token: str | None
    min_search_rank: int
    search_markets: list[str]

    @property
    def date(self) -> datetime:
        for value in (self.published_at, self.first_seen_at):
            if value:
                try:
                    return datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    pass
        return datetime.now(timezone.utc)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise ClusteringError(f"{name} is missing from the workflow environment.")
    return value


def first_row(response: Any, context: str) -> dict[str, Any]:
    data = getattr(response, "data", None)
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict) and data:
        return data
    raise ClusteringError(f"Supabase returned no row while {context}.")


def parse_source_metadata(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def stable_event_key(canonical_article_id: str) -> str:
    digest = hashlib.sha256(canonical_article_id.encode("utf-8")).hexdigest()[:24]
    return f"evt_{digest}"


def date_gap_days(a: Article, b: Article) -> float:
    return abs((a.date - b.date).total_seconds()) / 86400.0


def cluster_date_span_days(indices: list[int], articles: list[Article]) -> float:
    dates = [articles[i].date for i in indices]
    if len(dates) < 2:
        return 0.0
    return (max(dates) - min(dates)).total_seconds() / 86400.0


def pairwise_cluster_stats(
    indices: list[int],
    similarity: np.ndarray,
) -> tuple[float | None, float | None]:
    if len(indices) < 2:
        return None, None
    values: list[float] = []
    for pos, i in enumerate(indices):
        for j in indices[pos + 1 :]:
            values.append(float(similarity[i, j]))
    return mean(values), min(values)


def same_story_token(a: Article, b: Article) -> bool:
    return bool(a.story_token and b.story_token and a.story_token == b.story_token)


def choose_canonical(indices: list[int], articles: list[Article]) -> int:
    return min(
        indices,
        key=lambda i: (
            articles[i].min_search_rank,
            -len(articles[i].headline),
            articles[i].headline.casefold(),
        ),
    )


def greedy_clusters(
    articles: list[Article],
    similarity: np.ndarray,
) -> list[list[int]]:
    """Conservative complete-ish greedy clustering.

    An article joins a cluster when:
    - it shares a Google News story_token with at least one member; OR
    - its average similarity to cluster members >= threshold AND its weakest
      pair is no more than 0.05 below threshold;
    - the resulting cluster date span is within MAX_DAY_GAP.

    This avoids simple connected-component chaining while keeping the pilot
    transparent enough to tune manually.
    """
    order = sorted(
        range(len(articles)),
        key=lambda i: (
            articles[i].date,
            articles[i].min_search_rank,
            articles[i].headline.casefold(),
        ),
    )

    clusters: list[list[int]] = []

    for i in order:
        candidates: list[tuple[float, int]] = []

        for cluster_index, members in enumerate(clusters):
            prospective = members + [i]
            if cluster_date_span_days(prospective, articles) > MAX_DAY_GAP:
                continue

            token_match = any(
                same_story_token(articles[i], articles[j]) for j in members
            )

            sims = [float(similarity[i, j]) for j in members]
            avg_sim = mean(sims) if sims else 0.0
            min_sim = min(sims) if sims else 0.0

            semantic_match = (
                avg_sim >= SIMILARITY_THRESHOLD
                and min_sim >= max(0.0, SIMILARITY_THRESHOLD - 0.05)
            )

            if token_match or semantic_match:
                score = max(avg_sim, 1.0 if token_match else avg_sim)
                candidates.append((score, cluster_index))

        if not candidates:
            clusters.append([i])
            continue

        _, best_cluster_index = max(candidates, key=lambda item: item[0])
        clusters[best_cluster_index].append(i)

    return clusters


def get_latest_collection_run(client: Client) -> dict[str, Any]:
    response = (
        client.table("collection_runs")
        .select("run_id,run_key,started_at,completed_at,status")
        .in_("status", ["success", "partial"])
        .order("started_at", desc=True)
        .limit(1)
        .execute()
    )
    return first_row(response, "reading the latest successful collection run")


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
    observations = getattr(observations_response, "data", None) or []
    if not observations:
        raise ClusteringError(
            f"No article observations found for collection run {run_id}."
        )

    observation_meta: dict[str, dict[str, Any]] = {}
    for row in observations:
        article_id = str(row["article_id"])
        meta = observation_meta.setdefault(
            article_id,
            {"min_rank": 10**9, "markets": set()},
        )
        rank = row.get("search_rank")
        if rank is not None:
            meta["min_rank"] = min(meta["min_rank"], int(rank))
        market = str(row.get("search_country_iso3") or "").strip()
        if market:
            meta["markets"].add(market)

    article_ids = list(observation_meta)
    rows: list[dict[str, Any]] = []

    for start in range(0, len(article_ids), 150):
        batch = article_ids[start : start + 150]
        response = (
            client.table("articles")
            .select(
                "article_id,headline,publisher,published_at,"
                "first_seen_at,last_seen_at,source_metadata"
            )
            .in_("article_id", batch)
            .execute()
        )
        rows.extend(getattr(response, "data", None) or [])

    articles: list[Article] = []
    for row in rows:
        article_id = str(row["article_id"])
        meta = observation_meta[article_id]
        source_metadata = parse_source_metadata(row.get("source_metadata"))
        articles.append(
            Article(
                article_id=article_id,
                headline=str(row.get("headline") or "").strip(),
                publisher=str(row.get("publisher") or "Unknown source").strip(),
                published_at=row.get("published_at"),
                first_seen_at=str(row.get("first_seen_at")),
                last_seen_at=str(row.get("last_seen_at")),
                story_token=(
                    str(source_metadata.get("story_token")).strip()
                    if source_metadata.get("story_token")
                    else None
                ),
                min_search_rank=(
                    int(meta["min_rank"]) if meta["min_rank"] < 10**9 else 9999
                ),
                search_markets=sorted(meta["markets"]),
            )
        )

    articles = [article for article in articles if article.headline]
    if not articles:
        raise ClusteringError("No usable article headlines were found.")
    return articles


def register_model_version(
    client: Client,
    revision: str,
) -> str:
    row = {
        "provider": "huggingface",
        "model_name": MODEL_NAME,
        "model_revision": revision,
        "task": "event_clustering_embedding",
        "language_scope": "multilingual",
        "notes": (
            "Sentence embedding model used for provisional multilingual "
            "news-event clustering."
        ),
    }
    response = (
        client.table("model_versions")
        .upsert(
            row,
            on_conflict="provider,model_name,model_revision,task",
        )
        .select("model_version_id")
        .execute()
    )
    return str(first_row(response, "registering clustering model")["model_version_id"])


def start_clustering_run(
    client: Client,
    *,
    collection_run_id: str,
    model_version_id: str,
) -> tuple[str, str]:
    started = utc_now()
    run_key = started.strftime("cluster_%Y%m%dT%H%M%SZ")
    response = (
        client.table("event_clustering_runs")
        .insert(
            {
                "collection_run_id": collection_run_id,
                "model_version_id": model_version_id,
                "run_key": run_key,
                "started_at": iso_z(started),
                "status": "running",
                "similarity_threshold": SIMILARITY_THRESHOLD,
                "max_day_gap": MAX_DAY_GAP,
                "clustering_version": CLUSTERING_VERSION,
                "notes": (
                    "Pilot clustering. Manual cluster review required before "
                    "automatic weekly integration."
                ),
            }
        )
        .select("clustering_run_id")
        .execute()
    )
    clustering_run_id = str(
        first_row(response, "starting clustering run")["clustering_run_id"]
    )
    return clustering_run_id, run_key


def finish_clustering_run(
    client: Client,
    *,
    clustering_run_id: str,
    status: str,
    article_count: int,
    event_count: int,
    multi_article_event_count: int,
    review_required_count: int,
) -> None:
    (
        client.table("event_clustering_runs")
        .update(
            {
                "completed_at": iso_z(utc_now()),
                "status": status,
                "article_count": article_count,
                "event_count": event_count,
                "multi_article_event_count": multi_article_event_count,
                "review_required_count": review_required_count,
            }
        )
        .eq("clustering_run_id", clustering_run_id)
        .execute()
    )


def existing_event_ids_for_articles(
    client: Client,
    article_ids: list[str],
) -> set[str]:
    found: set[str] = set()
    for start in range(0, len(article_ids), 150):
        batch = article_ids[start : start + 150]
        response = (
            client.table("event_articles")
            .select("event_id,article_id")
            .in_("article_id", batch)
            .execute()
        )
        for row in getattr(response, "data", None) or []:
            found.add(str(row["event_id"]))
    return found


def persist_cluster(
    client: Client,
    *,
    clustering_run_id: str,
    indices: list[int],
    articles: list[Article],
    similarity: np.ndarray,
) -> dict[str, Any]:
    canonical_index = choose_canonical(indices, articles)
    canonical = articles[canonical_index]

    avg_similarity, min_similarity = pairwise_cluster_stats(indices, similarity)

    linked_event_ids = existing_event_ids_for_articles(
        client,
        [articles[i].article_id for i in indices],
    )

    weak_cluster = (
        len(indices) > 1
        and (
            avg_similarity is not None
            and avg_similarity < min(0.95, SIMILARITY_THRESHOLD + 0.04)
        )
    )
    multiple_existing_events = len(linked_event_ids) > 1

    requires_review = bool(weak_cluster or multiple_existing_events)
    review_reasons: list[str] = []
    if weak_cluster:
        review_reasons.append("borderline semantic cohesion")
    if multiple_existing_events:
        review_reasons.append("articles already linked to multiple existing events")

    first_seen = min(articles[i].first_seen_at for i in indices)
    last_seen = max(articles[i].last_seen_at for i in indices)
    event_date = min(articles[i].date.date().isoformat() for i in indices)

    event_payload = {
        "event_title": canonical.headline,
        "event_date": event_date,
        "first_seen_at": first_seen,
        "last_seen_at": last_seen,
        "clustering_run_id": clustering_run_id,
        "clustering_method": METHOD_NAME,
        "cluster_confidence": (
            round(float(avg_similarity), 4)
            if avg_similarity is not None
            else 1.0
        ),
        "requires_cluster_review": requires_review,
        "cluster_review_reason": (
            "; ".join(review_reasons) if review_reasons else None
        ),
        "updated_at": iso_z(utc_now()),
    }

    if len(linked_event_ids) == 1:
        event_id = next(iter(linked_event_ids))
        (
            client.table("events")
            .update(event_payload)
            .eq("event_id", event_id)
            .execute()
        )
    else:
        event_key = stable_event_key(canonical.article_id)
        response = (
            client.table("events")
            .upsert(
                {
                    "canonical_event_key": event_key,
                    **event_payload,
                },
                on_conflict="canonical_event_key",
            )
            .select("event_id")
            .execute()
        )
        event_id = str(
            first_row(response, "upserting an event cluster")["event_id"]
        )

    # Keep one canonical source in this event for the current clustering run.
    (
        client.table("event_articles")
        .update({"is_canonical_source": False})
        .eq("event_id", event_id)
        .execute()
    )

    rows = []
    for i in indices:
        article = articles[i]
        rows.append(
            {
                "event_id": event_id,
                "article_id": article.article_id,
                "is_canonical_source": i == canonical_index,
                "similarity_score": (
                    1.0
                    if i == canonical_index
                    else round(float(similarity[canonical_index, i]), 4)
                ),
            }
        )

    (
        client.table("event_articles")
        .upsert(
            rows,
            on_conflict="event_id,article_id",
        )
        .execute()
    )

    return {
        "event_id": event_id,
        "event_title": canonical.headline,
        "event_date": event_date,
        "article_count": len(indices),
        "average_similarity": (
            round(float(avg_similarity), 4)
            if avg_similarity is not None
            else None
        ),
        "minimum_similarity": (
            round(float(min_similarity), 4)
            if min_similarity is not None
            else None
        ),
        "requires_review": requires_review,
        "review_reason": "; ".join(review_reasons) if review_reasons else "",
        "articles": [
            {
                "article_id": articles[i].article_id,
                "headline": articles[i].headline,
                "publisher": articles[i].publisher,
                "published_at": articles[i].published_at,
                "search_markets": articles[i].search_markets,
                "search_rank": articles[i].min_search_rank,
                "similarity_to_canonical": (
                    1.0
                    if i == canonical_index
                    else round(float(similarity[canonical_index, i]), 4)
                ),
                "canonical": i == canonical_index,
            }
            for i in sorted(
                indices,
                key=lambda j: (
                    articles[j].min_search_rank,
                    articles[j].headline.casefold(),
                ),
            )
        ],
    }


def write_review(payload: dict[str, Any]) -> None:
    REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = REVIEW_PATH.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(REVIEW_PATH)


def main() -> int:
    supabase_url = required_env("SUPABASE_URL")
    supabase_secret_key = required_env("SUPABASE_SECRET_KEY")
    client: Client = create_client(supabase_url, supabase_secret_key)

    collection_run = get_latest_collection_run(client)
    articles = load_latest_articles(client, str(collection_run["run_id"]))

    print(f"Latest collection run: {collection_run['run_key']}")
    print(f"Articles to cluster: {len(articles)}")
    print(f"Embedding model: {MODEL_NAME}")
    print(f"Similarity threshold: {SIMILARITY_THRESHOLD}")
    print(f"Max date span: {MAX_DAY_GAP} days")

    # Resolve and record the exact model commit used by this run.
    try:
        model_revision = HfApi().model_info(MODEL_NAME).sha or "unknown"
    except Exception as exc:
        print(f"Warning: could not resolve Hugging Face revision: {exc}")
        model_revision = "unknown"

    model_version_id = register_model_version(client, model_revision)

    clustering_run_id, clustering_run_key = start_clustering_run(
        client,
        collection_run_id=str(collection_run["run_id"]),
        model_version_id=model_version_id,
    )

    try:
        model = SentenceTransformer(MODEL_NAME)
        texts = [article.headline for article in articles]
        embeddings = model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=True,
        )
        embeddings = np.asarray(embeddings, dtype=np.float32)
        similarity = embeddings @ embeddings.T

        clusters = greedy_clusters(articles, similarity)
        cluster_records: list[dict[str, Any]] = []

        for number, indices in enumerate(clusters, start=1):
            record = persist_cluster(
                client,
                clustering_run_id=clustering_run_id,
                indices=indices,
                articles=articles,
                similarity=similarity,
            )
            record["cluster_number"] = number
            cluster_records.append(record)

        cluster_records.sort(
            key=lambda item: (
                -int(item["article_count"]),
                item["event_title"].casefold(),
            )
        )

        multi_article = sum(
            1 for item in cluster_records if int(item["article_count"]) > 1
        )
        review_required = sum(
            1 for item in cluster_records if item["requires_review"]
        )

        finish_clustering_run(
            client,
            clustering_run_id=clustering_run_id,
            status="success",
            article_count=len(articles),
            event_count=len(cluster_records),
            multi_article_event_count=multi_article,
            review_required_count=review_required,
        )

        payload = {
            "meta": {
                "stage": "7B.2",
                "status": "provisional event clusters for human review",
                "collection_run_id": collection_run["run_id"],
                "collection_run_key": collection_run["run_key"],
                "clustering_run_id": clustering_run_id,
                "clustering_run_key": clustering_run_key,
                "model_name": MODEL_NAME,
                "model_revision": model_revision,
                "method": METHOD_NAME,
                "similarity_threshold": SIMILARITY_THRESHOLD,
                "max_day_gap": MAX_DAY_GAP,
                "article_count": len(articles),
                "event_count": len(cluster_records),
                "multi_article_event_count": multi_article,
                "singleton_event_count": len(cluster_records) - multi_article,
                "review_required_count": review_required,
                "warning": (
                    "These clusters are provisional. They do not yet affect "
                    "the Observatory indices. Review cluster quality before "
                    "automatic weekly use."
                ),
            },
            "events": cluster_records,
        }
        write_review(payload)

        print()
        print(f"Event clusters: {len(cluster_records)}")
        print(f"Multi-article events: {multi_article}")
        print(f"Needs review: {review_required}")
        print(f"Review file: {REVIEW_PATH}")
        return 0

    except Exception:
        finish_clustering_run(
            client,
            clustering_run_id=clustering_run_id,
            status="failed",
            article_count=len(articles),
            event_count=0,
            multi_article_event_count=0,
            review_required_count=0,
        )
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ClusteringError as exc:
        print(f"Clustering failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
