#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sentence_transformers import SentenceTransformer
from supabase import Client, create_client

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "review" / "events" / "calibration" / "pairs.json"

MODEL_NAME = os.environ.get(
    "EVENT_EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
MAX_DAY_GAP = int(os.environ.get("EVENT_MAX_DAY_GAP", "4"))
SEED = 42

BINS = [
    ("0.72–0.80", 0.72, 0.80, 8),
    ("0.80–0.86", 0.80, 0.86, 8),
    ("0.86–0.90", 0.86, 0.90, 8),
    ("0.90–1.00", 0.90, 1.001, 8),
]


class CalibrationError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise CalibrationError(f"{name} is missing.")
    return value


def parse_dt(value: str | None) -> datetime:
    if value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def first_row(response: Any, context: str) -> dict[str, Any]:
    data = getattr(response, "data", None)
    if isinstance(data, list) and data:
        return data[0]
    raise CalibrationError(f"No row returned while {context}.")


def latest_collection(client: Client) -> dict[str, Any]:
    response = (
        client.table("collection_runs")
        .select("run_id,run_key,started_at")
        .in_("status", ["success", "partial"])
        .order("started_at", desc=True)
        .limit(1)
        .execute()
    )
    return first_row(response, "reading latest collection")


def load_articles(client: Client, run_id: str) -> list[dict[str, Any]]:
    obs = (
        client.table("article_observations")
        .select(
            "article_id,search_country_iso3,search_language,search_rank,observed_at"
        )
        .eq("run_id", run_id)
        .execute()
    )
    observations = getattr(obs, "data", None) or []
    if not observations:
        raise CalibrationError("No observations found for latest collection.")

    meta: dict[str, dict[str, Any]] = {}
    for row in observations:
        aid = str(row["article_id"])
        entry = meta.setdefault(
            aid,
            {"markets": set(), "languages": set(), "min_rank": 9999},
        )
        if row.get("search_country_iso3"):
            entry["markets"].add(str(row["search_country_iso3"]))
        if row.get("search_language"):
            entry["languages"].add(str(row["search_language"]))
        if row.get("search_rank") is not None:
            entry["min_rank"] = min(entry["min_rank"], int(row["search_rank"]))

    article_ids = list(meta)
    rows: list[dict[str, Any]] = []
    for start in range(0, len(article_ids), 150):
        batch = article_ids[start:start+150]
        response = (
            client.table("articles")
            .select(
                "article_id,canonical_url,headline,publisher,published_at,"
                "first_seen_at,last_seen_at,source_metadata"
            )
            .in_("article_id", batch)
            .execute()
        )
        rows.extend(getattr(response, "data", None) or [])

    result = []
    for row in rows:
        aid = str(row["article_id"])
        source_meta = row.get("source_metadata")
        if not isinstance(source_meta, dict):
            source_meta = {}
        headline = str(row.get("headline") or "").strip()
        if not headline:
            continue
        result.append(
            {
                "article_id": aid,
                "url": row.get("canonical_url"),
                "headline": headline,
                "publisher": str(row.get("publisher") or "Unknown source"),
                "published_at": row.get("published_at"),
                "first_seen_at": row.get("first_seen_at"),
                "story_token": source_meta.get("story_token"),
                "markets": sorted(meta[aid]["markets"]),
                "languages": sorted(meta[aid]["languages"]),
                "min_rank": meta[aid]["min_rank"],
            }
        )
    return result


def current_event_map(client: Client, article_ids: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for start in range(0, len(article_ids), 150):
        batch = article_ids[start:start+150]
        response = (
            client.table("event_articles")
            .select("article_id,event_id")
            .in_("article_id", batch)
            .execute()
        )
        for row in getattr(response, "data", None) or []:
            mapping[str(row["article_id"])] = str(row["event_id"])
    return mapping


def select_pairs(
    articles: list[dict[str, Any]],
    similarity: np.ndarray,
    event_map: dict[str, str],
) -> list[dict[str, Any]]:
    candidates = []

    for i in range(len(articles)):
        for j in range(i + 1, len(articles)):
            a, b = articles[i], articles[j]
            da = parse_dt(a.get("published_at") or a.get("first_seen_at"))
            db = parse_dt(b.get("published_at") or b.get("first_seen_at"))
            gap = abs((da - db).total_seconds()) / 86400.0
            if gap > MAX_DAY_GAP:
                continue

            sim = float(similarity[i, j])
            if sim < 0.72:
                continue

            same_token = bool(
                a.get("story_token")
                and b.get("story_token")
                and a["story_token"] == b["story_token"]
            )

            e1, e2 = event_map.get(a["article_id"]), event_map.get(b["article_id"])
            currently_same_cluster = bool(e1 and e2 and e1 == e2)

            candidates.append(
                {
                    "pair_id": f"{a['article_id']}__{b['article_id']}",
                    "similarity": round(sim, 4),
                    "day_gap": round(gap, 2),
                    "story_token_match": same_token,
                    "current_cluster_same": currently_same_cluster,
                    "article_a": a,
                    "article_b": b,
                }
            )

    rng = random.Random(SEED)
    selected: list[dict[str, Any]] = []
    used = set()

    token_pairs = [p for p in candidates if p["story_token_match"]]
    token_pairs.sort(key=lambda p: (-p["similarity"], p["pair_id"]))
    for pair in token_pairs[:6]:
        selected.append({**pair, "sampling_stratum": "story-token match"})
        used.add(pair["pair_id"])

    for label, lower, upper, target_n in BINS:
        pool = [
            p for p in candidates
            if lower <= p["similarity"] < upper and p["pair_id"] not in used
        ]
        rng.shuffle(pool)
        pool.sort(
            key=lambda p: (
                not (
                    set(p["article_a"]["languages"])
                    != set(p["article_b"]["languages"])
                ),
                p["pair_id"],
            )
        )
        for pair in pool[:target_n]:
            selected.append({**pair, "sampling_stratum": label})
            used.add(pair["pair_id"])

    selected.sort(key=lambda p: (-p["similarity"], p["pair_id"]))
    return selected


def main() -> int:
    client: Client = create_client(
        required_env("SUPABASE_URL"),
        required_env("SUPABASE_SECRET_KEY"),
    )

    collection = latest_collection(client)
    articles = load_articles(client, str(collection["run_id"]))
    event_map = current_event_map(
        client,
        [article["article_id"] for article in articles],
    )

    model = SentenceTransformer(MODEL_NAME)
    embeddings = model.encode(
        [a["headline"] for a in articles],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=True,
    )
    embeddings = np.asarray(embeddings, dtype=np.float32)
    similarity = embeddings @ embeddings.T

    pairs = select_pairs(articles, similarity, event_map)
    if not pairs:
        raise CalibrationError("No calibration pairs were generated.")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "stage": "7B.2A",
            "purpose": "blind human calibration of same-event identity",
            "collection_run_id": collection["run_id"],
            "collection_run_key": collection["run_key"],
            "model_name": MODEL_NAME,
            "max_day_gap": MAX_DAY_GAP,
            "sample_size": len(pairs),
            "labels": [
                "same_event",
                "related_topic",
                "different_event",
                "unsure",
            ],
            "instruction": (
                "Judge whether the two headlines report the same specific "
                "real-world occurrence. 'Related topic' means substantively "
                "similar subject matter but a different occurrence."
            ),
        },
        "pairs": pairs,
    }
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Calibration pairs: {len(pairs)}")
    print(f"Output: {OUTPUT}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CalibrationError as exc:
        print(f"Calibration failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
