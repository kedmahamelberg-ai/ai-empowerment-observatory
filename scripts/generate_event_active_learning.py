#!/usr/bin/env python3
"""Stage 7B.2C — active-learning sample for event identity.

Goal:
Expand the human gold set with HIGH-INFORMATION article pairs rather than
random examples.

Human labels are deliberately binary + uncertainty:
- same_event
- not_same_event
- unclear_from_headlines

Candidate selection combines:
- multilingual MiniLM similarity
- task-specific ModernBERT same-event probability
- Qwen3-4B binary event verification
- publication/observation day gap
- Google News story-token match
- cross-language status
- current provisional event-cluster membership
- source snippets when available

The human review page hides all model predictions to avoid anchoring.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests
import torch
import torch.nn.functional as F
from huggingface_hub import HfApi
from sentence_transformers import SentenceTransformer
from supabase import Client, create_client
from transformers import AutoModelForSequenceClassification, AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "review" / "events" / "active-learning" / "latest.json"
GOLD_PATH = ROOT / "validation" / "event_pair_gold_v1.csv"

TRANSLATION_PROFILE = "validated_language_routing_v3"
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODERNBERT_MODEL = "Juanillaberia/articles-pairs-event-detection"
QWEN_REPO = "Qwen/Qwen3-4B-GGUF"
QWEN_QUANT = "Q4_K_M"

LLAMA_SERVER_BIN = os.environ.get(
    "LLAMA_SERVER_BIN",
    "/tmp/llama.cpp/build/bin/llama-server",
)
SERVER_URL = "http://127.0.0.1:8080/v1/chat/completions"
HEALTH_URL = "http://127.0.0.1:8080/health"

MAX_DAY_GAP = 5.0
BASE_SIMILARITY_FLOOR = 0.58
QWEN_CANDIDATE_N = 60
FINAL_SAMPLE_N = 45
MAX_PAIR_APPEARANCES_PER_ARTICLE = 5
MODERNBERT_BINARY_THRESHOLD = 0.45

SELECTION_BUCKETS = [
    ("model_disagreement", 15),
    ("high_similarity_not_same", 10),
    ("low_similarity_same", 8),
    ("cross_language", 8),
    ("cluster_conflict", 6),
    ("story_token_match", 4),
]


class ActiveLearningError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise ActiveLearningError(f"{name} is missing.")
    return value


def canonical_pair_id(a: str, b: str) -> str:
    return "__".join(sorted([str(a), str(b)]))


def load_existing_gold_pair_ids() -> set[str]:
    if not GOLD_PATH.exists():
        return set()

    frame = pd.read_csv(GOLD_PATH)
    if "pair_id" not in frame.columns:
        return set()

    result = set()
    for pair_id in frame["pair_id"].dropna().astype(str):
        parts = pair_id.split("__", 1)
        if len(parts) == 2:
            result.add(canonical_pair_id(parts[0], parts[1]))
    return result


def latest_collection(client: Client) -> dict[str, Any]:
    response = (
        client.table("collection_runs")
        .select("run_id,run_key,started_at,status")
        .in_("status", ["success", "partial"])
        .order("started_at", desc=True)
        .limit(1)
        .execute()
    )
    data = getattr(response, "data", None) or []
    if not data:
        raise ActiveLearningError("No successful collection run found.")
    return data[0]


def load_latest_articles(client: Client, run_id: str) -> list[dict[str, Any]]:
    obs_response = (
        client.table("article_observations")
        .select("article_id,search_country_iso3,search_language,search_rank,observed_at")
        .eq("run_id", run_id)
        .execute()
    )
    observations = getattr(obs_response, "data", None) or []
    if not observations:
        raise ActiveLearningError("Latest collection has no article observations.")

    observation_meta: dict[str, dict[str, Any]] = {}
    for row in observations:
        aid = str(row["article_id"])
        meta = observation_meta.setdefault(
            aid,
            {"markets": set(), "languages": set(), "min_rank": 9999},
        )
        if row.get("search_country_iso3"):
            meta["markets"].add(str(row["search_country_iso3"]))
        if row.get("search_language"):
            meta["languages"].add(str(row["search_language"]).lower())
        if row.get("search_rank") is not None:
            meta["min_rank"] = min(meta["min_rank"], int(row["search_rank"]))

    article_ids = sorted(observation_meta)
    rows: list[dict[str, Any]] = []

    for start in range(0, len(article_ids), 150):
        batch = article_ids[start:start + 150]
        response = (
            client.table("articles")
            .select(
                "article_id,headline,publisher,canonical_url,"
                "published_at,first_seen_at,source_metadata"
            )
            .in_("article_id", batch)
            .execute()
        )
        rows.extend(getattr(response, "data", None) or [])

    result = []
    for row in rows:
        headline = str(row.get("headline") or "").strip()
        if not headline:
            continue

        aid = str(row["article_id"])
        meta = observation_meta[aid]

        source_metadata = row.get("source_metadata")
        if not isinstance(source_metadata, dict):
            source_metadata = {}

        snippet = ""
        for key in ["snippet", "description", "summary", "source_snippet", "text"]:
            value = source_metadata.get(key)
            if value and str(value).strip():
                snippet = str(value).strip()
                break

        story_token = source_metadata.get("story_token")

        result.append(
            {
                "article_id": aid,
                "headline": headline,
                "publisher": str(row.get("publisher") or "Unknown source"),
                "url": row.get("canonical_url"),
                "published_at": row.get("published_at"),
                "first_seen_at": row.get("first_seen_at"),
                "snippet": snippet,
                "story_token": str(story_token).strip() if story_token else None,
                "markets": sorted(meta["markets"]),
                "search_languages": sorted(meta["languages"]),
                "min_rank": meta["min_rank"],
            }
        )

    return result


def load_translations(client: Client, article_ids: list[str]) -> dict[str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for start in range(0, len(article_ids), 150):
        batch = article_ids[start:start + 150]
        response = (
            client.table("article_translations")
            .select(
                "article_id,source_language_iso2,translated_headline,"
                "requires_review,review_reason,created_at"
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


def load_current_event_map(client: Client, article_ids: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for start in range(0, len(article_ids), 150):
        batch = article_ids[start:start + 150]
        response = (
            client.table("event_articles")
            .select("article_id,event_id")
            .in_("article_id", batch)
            .execute()
        )
        for row in getattr(response, "data", None) or []:
            mapping[str(row["article_id"])] = str(row["event_id"])
    return mapping


def normalized_headline(article: dict[str, Any], translations: dict[str, dict[str, Any]]) -> str:
    row = translations.get(article["article_id"])
    if not row:
        return article["headline"]
    value = str(row.get("translated_headline") or "").strip()
    return value or article["headline"]


def source_language(article: dict[str, Any], translations: dict[str, dict[str, Any]]) -> str:
    row = translations.get(article["article_id"])
    if row and row.get("source_language_iso2"):
        return str(row["source_language_iso2"])

    languages = article.get("search_languages") or []
    if languages:
        value = str(languages[0]).lower()
        if value.startswith("zh"):
            return "zh"
        if value.startswith("fr"):
            return "fr"
        if value.startswith("en"):
            return "en"
    return "unknown"


def parse_dt(value: str | None) -> float:
    if not value:
        return 0.0
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0


def day_gap(a: dict[str, Any], b: dict[str, Any]) -> float:
    ta = parse_dt(a.get("published_at") or a.get("first_seen_at"))
    tb = parse_dt(b.get("published_at") or b.get("first_seen_at"))
    if ta == 0.0 or tb == 0.0:
        return 0.0
    return abs(ta - tb) / 86400.0


def start_llama_server() -> tuple[subprocess.Popen, Any]:
    log_path = Path("/tmp/active-learning-qwen.log")
    handle = log_path.open("w", encoding="utf-8")

    command = [
        LLAMA_SERVER_BIN,
        "-hf",
        f"{QWEN_REPO}:{QWEN_QUANT}",
        "--host", "127.0.0.1",
        "--port", "8080",
        "-c", "4096",
        "-np", "1",
        "--jinja",
        "-ngl", "0",
    ]

    process = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT)
    deadline = time.time() + 480

    while time.time() < deadline:
        if process.poll() is not None:
            handle.flush()
            try:
                print(log_path.read_text(encoding="utf-8")[-10000:], file=sys.stderr)
            except Exception:
                pass
            raise ActiveLearningError("Qwen server exited during startup.")

        try:
            response = requests.get(HEALTH_URL, timeout=3)
            if response.ok:
                return process, handle
        except requests.RequestException:
            pass

        time.sleep(2)

    raise ActiveLearningError("Qwen server did not become healthy.")


def stop_llama_server(process: subprocess.Popen | None, handle: Any | None) -> None:
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
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ActiveLearningError(f"Qwen output contained no JSON: {text}")
    return json.loads(match.group(0))


def qwen_verify(
    a: dict[str, Any],
    b: dict[str, Any],
    english_a: str,
    english_b: str,
    gap: float,
    token_match: bool,
) -> dict[str, Any]:
    snippet_a = a.get("snippet") or ""
    snippet_b = b.get("snippet") or ""

    prompt = f"""
