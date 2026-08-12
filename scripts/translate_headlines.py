#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import torch
from huggingface_hub import HfApi
from lingua import LanguageDetectorBuilder
from supabase import Client, create_client
from transformers import AutoModelForCausalLM, AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = ROOT / "review" / "translations" / "latest.json"

MODEL_NAME = "Qwen/Qwen3-1.7B"
TRANSLATION_PROFILE = "qwen3_1_7b_en_normalization_v2"
PIPELINE_VERSION = "7B.2B-0c"
TARGET_LANGUAGE = "en"
STRONG_DETECTION_CONFIDENCE = 0.65
OUTPUT_ENGLISH_CONFIDENCE = 0.55


class TranslationError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def iso_z(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def required_env(name):
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise TranslationError(f"{name} is missing.")
    return value


def first_row(response, context):
    data = getattr(response, "data", None)
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict) and data:
        return data
    raise TranslationError(f"No Supabase row while {context}.")


def latest_collection(client):
    response = (
        client.table("collection_runs")
        .select("run_id,run_key,started_at,status")
        .in_("status", ["success", "partial"])
        .order("started_at", desc=True)
        .limit(1)
        .execute()
    )
    return first_row(response, "reading latest collection")


def load_articles(client, run_id):
    obs = (
        client.table("article_observations")
        .select("article_id,search_language")
        .eq("run_id", run_id)
        .execute()
    )
    observations = getattr(obs, "data", None) or []
    if not observations:
        raise TranslationError("No observations found.")

    search_languages = defaultdict(set)
    for row in observations:
        if row.get("search_language"):
            search_languages[str(row["article_id"])].add(
                str(row["search_language"]).lower()
            )

    article_ids = sorted(search_languages)
    rows = []
    for start in range(0, len(article_ids), 150):
        batch = article_ids[start:start+150]
        response = (
            client.table("articles")
            .select(
                "article_id,headline,publisher,canonical_url,"
                "published_at,first_seen_at"
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
        result.append({
            **row,
            "headline": headline,
            "observed_search_languages": sorted(search_languages.get(aid, set())),
        })
    return result


def build_detector():
    return LanguageDetectorBuilder.from_all_languages().build()


def detect_language(detector, text):
    values = detector.compute_language_confidence_values(text)
    if not values:
        return "und", 0.0
    best = values[0]
    iso = best.language.iso_code_639_1
    if iso is None:
        return "und", float(best.value)
    return iso.name.lower(), float(best.value)


def normalize_search_language(value):
    value = str(value or "").strip().lower()
    if value.startswith("zh"):
        return "zh"
    if value.startswith("fr"):
        return "fr"
    if value.startswith("en"):
        return "en"
    return value.split("-", 1)[0] if value else ""


def contains_han_script(text):
    return any(
        "\u3400" <= c <= "\u4dbf" or "\u4e00" <= c <= "\u9fff"
        for c in text
    )


def resolve_source_language(detector, text, observed_search_languages):
    detected, confidence = detect_language(detector, text)
    hints = {
        normalize_search_language(v)
        for v in observed_search_languages
        if normalize_search_language(v)
    }
    unanimous_hint = next(iter(hints)) if len(hints) == 1 else None

    if contains_han_script(text):
        return "zh", confidence, "han_script+lingua", False, ""

    if unanimous_hint and detected == unanimous_hint:
        return detected, confidence, "lingua+search_language", False, ""

    if confidence >= STRONG_DETECTION_CONFIDENCE:
        return detected, confidence, "lingua", False, ""

    if unanimous_hint:
        return (
            unanimous_hint,
            confidence,
            "search_language_override",
            True,
            f"low-confidence '{detected}' overridden by '{unanimous_hint}'",
        )

    return detected if len(detected) == 2 else "un", confidence, "lingua", True, "uncertain source language"


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def arabic_numbers(text):
    return re.findall(r"\d+(?:[.,]\d+)?", text)


def register_model_version(client, revision):
    response = (
        client.table("model_versions")
        .upsert(
            {
                "provider": "huggingface",
                "model_name": MODEL_NAME,
                "model_revision": revision,
                "task": "headline_translation_to_english",
                "language_scope": "multilingual",
                "notes": "Selected after blind human benchmark against OPUS-MT.",
            },
            on_conflict="provider,model_name,model_revision,task",
        )
        .select("model_version_id")
        .execute()
    )
    return str(first_row(response, "registering model")["model_version_id"])


def start_run(client, collection_run_id, detector_version):
    now = utc_now()
    run_key = now.strftime("translate_qwen_%Y%m%dT%H%M%SZ")
    response = (
        client.table("translation_runs")
        .insert(
            {
                "collection_run_id": collection_run_id,
                "run_key": run_key,
                "started_at": iso_z(now),
                "status": "running",
                "translation_profile": TRANSLATION_PROFILE,
                "detector_name": "lingua + search-language context + Han-script override",
                "detector_version": detector_version,
                "pipeline_version": PIPELINE_VERSION,
            }
        )
        .select("translation_run_id")
        .execute()
    )
    return str(first_row(response, "starting run")["translation_run_id"]), run_key


def finish_run(client, translation_run_id, status, article_count, passthrough, translated, failed, review):
    (
        client.table("translation_runs")
        .update(
            {
                "completed_at": iso_z(utc_now()),
                "status": status,
                "article_count": article_count,
                "passthrough_count": passthrough,
                "translated_count": translated,
                "unsupported_count": 0,
                "failed_count": failed,
                "review_required_count": review,
            }
        )
        .eq("translation_run_id", translation_run_id)
        .execute()
    )


def upsert_translation(client, payload):
    (
        client.table("article_translations")
        .upsert(
            payload,
            on_conflict="article_id,translation_profile,original_text_hash",
        )
        .execute()
    )


def translate_one(tokenizer, model, headline, source_language):
    language_label = {"fr": "French", "zh": "Chinese"}.get(
        source_language, f"language code {source_language}"
    )
    prompt = (
        f"Translate this {language_label} news headline into natural, precise English. "
        "Preserve the specific event meaning, named entities, place names, organizations, "
        "people, numbers, negation, modality, and comparisons. Translate idioms by meaning, "
        "not word-for-word. Do not summarize, explain, infer, or add facts. "
        f"Return only the English headline.\n\nHeadline: {headline}"
    )
    text = tokenizer.apply_chat_template(
        [{"role": "user", "content": prompt}],
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


def translation_qc(detector, source_language, original, translated):
    reasons = []
    if not translated.strip():
        return ["empty translation"]
    if source_language != "en" and translated.strip() == original.strip():
        reasons.append("translation identical to non-English source")
    if source_language == "zh" and contains_han_script(translated):
        reasons.append("Chinese characters remain in English normalization")

    out_lang, out_conf = detect_language(detector, translated)
    if out_lang != "en" or out_conf < OUTPUT_ENGLISH_CONFIDENCE:
        reasons.append(f"output English check uncertain: {out_lang} {out_conf:.3f}")

    original_nums = arabic_numbers(original)
    missing = [n for n in original_nums if n not in translated]
    if missing:
        reasons.append("numeric information not preserved: " + ", ".join(missing))
    return reasons


def main():
    client = create_client(
        required_env("SUPABASE_URL"),
        required_env("SUPABASE_SECRET_KEY"),
    )
    collection = latest_collection(client)
    articles = load_articles(client, str(collection["run_id"]))

    detector_version = importlib.metadata.version("lingua-language-detector")
    detector = build_detector()

    revision = HfApi().model_info(MODEL_NAME).sha or "unknown"
    model_version_id = register_model_version(client, revision)

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, revision=revision)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_NAME,
        revision=revision,
        torch_dtype="auto",
        low_cpu_mem_usage=True,
    )
    model.eval()

    translation_run_id, run_key = start_run(
        client, str(collection["run_id"]), detector_version
    )

    counts = {"passthrough": 0, "translated": 0, "failed": 0, "review": 0}
    review_rows = []

    try:
        for article in articles:
            source_language, confidence, method, lang_review, lang_reason = resolve_source_language(
                detector,
                article["headline"],
                article["observed_search_languages"],
            )
            reasons = [lang_reason] if lang_reason else []

            if source_language == "en":
                translated = article["headline"]
                status = "passthrough"
                used_model_version_id = None
                counts["passthrough"] += 1
            else:
                try:
                    translated = translate_one(
                        tokenizer, model, article["headline"], source_language
                    )
                    reasons.extend(
                        translation_qc(
                            detector,
                            source_language,
                            article["headline"],
                            translated,
                        )
                    )
                    status = "translated" if translated else "failed"
                    used_model_version_id = model_version_id
                    counts[status] += 1
                except Exception as exc:
                    translated = article["headline"]
                    status = "failed"
                    used_model_version_id = model_version_id
                    reasons.append(f"Qwen translation failure: {type(exc).__name__}")
                    counts["failed"] += 1

            requires_review = bool(reasons) or status == "failed"
            if requires_review:
                counts["review"] += 1

            upsert_translation(
                client,
                {
                    "article_id": article["article_id"],
                    "translation_run_id": translation_run_id,
                    "model_version_id": used_model_version_id,
                    "source_language_iso2": source_language if len(source_language) == 2 else "un",
                    "detection_confidence": round(float(confidence), 4),
                    "detected_by": method,
                    "observed_search_languages": article["observed_search_languages"],
                    "target_language_iso2": TARGET_LANGUAGE,
                    "translation_profile": TRANSLATION_PROFILE,
                    "original_headline": article["headline"],
                    "translated_headline": translated or article["headline"],
                    "original_text_hash": text_hash(article["headline"]),
                    "status": status,
                    "requires_review": requires_review,
                    "review_reason": "; ".join(reasons) or None,
                    "updated_at": iso_z(utc_now()),
                },
            )

            if source_language != "en" or requires_review:
                review_rows.append({
                    "article_id": article["article_id"],
                    "publisher": article["publisher"],
                    "source_language": source_language,
                    "detection_confidence": round(float(confidence), 4),
                    "original_headline": article["headline"],
                    "english_headline": translated or article["headline"],
                    "model_name": MODEL_NAME if source_language != "en" else None,
                    "model_revision": revision if source_language != "en" else None,
                    "status": status,
                    "requires_review": requires_review,
                    "review_reason": "; ".join(reasons),
                    "url": article.get("canonical_url"),
                })

        status = "success" if counts["failed"] == 0 else "partial"
        finish_run(
            client,
            translation_run_id,
            status,
            len(articles),
            counts["passthrough"],
            counts["translated"],
            counts["failed"],
            counts["review"],
        )

        REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
        REVIEW_PATH.write_text(
            json.dumps(
                {
                    "meta": {
                        "stage": PIPELINE_VERSION,
                        "status": status,
                        "translation_run_id": translation_run_id,
                        "translation_run_key": run_key,
                        "collection_run_key": collection["run_key"],
                        "translation_profile": TRANSLATION_PROFILE,
                        "model_name": MODEL_NAME,
                        "model_revision": revision,
                        "article_count": len(articles),
                        "passthrough": counts["passthrough"],
                        "translated": counts["translated"],
                        "unsupported": 0,
                        "failed": counts["failed"],
                        "review": counts["review"],
                        "principle": "Original evidence is retained. English is an additional normalized representation.",
                    },
                    "translations": sorted(
                        review_rows,
                        key=lambda r: (
                            r["requires_review"] is False,
                            r["source_language"],
                            r["publisher"],
                            r["original_headline"],
                        ),
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )

        print(f"Translation run: {run_key}")
        print(f"Articles: {len(articles)}")
        print(f"English passthrough: {counts['passthrough']}")
        print(f"Translated: {counts['translated']}")
        print(f"Failed: {counts['failed']}")
        print(f"Review required: {counts['review']}")
        return 0

    except Exception:
        finish_run(
            client,
            translation_run_id,
            "failed",
            len(articles),
            counts["passthrough"],
            counts["translated"],
            counts["failed"],
            counts["review"],
        )
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TranslationError as exc:
        print(f"Translation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
