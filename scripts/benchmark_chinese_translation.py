#!/usr/bin/env python3
"""Blind Chinese→English benchmark for the AI Empowerment Observatory.

Candidates:
A/B/C are randomized per headline from:
1) DunnBC22/opus-mt-zh-en-Chinese_to_English
   - dedicated Chinese→English Marian model
2) tencent/Hy-MT2-1.8B-GGUF:Q4_K_M
   - translation-specialist model
3) Qwen/Qwen3-4B-GGUF:Q4_K_M
   - strong multilingual general-model baseline

The script does NOT change production translations.
"""

from __future__ import annotations

import gc
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests
import torch
from huggingface_hub import HfApi, snapshot_download
from quickmt import Translator as QuickMTTranslator
from supabase import Client, create_client

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = (
    ROOT
    / "review"
    / "translations"
    / "chinese-benchmark"
    / "latest.json"
)

SOURCE_PROFILE = "qwen3_1_7b_en_normalization_v2"

DEDICATED_MODEL = "quickmt/quickmt-zh-en"
HYMT_REPO = "tencent/Hy-MT2-1.8B-GGUF"
HYMT_QUANT = "Q4_K_M"
QWEN4_REPO = "Qwen/Qwen3-4B-GGUF"
QWEN4_QUANT = "Q4_K_M"

LLAMA_SERVER_BIN = os.environ.get(
    "LLAMA_SERVER_BIN",
    "/tmp/llama.cpp/build/bin/llama-server",
)
SERVER_URL = "http://127.0.0.1:8080/v1/chat/completions"
HEALTH_URL = "http://127.0.0.1:8080/health"

TARGET_N = 15
SEED = 20260812

# Difficult cases identified from the pilot. The selector first tries to include
# these patterns, then fills the sample with long/entity-rich Chinese headlines.
CURATED_PATTERNS = [
    "在常举行",
    "北京大学临床科学家",
    "之江实验室王坚院士",
    "千里马",
    "向善",
    "海外救场",
    "海岱舆情大模型",
    "人工智能局",
    "雄安人工智能实训基地",
    "生成式人工智能领域新设企业增长28.0%",
    "人工智能被用于设计全新病毒",
    "人工智能治理至关重要",
    "智能分析与自主发现",
]


class BenchmarkError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise BenchmarkError(f"{name} is missing.")
    return value


def load_chinese_rows(client: Client) -> list[dict[str, Any]]:
    response = (
        client.table("article_translations")
        .select(
            "article_id,original_headline,translated_headline,"
            "requires_review,review_reason,detection_confidence,created_at"
        )
        .eq("translation_profile", SOURCE_PROFILE)
        .eq("source_language_iso2", "zh")
        .eq("status", "translated")
        .order("created_at", desc=True)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    if not rows:
        raise BenchmarkError(
            f"No Chinese translations found for profile {SOURCE_PROFILE}."
        )

    newest: dict[str, dict[str, Any]] = {}
    for row in rows:
        aid = str(row["article_id"])
        if aid not in newest:
            newest[aid] = row
    return list(newest.values())


def difficulty_score(row: dict[str, Any]) -> tuple[int, int]:
    headline = str(row["original_headline"])
    score = 0

    if row.get("requires_review"):
        score += 100

    for pattern in CURATED_PATTERNS:
        if pattern in headline:
            score += 50

    if re.search(r"\d", headline):
        score += 8
    if any(mark in headline for mark in ["“", "”", "《", "》", "丨", "：", "——"]):
        score += 6
    if len(headline) >= 35:
        score += 4

    return score, len(headline)


def select_sample(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rng = random.Random(SEED)
    decorated = []

    for row in rows:
        score, length = difficulty_score(row)
        decorated.append((score, length, rng.random(), row))

    decorated.sort(
        key=lambda x: (
            -x[0],
            -x[1],
            x[2],
            str(x[3]["article_id"]),
        )
    )

    return [item[3] for item in decorated[:TARGET_N]]


def dedicated_translate(translator, headline: str) -> str:
    result = translator(headline, beam_size=5)
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, list) and result:
        return str(result[0]).strip()
    text = str(result).strip()
    if not text:
        raise BenchmarkError("quickmt returned an empty translation.")
    return text


def wait_for_server(process: subprocess.Popen, timeout_s: int = 480) -> None:
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        if process.poll() is not None:
            raise BenchmarkError(
                "llama.cpp server exited before becoming healthy."
            )

        try:
            response = requests.get(HEALTH_URL, timeout=3)
            if response.ok:
                return
        except requests.RequestException:
            pass

        time.sleep(2)

    raise BenchmarkError(
        f"llama.cpp server did not become healthy within {timeout_s}s."
    )


def start_llama_server(
    repo: str,
    quant: str,
    *,
    log_name: str,
) -> tuple[subprocess.Popen, Any]:
    log_path = Path(f"/tmp/{log_name}.log")
    log_handle = log_path.open("w", encoding="utf-8")

    command = [
        LLAMA_SERVER_BIN,
        "-hf",
        f"{repo}:{quant}",
        "--host",
        "127.0.0.1",
        "--port",
        "8080",
        "-c",
        "2048",
        "-np",
        "1",
        "--jinja",
        "-ngl",
        "0",
    ]

    process = subprocess.Popen(
        command,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )

    try:
        wait_for_server(process)
    except Exception:
        log_handle.flush()
        print(
            f"--- llama.cpp log: {log_path} ---",
            file=sys.stderr,
        )
        try:
            print(log_path.read_text(encoding="utf-8")[-10000:], file=sys.stderr)
        except Exception:
            pass
        raise

    return process, log_handle


def stop_llama_server(
    process: subprocess.Popen | None,
    log_handle: Any | None,
) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)

    if log_handle is not None:
        try:
            log_handle.close()
        except Exception:
            pass

    time.sleep(3)