/no_think

Determine whether two news records report the SAME SPECIFIC REAL-WORLD EVENT.

Use only:
- same_event
- not_same_event
- unclear

same_event means the same specific occurrence, announcement, study result,
decision, launch, incident, meeting, speech, policy action, etc.

not_same_event includes:
- merely the same topic
- the same organization but a different occurrence
- the same policy area but a different article/event
- commentary/opinion pieces that are not reporting the same occurrence

unclear means the headline/snippet evidence is genuinely insufficient.

Do not use broad topical similarity as evidence of event identity.

ARTICLE A
Publisher: {a["publisher"]}
Original headline: {a["headline"]}
English normalization: {english_a}
Snippet if available: {snippet_a}

ARTICLE B
Publisher: {b["publisher"]}
Original headline: {b["headline"]}
English normalization: {english_b}
Snippet if available: {snippet_b}

Context
Day gap: {gap:.2f}
Google News story-token match: {str(token_match).lower()}

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
                {"role": "system", "content": "You are a conservative news event-identity verifier."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 220,
            "stream": False,
        },
        timeout=180,
    )
    response.raise_for_status()

    result = extract_json(str(response.json()["choices"][0]["message"]["content"]))
    relationship = str(result.get("relationship") or "").strip()

    if relationship not in {"same_event", "not_same_event", "unclear"}:
        raise ActiveLearningError(f"Unexpected Qwen relationship: {relationship}")

    try:
        confidence = float(result.get("confidence", 0.0))
    except Exception:
        confidence = 0.0

    return {
        "relationship": relationship,
        "confidence": max(0.0, min(1.0, confidence)),
        "reason": str(result.get("reason") or ""),
    }


