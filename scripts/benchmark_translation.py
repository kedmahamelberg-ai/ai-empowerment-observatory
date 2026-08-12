#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path
from typing import Any

import torch
from huggingface_hub import HfApi
from supabase import Client, create_client
from transformers import AutoModelForCausalLM, AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "review" / "translations" / "benchmark" / "latest.json"

QWEN_MODEL = "Qwen/Qwen3-1.7B"
SOURCE_PROFILE = "opus_en_normalization_v1.1"
SEED = 42
N_PER_LANGUAGE = 12


class BenchmarkError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise BenchmarkError(f"{name} is missing.")
    return value


def load_candidates(client: Client) -> list[dict[str, Any]]:
    response = (
        client.table("article_translations")
        .select(
            "article_id,source_language_iso2,original_headline,"
            "translated_headline,detection_confidence,status,created_at"
        )
        .eq("translation_profile", SOURCE_PROFILE)
        .in_("source_language_iso2", ["fr", "zh"])
        .eq("status", "translated")
        .order("created_at", desc=True)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    if not rows:
        raise BenchmarkError(
            f"No translated rows found for profile {SOURCE_PROFILE}."
        )

    latest_by_article: dict[str, dict[str, Any]] = {}
    for row in rows:
        aid = str(row["article_id"])
        if aid not in latest_by_article:
            latest_by_article[aid] = row
    return list(latest_by_article.values())


def sample_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rng = random.Random(SEED)
    selected: list[dict[str, Any]] = []
    for language in ["fr", "zh"]:
        pool = [r for r in rows if r["source_language_iso2"] == language]
        pool.sort(key=lambda r: r["article_id"])
        rng.shuffle(pool)
        selected.extend(pool[:N_PER_LANGUAGE])
    rng.shuffle(selected)
    return selected


def qwen_translate(tokenizer, model, headline: str, source_language: str) -> str:
    language_name = {"fr": "French", "zh": "Chinese"}.get(
        source_language, source_language
    )

    prompt = (
        f"Translate this {language_name} news headline into natural, precise "
        "English. Preserve named entities, numbers, negation, modality, and "
        "the specific event meaning. Translate idioms by meaning, not "
        "literally. Do not explain, summarize, or add facts. Return only the "
        f"English headline.\n\nHeadline: {headline}"
    )

    messages = [{"role": "user", "content": prompt}]
    text = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    inputs = tokenizer(text, return_tensors="pt")

    with torch.inference_mode():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=128,
            do_sample=False,
            repetition_penalty=1.05,
        )

    generated = output_ids[0][inputs.input_ids.shape[1]:]
    return tokenizer.decode(generated, skip_special_tokens=True).strip()


def main() -> int:
    client: Client = create_client(
        required_env("SUPABASE_URL"),
        required_env("SUPABASE_SECRET_KEY"),
    )

    candidates = load_candidates(client)
    sample = sample_rows(candidates)
    if not sample:
        raise BenchmarkError("Benchmark sample is empty.")

    revision = HfApi().model_info(QWEN_MODEL).sha or "unknown"

    tokenizer = AutoTokenizer.from_pretrained(QWEN_MODEL, revision=revision)
    model = AutoModelForCausalLM.from_pretrained(
        QWEN_MODEL,
        revision=revision,
        torch_dtype="auto",
        low_cpu_mem_usage=True,
    )
    model.eval()

    rng = random.Random(SEED + 1)
    items = []

    for index, row in enumerate(sample, start=1):
        qwen = qwen_translate(
            tokenizer,
            model,
            row["original_headline"],
            row["source_language_iso2"],
        )
        opus = str(row["translated_headline"]).strip()

        qwen_left = rng.choice([True, False])
        left = qwen if qwen_left else opus
        right = opus if qwen_left else qwen

        items.append(
            {
                "benchmark_id": f"translation_{index:02d}",
                "article_id": row["article_id"],
                "source_language": row["source_language_iso2"],
                "original_headline": row["original_headline"],
                "candidate_a": left,
                "candidate_b": right,
                "candidate_a_model": "qwen3-1.7b" if qwen_left else "opus-mt",
                "candidate_b_model": "opus-mt" if qwen_left else "qwen3-1.7b",
                "opus_translation": opus,
                "qwen_translation": qwen,
            }
        )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "stage": "7B.2B-0b",
            "purpose": "blind translation-quality benchmark",
            "source_profile": SOURCE_PROFILE,
            "qwen_model": QWEN_MODEL,
            "qwen_revision": revision,
            "sample_size": len(items),
            "sample_per_language": N_PER_LANGUAGE,
        },
        "items": items,
    }

    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Benchmark items: {len(items)}")
    print(f"Qwen model revision: {revision}")
    print(f"Output: {OUTPUT}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BenchmarkError as exc:
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