def chat_completion(payload: dict[str, Any]) -> str:
    response = requests.post(
        SERVER_URL,
        json=payload,
        timeout=180,
    )
    response.raise_for_status()

    data = response.json()

    try:
        text = str(data["choices"][0]["message"]["content"]).strip()
    except Exception as exc:
        raise BenchmarkError(
            f"Unexpected llama.cpp response shape: {data}"
        ) from exc

    text = re.sub(
        r"<think>.*?</think>",
        "",
        text,
        flags=re.S,
    ).strip()

    text = text.strip('"').strip()

    if not text:
        raise BenchmarkError("Translation model returned empty output.")

    return text


def qwen4_translate(headline: str) -> str:
    prompt = (
        "/no_think\n"
        "Translate this Chinese news headline into natural, precise English. "
        "Preserve the specific event meaning, named entities, place names, "
        "organizations, people, numbers, negation, modality and comparisons. "
        "Translate idioms by meaning rather than word-for-word. "
        "Do not summarize, explain, infer, or add facts. "
        "Return only the English headline.\n\n"
        f"Headline: {headline}"
    )

    return chat_completion(
        {
            "model": f"{QWEN4_REPO}:{QWEN4_QUANT}",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a precise Chinese-to-English "
                        "news-headline translator."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "top_p": 0.8,
            "max_tokens": 160,
            "stream": False,
        }
    )


def hymt_translate(headline: str) -> str:
    # Tencent's own model card recommends asking only for the translated result.
    prompt = (
        "Translate the following text into English. "
        "Note that you should only output the translated result "
        "without any additional explanation:\n"
        f"{headline}"
    )

    return chat_completion(
        {
            "model": f"{HYMT_REPO}:{HYMT_QUANT}",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "top_p": 0.6,
            "top_k": 20,
            "repetition_penalty": 1.05,
            "max_tokens": 160,
            "stream": False,
        }
    )