def modernbert_scores(tokenizer, model, pairs: list[dict[str, Any]]) -> list[float]:
    scores = []
    batch_size = 32

    for start in range(0, len(pairs), batch_size):
        batch = pairs[start:start + batch_size]
        inputs = tokenizer(
            text=[item["english_a"] for item in batch],
            text_pair=[item["english_b"] for item in batch],
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=128,
        )
        with torch.inference_mode():
            logits = model(**inputs).logits
            probs = F.softmax(logits, dim=-1)[:, 1]
        scores.extend(float(value) for value in probs.tolist())

    return scores


def base_candidate_score(item: dict[str, Any]) -> float:
    score = 0.0
    sim = item["minilm_similarity"]
    mb = item["modernbert_same_probability"]

    if 0.35 <= mb <= 0.65:
        score += 4.0
    if 0.70 <= sim <= 0.92:
        score += 2.5
    if item["cross_language"]:
        score += 2.5
    if item["story_token_match"]:
        score += 6.0
    if item["current_same_cluster"] and mb < 0.45:
        score += 3.0
    if (not item["current_same_cluster"]) and mb >= 0.55:
        score += 3.0
    if item["translation_review_any"]:
        score += 1.0
    score += max(0.0, 1.5 - 0.25 * item["day_gap"])

    return score


def final_information_score(item: dict[str, Any]) -> tuple[float, list[str]]:
    score = item["base_score"]
    reasons = []

    modern_binary = (
        "same_event"
        if item["modernbert_same_probability"] >= MODERNBERT_BINARY_THRESHOLD
        else "not_same_event"
    )
    qwen = item["qwen_relationship"]

    if qwen == "unclear":
        score += 6.0
        reasons.append("model_disagreement")
    elif qwen != modern_binary:
        score += 7.0
        reasons.append("model_disagreement")

    if item["minilm_similarity"] >= 0.78 and (
        modern_binary == "not_same_event" or qwen == "not_same_event"
    ):
        score += 4.0
        reasons.append("high_similarity_not_same")

    if item["minilm_similarity"] < 0.78 and (
        modern_binary == "same_event" or qwen == "same_event"
    ):
        score += 4.0
        reasons.append("low_similarity_same")

    if item["cross_language"]:
        reasons.append("cross_language")
    if item["story_token_match"]:
        reasons.append("story_token_match")

    model_same = modern_binary == "same_event" and qwen == "same_event"
    model_not_same = modern_binary == "not_same_event" and qwen in {"not_same_event", "unclear"}

    if (item["current_same_cluster"] and model_not_same) or (
        (not item["current_same_cluster"]) and model_same
    ):
        score += 5.0
        reasons.append("cluster_conflict")

    return score, sorted(set(reasons))


