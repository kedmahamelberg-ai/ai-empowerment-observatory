#!/usr/bin/env python3
"""Stage 7B.2B — Same-event verifier benchmark.

Benchmarks the Observatory's human-coded event-pair gold set against:

1. ModernBERT task-specific event-pair classifier
   Juanillaberia/articles-pairs-event-detection

2. Qwen3-4B event verifier
   Original headline + validated English normalization + temporal/source context

3. Controlled agreement rule
   - both say SAME -> auto same_event
   - both say NOT SAME -> auto not_same
   - disagreement -> human review

The benchmark DOES NOT overwrite current event clusters.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import torch
import torch.nn.functional as F
from huggingface_hub import HfApi
from supabase import Client, create_client
from transformers import AutoModelForSequenceClassification, AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]

GOLD_PATH = ROOT / "validation" / "event_pair_gold_v1.csv"

OUTPUT_PATH = (
    ROOT
    / "review"
    / "events"
    / "verifier-benchmark"
    / "latest.json"
)

TRANSLATION_PROFILE = "validated_language_routing_v3"

MODERNBERT_MODEL = "Juanillaberia/articles-pairs-event-detection"

QWEN_REPO = "Qwen/Qwen3-4B-GGUF"
QWEN_QUANT = "Q4_K_M"

LLAMA_SERVER_BIN = os.environ.get(
    "LLAMA_SERVER_BIN",
    "/tmp/llama.cpp/build/bin/llama-server",
)

SERVER_URL = "http://127.0.0.1:8080/v1/chat/completions"
HEALTH_URL = "http://127.0.0.1:8080/health"

MODERNBERT_RAW_THRESHOLD = 0.55
MODERNBERT_DATE_THRESHOLD = 0.45
DATE_DECAY_LAMBDA = 0.20


class BenchmarkError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()

    if not value:
        raise BenchmarkError(f"{name} is missing.")

    return value


def parse_pair_id(pair_id: str) -> tuple[str, str]:
    parts = str(pair_id).split("__", 1)

    if len(parts) != 2:
        raise BenchmarkError(
            f"Unexpected pair_id format: {pair_id}"
        )

    return parts[0], parts[1]


def load_gold() -> pd.DataFrame:
    if not GOLD_PATH.exists():
        raise BenchmarkError(
            f"Gold label file missing: {GOLD_PATH}"
        )

    frame = pd.read_csv(GOLD_PATH)

    required = {
        "pair_id",
        "human_label",
        "similarity",
        "day_gap",
        "headline_a",
        "headline_b",
        "publisher_a",
        "publisher_b",
    }

    missing = required - set(frame.columns)

    if missing:
        raise BenchmarkError(
            f"Gold CSV missing columns: {sorted(missing)}"
        )

    frame = frame.dropna(
        subset=["human_label"]
    ).copy()

    if frame.empty:
        raise BenchmarkError("Gold CSV has no human labels.")

    return frame


def fetch_articles(
    client: Client,
    article_ids: list[str],
) -> dict[str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    unique_ids = sorted(set(article_ids))

    for start in range(0, len(unique_ids), 150):
        batch = unique_ids[start:start + 150]

        response = (
            client.table("articles")
            .select(
                "article_id,headline,publisher,published_at,"
                "canonical_url,source_metadata"
            )
            .in_("article_id", batch)
            .execute()
        )

        rows.extend(
            getattr(response, "data", None) or []
        )

    return {
        str(row["article_id"]): row
        for row in rows
    }


def fetch_translations(
    client: Client,
    article_ids: list[str],
) -> dict[str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    unique_ids = sorted(set(article_ids))

    for start in range(0, len(unique_ids), 150):
        batch = unique_ids[start:start + 150]

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

        rows.extend(
            getattr(response, "data", None) or []
        )

    newest: dict[str, dict[str, Any]] = {}

    for row in rows:
        aid = str(row["article_id"])

        if aid not in newest:
            newest[aid] = row

    return newest


def story_token(article: dict[str, Any] | None) -> str | None:
    if not article:
        return None

    metadata = article.get("source_metadata")

    if not isinstance(metadata, dict):
        return None

    value = metadata.get("story_token")

    return str(value).strip() if value else None


def normalized_headline(
    article_id: str,
    original: str,
    translations: dict[str, dict[str, Any]],
) -> str:
    row = translations.get(article_id)

    if not row:
        return original

    value = str(
        row.get("translated_headline") or ""
    ).strip()

    return value or original


def start_llama_server() -> tuple[subprocess.Popen, Any]:
    log_path = Path("/tmp/event-verifier-qwen.log")
    handle = log_path.open("w", encoding="utf-8")

    command = [
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
    ]

    process = subprocess.Popen(
        command,
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

            raise BenchmarkError(
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

    raise BenchmarkError(
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
        raise BenchmarkError(
            f"Qwen output contained no JSON object: {text}"
        )

    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise BenchmarkError(
            f"Could not parse Qwen JSON: {text}"
        ) from exc


def qwen_verify(
    *,
    original_a: str,
    english_a: str,
    publisher_a: str,
    original_b: str,
    english_b: str,
    publisher_b: str,
    day_gap: float,
    story_token_match: bool,
) -> dict[str, Any]:
    prompt = f"""