def main() -> int:
    client: Client = create_client(
        required_env("SUPABASE_URL"),
        required_env("SUPABASE_SECRET_KEY"),
    )

    rows = load_chinese_rows(client)
    sample = select_sample(rows)

    if not sample:
        raise BenchmarkError("Chinese benchmark sample is empty.")

    print(f"Chinese challenge headlines: {len(sample)}")

    # Resolve exact revisions for provenance.
    dedicated_revision = (
        HfApi().model_info(DEDICATED_MODEL).sha or "unknown"
    )
    hymt_revision = HfApi().model_info(HYMT_REPO).sha or "unknown"
    qwen4_revision = HfApi().model_info(QWEN4_REPO).sha or "unknown"

    # 1) Dedicated Chinese→English model.
    print("Loading quickmt dedicated Chinese→English model...")
    quickmt_path = snapshot_download(
        DEDICATED_MODEL,
        revision=dedicated_revision,
        ignore_patterns=["eole-model/*"],
    )
    dedicated_translator = QuickMTTranslator(
        quickmt_path,
        device="cpu",
    )

    translations: dict[str, dict[str, str]] = {}

    for row in sample:
        aid = str(row["article_id"])
        translations[aid] = {
            "dedicated_zh_en": dedicated_translate(
                dedicated_translator,
                str(row["original_headline"]),
            )
        }

    del dedicated_translator
    gc.collect()

    # 2) Tencent Hy-MT2 translation specialist.
    process = None
    log_handle = None

    try:
        print("Starting Tencent Hy-MT2-1.8B Q4_K_M...")
        process, log_handle = start_llama_server(
            HYMT_REPO,
            HYMT_QUANT,
            log_name="hymt-server",
        )

        for row in sample:
            aid = str(row["article_id"])
            translations[aid]["hymt2_1_8b"] = hymt_translate(
                str(row["original_headline"])
            )
    finally:
        stop_llama_server(process, log_handle)

    # 3) Qwen3-4B baseline.
    process = None
    log_handle = None

    try:
        print("Starting Qwen3-4B Q4_K_M...")
        process, log_handle = start_llama_server(
            QWEN4_REPO,
            QWEN4_QUANT,
            log_name="qwen4-server",
        )

        for row in sample:
            aid = str(row["article_id"])
            translations[aid]["qwen3_4b"] = qwen4_translate(
                str(row["original_headline"])
            )
    finally:
        stop_llama_server(process, log_handle)

    rng = random.Random(SEED + 99)
    items = []

    for index, row in enumerate(sample, start=1):
        aid = str(row["article_id"])

        candidates = [
            ("dedicated_zh_en", translations[aid]["dedicated_zh_en"]),
            ("hymt2_1_8b", translations[aid]["hymt2_1_8b"]),
            ("qwen3_4b", translations[aid]["qwen3_4b"]),
        ]
        rng.shuffle(candidates)

        labels = ["A", "B", "C"]

        item = {
            "benchmark_id": f"zh_threeway_{index:02d}",
            "article_id": aid,
            "original_headline": row["original_headline"],
            "previous_qwen1_7b_translation": row["translated_headline"],
            "previous_qc_flag": bool(row.get("requires_review")),
            "previous_qc_reason": row.get("review_reason") or "",
        }

        for letter, (model_id, text) in zip(labels, candidates):
            item[f"candidate_{letter.lower()}"] = text
            item[f"candidate_{letter.lower()}_model"] = model_id

        items.append(item)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {
                "meta": {
                    "stage": "7B.2B-0e",
                    "purpose": (
                        "blind three-model Chinese-to-English "
                        "headline translation benchmark"
                    ),
                    "sample_size": len(items),
                    "selection": (
                        "difficult/entity-rich Chinese headlines from "
                        "current Observatory collection"
                    ),
                    "models": {
                        "dedicated_zh_en": {
                            "repo": DEDICATED_MODEL,
                            "revision": dedicated_revision,
                            "type": "dedicated Chinese-to-English CTranslate2 NMT",
                        },
                        "hymt2_1_8b": {
                            "repo": HYMT_REPO,
                            "quant": HYMT_QUANT,
                            "revision": hymt_revision,
                            "type": "translation-specialist multilingual",
                        },
                        "qwen3_4b": {
                            "repo": QWEN4_REPO,
                            "quant": QWEN4_QUANT,
                            "revision": qwen4_revision,
                            "type": "general multilingual LLM baseline",
                        },
                    },
                    "human_labels": [
                        "candidate_a_best",
                        "candidate_b_best",
                        "candidate_c_best",
                        "tie_a_b",
                        "tie_a_c",
                        "tie_b_c",
                        "all_three_accurate",
                        "none_adequate",
                        "unsure",
                    ],
                },
                "items": items,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Output: {OUTPUT}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BenchmarkError as exc:
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