def select_diverse_sample(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = []
    selected_ids = set()
    article_counts: dict[str, int] = defaultdict(int)

    def can_add(item: dict[str, Any]) -> bool:
        if item["pair_id"] in selected_ids:
            return False
        if article_counts[item["article_a_id"]] >= MAX_PAIR_APPEARANCES_PER_ARTICLE:
            return False
        if article_counts[item["article_b_id"]] >= MAX_PAIR_APPEARANCES_PER_ARTICLE:
            return False
        return True

    def add(item: dict[str, Any]) -> None:
        selected.append(item)
        selected_ids.add(item["pair_id"])
        article_counts[item["article_a_id"]] += 1
        article_counts[item["article_b_id"]] += 1

    ordered = sorted(
        candidates,
        key=lambda item: (-item["information_score"], -item["minilm_similarity"], item["pair_id"]),
    )

    for bucket, target in SELECTION_BUCKETS:
        count = 0
        for item in ordered:
            if count >= target:
                break
            if bucket not in item["selection_reasons"]:
                continue
            if not can_add(item):
                continue
            add(item)
            count += 1
            if len(selected) >= FINAL_SAMPLE_N:
                return selected

    for item in ordered:
        if len(selected) >= FINAL_SAMPLE_N:
            break
        if can_add(item):
            add(item)

    return selected


def main() -> int:
    client: Client = create_client(
        required_env("SUPABASE_URL"),
        required_env("SUPABASE_SECRET_KEY"),
    )

    existing_gold = load_existing_gold_pair_ids()
    collection = latest_collection(client)
    articles = load_latest_articles(client, str(collection["run_id"]))

    if len(articles) < 2:
        raise ActiveLearningError("Not enough articles for active learning.")

    article_ids = [article["article_id"] for article in articles]
    translations = load_translations(client, article_ids)
    event_map = load_current_event_map(client, article_ids)

    embedder = SentenceTransformer(EMBEDDING_MODEL)
    english_headlines = [
        normalized_headline(article, translations)
        for article in articles
    ]
    embeddings = embedder.encode(
        english_headlines,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=True,
    )
    embeddings = np.asarray(embeddings, dtype=np.float32)
    similarity = embeddings @ embeddings.T

    candidate_pairs = []

    for i in range(len(articles)):
        for j in range(i + 1, len(articles)):
            a = articles[i]
            b = articles[j]

            pair_id = canonical_pair_id(a["article_id"], b["article_id"])
            if pair_id in existing_gold:
                continue

            gap = day_gap(a, b)
            if gap > MAX_DAY_GAP:
                continue

            sim = float(similarity[i, j])

            token_match = bool(
                a.get("story_token")
                and b.get("story_token")
                and a["story_token"] == b["story_token"]
            )

            current_same_cluster = bool(
                event_map.get(a["article_id"])
                and event_map.get(a["article_id"]) == event_map.get(b["article_id"])
            )

            if sim < BASE_SIMILARITY_FLOOR and not token_match and not current_same_cluster:
                continue

            lang_a = source_language(a, translations)
            lang_b = source_language(b, translations)

            translation_review_any = bool(
                translations.get(a["article_id"], {}).get("requires_review")
                or translations.get(b["article_id"], {}).get("requires_review")
            )

            candidate_pairs.append(
                {
                    "pair_id": pair_id,
                    "article_a_id": a["article_id"],
                    "article_b_id": b["article_id"],
                    "a": a,
                    "b": b,
                    "english_a": normalized_headline(a, translations),
                    "english_b": normalized_headline(b, translations),
                    "language_a": lang_a,
                    "language_b": lang_b,
                    "cross_language": lang_a != lang_b,
                    "translation_review_any": translation_review_any,
                    "day_gap": round(gap, 3),
                    "minilm_similarity": round(sim, 4),
                    "story_token_match": token_match,
                    "current_same_cluster": current_same_cluster,
                }
            )

    if not candidate_pairs:
        raise ActiveLearningError("No active-learning candidates generated.")

    modern_revision = HfApi().model_info(MODERNBERT_MODEL).sha or "unknown"
    tokenizer = AutoTokenizer.from_pretrained(MODERNBERT_MODEL, revision=modern_revision)
    modern_model = AutoModelForSequenceClassification.from_pretrained(
        MODERNBERT_MODEL,
        revision=modern_revision,
    )
    modern_model.eval()

    modern_scores = modernbert_scores(tokenizer, modern_model, candidate_pairs)

    for item, score in zip(candidate_pairs, modern_scores):
        item["modernbert_same_probability"] = round(score, 4)
        item["base_score"] = round(base_candidate_score(item), 4)

    preselected = sorted(
        candidate_pairs,
        key=lambda item: (-item["base_score"], -item["minilm_similarity"], item["pair_id"]),
    )[:QWEN_CANDIDATE_N]

    process = None
    handle = None

    try:
        process, handle = start_llama_server()

        for index, item in enumerate(preselected, start=1):
            qwen = qwen_verify(
                item["a"],
                item["b"],
                item["english_a"],
                item["english_b"],
                item["day_gap"],
                item["story_token_match"],
            )

            item["qwen_relationship"] = qwen["relationship"]
            item["qwen_confidence"] = round(qwen["confidence"], 4)
            item["qwen_reason"] = qwen["reason"]

            info_score, reasons = final_information_score(item)
            item["information_score"] = round(info_score, 4)
            item["selection_reasons"] = reasons

            print(
                f"[{index}/{len(preselected)}] "
                f"{item['pair_id']} -> {item['qwen_relationship']}"
            )
    finally:
        stop_llama_server(process, handle)

    selected = select_diverse_sample(preselected)
    if not selected:
        raise ActiveLearningError("No final active-learning sample selected.")

    public_items = []

    for index, item in enumerate(selected, start=1):
        a = item["a"]
        b = item["b"]

        public_items.append(
            {
                "sample_id": f"active_{index:03d}",
                "pair_id": item["pair_id"],
                "article_a_id": item["article_a_id"],
                "article_b_id": item["article_b_id"],
                "article_a": {
                    "original_headline": a["headline"],
                    "english_headline": item["english_a"],
                    "publisher": a["publisher"],
                    "url": a.get("url"),
                    "snippet": a.get("snippet") or "",
                    "published_at": a.get("published_at"),
                    "source_language": item["language_a"],
                },
                "article_b": {
                    "original_headline": b["headline"],
                    "english_headline": item["english_b"],
                    "publisher": b["publisher"],
                    "url": b.get("url"),
                    "snippet": b.get("snippet") or "",
                    "published_at": b.get("published_at"),
                    "source_language": item["language_b"],
                },
                "day_gap": item["day_gap"],
                "minilm_similarity": item["minilm_similarity"],
                "modernbert_same_probability": item["modernbert_same_probability"],
                "qwen_relationship": item["qwen_relationship"],
                "qwen_confidence": item["qwen_confidence"],
                "story_token_match": item["story_token_match"],
                "current_same_cluster": item["current_same_cluster"],
                "selection_reasons": item["selection_reasons"],
                "information_score": item["information_score"],
            }
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(
            {
                "meta": {
                    "stage": "7B.2C",
                    "purpose": "active-learning expansion of binary event-identity human gold labels",
                    "collection_run_key": collection["run_key"],
                    "translation_profile": TRANSLATION_PROFILE,
                    "embedding_model": EMBEDDING_MODEL,
                    "modernbert_model": MODERNBERT_MODEL,
                    "qwen_model": QWEN_REPO,
                    "candidate_pool_size": len(candidate_pairs),
                    "qwen_scored_candidates": len(preselected),
                    "sample_size": len(public_items),
                    "labels": ["same_event", "not_same_event", "unclear_from_headlines"],
                    "instruction": (
                        "Judge whether the records report the same specific real-world occurrence. "
                        "Do not infer from topic similarity. Choose unclear when the headline/snippet "
                        "evidence is genuinely insufficient."
                    ),
                    "warning": (
                        "Model predictions are stored in the JSON for later analysis but deliberately "
                        "hidden from the annotation interface to avoid anchoring."
                    ),
                },
                "pairs": public_items,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "candidate_pool_size": len(candidate_pairs),
                "qwen_scored": len(preselected),
                "selected": len(public_items),
            },
            indent=2,
        )
    )
    print(f"Output: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ActiveLearningError as exc:
        print(f"Active-learning generation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