/no_think

You are verifying whether two news articles report the SAME SPECIFIC
REAL-WORLD EVENT.

The Observatory distinguishes:
- same_event: the same specific occurrence, announcement, decision,
  incident, launch, study result, policy action, speech, meeting, etc.
- related_topic: substantively related subject matter, but not the same
  occurrence.
- different_event: different occurrence and not merely another report
  of the same event.
- unclear: evidence supplied here is insufficient.

Do NOT label two articles "same_event" merely because they discuss the
same topic, technology, law, institution, country, trend, or debate.

Use both the original-language headline and the English normalization.
The original headline remains evidentiary if translation wording is imperfect.

Article A
Publisher: {publisher_a}
Original: {original_a}
English normalization: {english_a}

Article B
Publisher: {publisher_b}
Original: {original_b}
English normalization: {english_b}

Context
Publication/observation gap: {day_gap:.2f} days
Google News story-token match: {str(story_token_match).lower()}

Return ONLY valid JSON:
{{
  "relationship": "same_event | related_topic | different_event | unclear",
  "confidence": 0.00,
  "shared_event": "",
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
                        "You are a conservative news event-identity verifier."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 250,
            "stream": False,
        },
        timeout=180,
    )

    response.raise_for_status()

    content = str(
        response.json()["choices"][0]["message"]["content"]
    )

    result = extract_json(content)

    relationship = str(
        result.get("relationship") or ""
    ).strip()

    allowed = {
        "same_event",
        "related_topic",
        "different_event",
        "unclear",
    }

    if relationship not in allowed:
        raise BenchmarkError(
            f"Unexpected Qwen relationship: {relationship}"
        )

    try:
        confidence = float(
            result.get("confidence", 0.0)
        )
    except Exception:
        confidence = 0.0

    result["confidence"] = max(
        0.0,
        min(1.0, confidence),
    )

    return result


def binary_gold(label: str) -> int:
    return 1 if label == "same_event" else 0


def precision_recall_f1(
    y_true: list[int],
    y_pred: list[int],
) -> dict[str, float]:
    tp = sum(
        1
        for t, p in zip(y_true, y_pred)
        if t == 1 and p == 1
    )

    fp = sum(
        1
        for t, p in zip(y_true, y_pred)
        if t == 0 and p == 1
    )

    fn = sum(
        1
        for t, p in zip(y_true, y_pred)
        if t == 1 and p == 0
    )

    tn = sum(
        1
        for t, p in zip(y_true, y_pred)
        if t == 0 and p == 0
    )

    precision = (
        tp / (tp + fp)
        if tp + fp
        else 0.0
    )

    recall = (
        tp / (tp + fn)
        if tp + fn
        else 0.0
    )

    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )

    accuracy = (
        (tp + tn) / len(y_true)
        if y_true
        else 0.0
    )

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "accuracy": round(accuracy, 4),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
    }


