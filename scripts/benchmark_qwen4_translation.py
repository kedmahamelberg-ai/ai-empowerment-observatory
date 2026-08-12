#!/usr/bin/env python3
"""Blind difficult-case translation benchmark: Qwen3-1.7B vs Qwen3-4B Q4_K_M.

The current Qwen3-1.7B production normalization is read from Supabase.
Qwen3-4B is served locally by llama.cpp through its OpenAI-compatible API.

The challenge sample is deliberately NOT random:
1. current non-English translations flagged by automatic QC;
2. curated headline patterns that exposed semantic/entity/idiom failures;
3. longest remaining French/Chinese headlines until the sample reaches 20.

No production translations are changed by this benchmark.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
from pathlib import Path
from typing import Any

import requests
from huggingface_hub import HfApi
from supabase import Client, create_client

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "review" / "translations" / "challenge" / "latest.json"

SOURCE_PROFILE = "qwen3_1_7b_en_normalization_v2"
QWEN4_REPO = "Qwen/Qwen3-4B-GGUF"
QWEN4_QUANT = "Q4_K_M"
SERVER_URL = os.environ.get(
    "LLAMA_SERVER_URL",
    "http://127.0.0.1:8080/v1/chat/completions",
)
TARGET_N = 20
SEED = 20260812

CURATED_PATTERNS = [
    # French idioms / semantic traps observed in the pilot.
    "grand ménage",
    "filigrane",
    "Former les élèves",
    "ne cotise pas",
    "refuser d'être augmenté",
    "bouscule les entreprises",
    # Chinese entities, places, metaphors and policy phrases observed in pilot.
    "在常举行",
    "北京大学临床科学家",
    "之江实验室王坚院士",
    "千里马",
    "向善",
    "海外救场",
    "海岱舆情大模型",
    "人工智能局",
]


class ChallengeError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise ChallengeError(f"{name} is missing.")
    return value


def load_latest_profile_rows(client: Client) -> list[dict[str, Any]]:
    response = (
        client.table("article_translations")
        .select(
            "article_id,source_language_iso2,original_headline,"
            "translated_headline,requires_review,review_reason,"
            "detection_confidence,created_at,translation_run_id"
        )
        .eq("translation_profile", SOURCE_PROFILE)
        .in_("source_language_iso2", ["fr", "zh"])
        .eq("status", "translated")
        .order("created_at", desc=True)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    if not rows:
        raise ChallengeError(
            f"No translated rows found for profile {SOURCE_PROFILE}."
        )

    # Upserts usually leave one row per article/profile/hash, but this ensures
    # deterministic newest-row behavior if history contains duplicates.
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
        if pattern.casefold() in headline.casefold():
            score += 40

    if re.search(r"\d", headline):
        score += 5
    if any(mark in headline for mark in ['"', "“", "”", "«", "»", "：", "—", "丨"]):
        score += 4
    if len(headline) >= 70:
        score += 3
    if row.get("source_language_iso2") == "zh":
        score += 2

    return score, len(headline)


def select_challenge(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rng = random.Random(SEED)

    # Stable random tie-breaker prevents source-order artifacts.
    decorated = []
    for row in rows:
        score, length = difficulty_score(row)
        decorated.append((score, length, rng.random(), row))

    decorated.sort(
        key=lambda item: (-item[0], -item[1], item[2], str(item[3]["article_id"]))
    )

    selected = [item[3] for item in decorated[:TARGET_N]]

    # Try to keep both pilot languages represented.
    languages = {row["source_language_iso2"] for row in selected}
    for required_language in {"fr", "zh"} - languages:
        replacement = next(
            (
                row
                for _, _, _, row in decorated
                if row["source_language_iso2"] == required_language
                and row not in selected
            ),
            None,
        )
        if replacement is not None:
            selected[-1] = replacement

    return selected


def source_language_name(code: str) -> str:
    return {"fr": "French", "zh": "Chinese"}.get(code, code)


def translate_with_qwen4(headline: str, source_language: str) -> str:
    prompt = (
        "/no_think\n"
        f"Translate this {source_language_name(source_language)} news headline "
        "into natural, precise English. Preserve the specific event meaning, "
        "named entities, place names, organizations, people, numbers, "
        "negation, modality, and comparisons. Translate idioms by meaning, "
        "not word-for-word. Do not summarize, explain, infer, or add facts. "
        "Return only the English headline.\n\n"
        f"Headline: {headline}"
    )

    payload = {
        "model": f"{QWEN4_REPO}:{QWEN4_QUANT}",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a precise multilingual news-headline translator. "
                    "Output only the English translation."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "top_p": 0.8,
        "max_tokens": 160,
        "stream": False,
    }

    response = requests.post(SERVER_URL, json=payload, timeout=180)
    response.raise_for_status()
    data = response.json()

    try:
        text = str(data["choices"][0]["message"]["content"]).strip()
    except Exception as exc:
        raise ChallengeError(
            f"Unexpected llama.cpp response shape: {data}"
        ) from exc

    # Defensive cleanup if a model/server still emits an empty think block.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = text.strip('"').strip()

    if not text:
        raise ChallengeError("Qwen3-4B returned an empty translation.")

    return text


def main() -> int:
    client: Client = create_client(
        required_env("SUPABASE_URL"),
        required_env("SUPABASE_SECRET_KEY"),
    )

    rows = load_latest_profile_rows(client)
    challenge = select_challenge(rows)
    if not challenge:
        raise ChallengeError("No challenge rows selected.")

    qwen4_revision = HfApi().model_info(QWEN4_REPO).sha or "unknown"
    llama_cpp_commit = os.environ.get("LLAMA_CPP_COMMIT", "unknown")

    rng = random.Random(SEED + 1)
    items = []

    for index, row in enumerate(challenge, start=1):
        qwen1 = str(row["translated_headline"]).strip()
        qwen4 = translate_with_qwen4(
            str(row["original_headline"]),
            str(row["source_language_iso2"]),
        )

        qwen4_is_a = rng.choice([True, False])

        items.append(
            {
                "challenge_id": f"challenge_{index:02d}",
                "article_id": row["article_id"],
                "source_language": row["source_language_iso2"],
                "original_headline": row["original_headline"],
                "candidate_a": qwen4 if qwen4_is_a else qwen1,
                "candidate_b": qwen1 if qwen4_is_a else qwen4,
                # Hidden from the UI; retained for later analysis.
                "candidate_a_model": (
                    "qwen3-4b-q4_k_m" if qwen4_is_a else "qwen3-1.7b"
                ),
                "candidate_b_model": (
                    "qwen3-1.7b" if qwen4_is_a else "qwen3-4b-q4_k_m"
                ),
                "qwen3_1_7b_translation": qwen1,
                "qwen3_4b_translation": qwen4,
                "previous_qc_flag": bool(row.get("requires_review")),
                "previous_qc_reason": row.get("review_reason") or "",
            }
        )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {
                "meta": {
                    "stage": "7B.2B-0d",
                    "purpose": (
                        "blind difficult-case benchmark of Qwen3-1.7B vs "
                        "Qwen3-4B Q4_K_M"
                    ),
                    "source_profile": SOURCE_PROFILE,
                    "qwen4_repo": QWEN4_REPO,
                    "qwen4_quant": QWEN4_QUANT,
                    "qwen4_revision": qwen4_revision,
                    "llama_cpp_commit": llama_cpp_commit,
                    "sample_size": len(items),
                    "selection": (
                        "QC-flagged + curated semantic/entity/idiom traps + "
                        "long difficult headlines"
                    ),
                    "human_labels": [
                        "candidate_a_better",
                        "candidate_b_better",
                        "tie_both_accurate",
                        "both_inaccurate",
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

    print(f"Challenge items: {len(items)}")
    print(f"Qwen3-4B revision: {qwen4_revision}")
    print(f"llama.cpp commit: {llama_cpp_commit}")
    print(f"Output: {OUTPUT}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ChallengeError as exc:
        print(f"Challenge benchmark failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