def main() -> int:
    client: Client = create_client(
        required_env("SUPABASE_URL"),
        required_env("SUPABASE_SECRET_KEY"),
    )

    gold = load_gold()

    pairs = []

    all_ids = []

    for _, row in gold.iterrows():
        aid, bid = parse_pair_id(
            str(row["pair_id"])
        )

        all_ids.extend([aid, bid])

        pairs.append(
            {
                "pair_id": str(row["pair_id"]),
                "article_a_id": aid,
                "article_b_id": bid,
                "human_label": str(row["human_label"]),
                "embedding_similarity": float(row["similarity"]),
                "day_gap": float(row["day_gap"]),
                "headline_a_gold": str(row["headline_a"]),
                "headline_b_gold": str(row["headline_b"]),
                "publisher_a_gold": str(row["publisher_a"]),
                "publisher_b_gold": str(row["publisher_b"]),
            }
        )

    articles = fetch_articles(
        client,
        all_ids,
    )

    translations = fetch_translations(
        client,
        all_ids,
    )

    modernbert_revision = (
        HfApi().model_info(MODERNBERT_MODEL).sha
        or "unknown"
    )

    qwen_revision = (
        HfApi().model_info(QWEN_REPO).sha
        or "unknown"
    )

    print("Loading task-specific ModernBERT event-pair verifier...")

    modern_tokenizer = AutoTokenizer.from_pretrained(
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

    process = None
    log_handle = None

    try:
        process, log_handle = start_llama_server()

        results = []

        for index, pair in enumerate(pairs, start=1):
            aid = pair["article_a_id"]
            bid = pair["article_b_id"]

            article_a = articles.get(aid)
            article_b = articles.get(bid)

            original_a = str(
                (article_a or {}).get("headline")
                or pair["headline_a_gold"]
            )

            original_b = str(
                (article_b or {}).get("headline")
                or pair["headline_b_gold"]
            )

            publisher_a = str(
                (article_a or {}).get("publisher")
                or pair["publisher_a_gold"]
            )

            publisher_b = str(
                (article_b or {}).get("publisher")
                or pair["publisher_b_gold"]
            )

            english_a = normalized_headline(
                aid,
                original_a,
                translations,
            )

            english_b = normalized_headline(
                bid,
                original_b,
                translations,
            )

            token_a = story_token(article_a)
            token_b = story_token(article_b)

            token_match = bool(
                token_a
                and token_b
                and token_a == token_b
            )

            # ModernBERT is English-first, so use validated English normalization.
            modern_inputs = modern_tokenizer(
                text=english_a,
                text_pair=english_b,
                return_tensors="pt",
                truncation=True,
                max_length=128,
            )

            with torch.inference_mode():
                modern_logits = modern_model(
                    **modern_inputs
                ).logits

                modern_probs = F.softmax(
                    modern_logits,
                    dim=-1,
                )

            modern_same_raw = float(
                modern_probs[0][1].item()
            )

            modern_same_adjusted = (
                modern_same_raw
                * math.exp(
                    -DATE_DECAY_LAMBDA
                    * pair["day_gap"]
                )
            )

            modern_raw_label = (
                "same_event"
                if modern_same_raw
                >= MODERNBERT_RAW_THRESHOLD
                else "not_same"
            )

            modern_adjusted_label = (
                "same_event"
                if modern_same_adjusted
                >= MODERNBERT_DATE_THRESHOLD
                else "not_same"
            )

            qwen = qwen_verify(
                original_a=original_a,
                english_a=english_a,
                publisher_a=publisher_a,
                original_b=original_b,
                english_b=english_b,
                publisher_b=publisher_b,
                day_gap=pair["day_gap"],
                story_token_match=token_match,
            )

            qwen_binary = (
                "same_event"
                if qwen["relationship"] == "same_event"
                else "not_same"
            )

            if (
                modern_adjusted_label == "same_event"
                and qwen_binary == "same_event"
            ):
                ensemble = "same_event"

            elif (
                modern_adjusted_label == "not_same"
                and qwen_binary == "not_same"
            ):
                ensemble = "not_same"

            else:
                ensemble = "review"

            results.append(
                {
                    "index": index,
                    **pair,
                    "original_a": original_a,
                    "english_a": english_a,
                    "publisher_a": publisher_a,
                    "translation_a_review": bool(
                        translations.get(aid, {}).get(
                            "requires_review",
                            False,
                        )
                    ),
                    "original_b": original_b,
                    "english_b": english_b,
                    "publisher_b": publisher_b,
                    "translation_b_review": bool(
                        translations.get(bid, {}).get(
                            "requires_review",
                            False,
                        )
                    ),
                    "story_token_match": token_match,
                    "modernbert_same_probability_raw": round(
                        modern_same_raw,
                        4,
                    ),
                    "modernbert_same_probability_date_adjusted": round(
                        modern_same_adjusted,
                        4,
                    ),
                    "modernbert_raw_label": modern_raw_label,
                    "modernbert_date_label": modern_adjusted_label,
                    "qwen_relationship": qwen["relationship"],
                    "qwen_confidence": round(
                        float(qwen["confidence"]),
                        4,
                    ),
                    "qwen_shared_event": str(
                        qwen.get("shared_event") or ""
                    ),
                    "qwen_reason": str(
                        qwen.get("reason") or ""
                    ),
                    "ensemble_decision": ensemble,
                }
            )

    finally:
        stop_llama_server(
            process,
            log_handle,
        )

    true_binary = [
        binary_gold(item["human_label"])
        for item in results
    ]

    modern_raw_pred = [
        1
        if item["modernbert_raw_label"] == "same_event"
        else 0
        for item in results
    ]

    modern_adjusted_pred = [
        1
        if item["modernbert_date_label"] == "same_event"
        else 0
        for item in results
    ]

    qwen_pred = [
        1
        if item["qwen_relationship"] == "same_event"
        else 0
        for item in results
    ]

    qwen_three_class_correct = sum(
        1
        for item in results
        if item["qwen_relationship"] == item["human_label"]
    )

    auto_ensemble = [
        item
        for item in results
        if item["ensemble_decision"] != "review"
    ]

    ensemble_true = [
        binary_gold(item["human_label"])
        for item in auto_ensemble
    ]

    ensemble_pred = [
        1
        if item["ensemble_decision"] == "same_event"
        else 0
        for item in auto_ensemble
    ]

    summary = {
        "gold_pairs": len(results),
        "gold_same_event": sum(true_binary),
        "gold_not_same": len(true_binary) - sum(true_binary),

        "modernbert_raw": precision_recall_f1(
            true_binary,
            modern_raw_pred,
        ),

        "modernbert_date_adjusted": precision_recall_f1(
            true_binary,
            modern_adjusted_pred,
        ),

        "qwen_binary": precision_recall_f1(
            true_binary,
            qwen_pred,
        ),

        "qwen_three_class_accuracy": round(
            qwen_three_class_correct / len(results),
            4,
        ),

        "ensemble_auto_decided": len(auto_ensemble),
        "ensemble_review_count": (
            len(results) - len(auto_ensemble)
        ),
        "ensemble_coverage": round(
            len(auto_ensemble) / len(results),
            4,
        ),
        "ensemble_auto_metrics": (
            precision_recall_f1(
                ensemble_true,
                ensemble_pred,
            )
            if auto_ensemble
            else None
        ),
    }

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT_PATH.write_text(
        json.dumps(
            {
                "meta": {
                    "stage": "7B.2B",
                    "purpose": (
                        "same-event verifier benchmark against "
                        "human-coded Observatory pairs"
                    ),
                    "translation_profile": TRANSLATION_PROFILE,
                    "gold_file": (
                        "validation/event_pair_gold_v1.csv"
                    ),
                    "modernbert_model": MODERNBERT_MODEL,
                    "modernbert_revision": modernbert_revision,
                    "qwen_model": QWEN_REPO,
                    "qwen_quant": QWEN_QUANT,
                    "qwen_revision": qwen_revision,
                    "modernbert_raw_threshold": (
                        MODERNBERT_RAW_THRESHOLD
                    ),
                    "modernbert_date_threshold": (
                        MODERNBERT_DATE_THRESHOLD
                    ),
                    "date_decay_lambda": DATE_DECAY_LAMBDA,
                    "warning": (
                        "This is a small development benchmark. "
                        "Only four gold pairs are same-event. "
                        "Use results to choose architecture and "
                        "generate the next active-learning sample, "
                        "not as a final publication-grade estimate."
                    ),
                },
                "summary": summary,
                "pairs": results,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(json.dumps(summary, indent=2))
    print(f"Output: {OUTPUT_PATH}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BenchmarkError as exc:
        print(
            f"Verifier benchmark failed: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
