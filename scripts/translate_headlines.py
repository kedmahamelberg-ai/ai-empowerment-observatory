#!/usr/bin/env python3
"""Translate non-English Observatory headlines into English.

Production principle:
- Original headlines remain the evidentiary source in `articles`.
- English translations are stored separately in `article_translations`.
- English items are copied through unchanged.
- This pilot routes French and Chinese to dedicated Apache-2.0 OPUS-MT models.
- Other detected languages are retained but flagged unsupported for review.
"""

from __future__ import annotations

import gc
import hashlib
import importlib.metadata
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import torch
from huggingface_hub import HfApi
from lingua import LanguageDetectorBuilder
from supabase import Client, create_client
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = ROOT / "review" / "translations" / "latest.json"

TRANSLATION_PROFILE = "opus_en_normalization_v1.1"
PIPELINE_VERSION = "7B.2B-0a"

MODEL_BY_LANGUAGE = {
    "fr": "Helsinki-NLP/opus-mt-fr-en",
    "zh": "Helsinki-NLP/opus-mt-zh-en",
}

TARGET_LANGUAGE = "en"
MIN_DETECTION_CONFIDENCE = 0.55
BATCH_SIZE = 16


class TranslationError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise TranslationError(f"{name} is missing from workflow environment.")
    return value


def first_row(response: Any, context: str) -> dict[str, Any]:
    data = getattr(response, "data", None)
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict) and data:
        return data
    raise TranslationError(f"Supabase returned no row while {context}.")


def latest_collection(client: Client) -> dict[str, Any]:
    response = (
        client.table("collection_runs")
        .select("run_id,run_key,started_at,status")
        .in_("status", ["success", "partial"])
        .order("started_at", desc=True)
        .limit(1)
        .execute()
    )
    return first_row(response, "reading latest collection")


def load_articles(client: Client, run_id: str) -> list[dict[str, Any]]:
    obs_response = (
        client.table("article_observations")
        .select("article_id,search_language")
        .eq("run_id", run_id)
        .execute()
    )
    observations = getattr(obs_response, "data", None) or []
    if not observations:
        raise TranslationError("No observations found for latest collection.")

    search_languages: dict[str, set[str]] = defaultdict(set)
    for row in observations:
        aid = str(row["article_id"])
        if row.get("search_language"):
            search_languages[aid].add(str(row["search_language"]).lower())

    article_ids = sorted(search_languages)
    rows: list[dict[str, Any]] = []

    for start in range(0, len(article_ids), 150):
        batch = article_ids[start:start + 150]
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
        result.append(
            {
                **row,
                "headline": headline,
                "observed_search_languages": sorted(search_languages.get(aid, set())),
            }
        )

    if not result:
        raise TranslationError("No usable article headlines found.")
    return result


def build_detector():
    # All-language detection is deliberate here: Google News localization is a
    # useful hint, but it does not guarantee that every returned headline uses
    # the localization language.
    return LanguageDetectorBuilder.from_all_languages().build()


def detect_language(detector, text: str) -> tuple[str, float]:
    values = detector.compute_language_confidence_values(text)
    if not values:
        return "und", 0.0

    best = values[0]
    iso = best.language.iso_code_639_1
    if iso is None:
        return "und", float(best.value)

    return iso.name.lower(), float(best.value)


def normalize_search_language(value: str) -> str:
    value = str(value or "").strip().lower()
    if value.startswith("zh"):
        return "zh"
    if value.startswith("fr"):
        return "fr"
    if value.startswith("en"):
        return "en"
    return value.split("-", 1)[0] if value else ""


def contains_han_script(text: str) -> bool:
    # CJK Unified Ideographs + Extension A. Headlines with Han script should
    # not be allowed to drift to unrelated Latin-script language labels.
    return any(
        "\u3400" <= char <= "\u4dbf" or "\u4e00" <= char <= "\u9fff"
        for char in text
    )


def resolve_source_language(
    detector,
    text: str,
    observed_search_languages: list[str],
) -> dict[str, Any]:
    detected_language, confidence = detect_language(detector, text)

    hints = {
        normalize_search_language(value)
        for value in observed_search_languages
        if normalize_search_language(value) in {"en", "fr", "zh"}
    }
    unanimous_hint = next(iter(hints)) if len(hints) == 1 else None

    # Script is a stronger signal than statistical language detection for
    # Chinese headlines.
    if contains_han_script(text):
        return {
            "language": "zh",
            "confidence": confidence,
            "raw_detected_language": detected_language,
            "method": "han_script+lingua",
            "requires_review": False,
            "review_reason": "",
        }

    # Strong agreement between Lingua and the Google News search-language
    # context: accept even when the numeric confidence is modest. Headlines
    # are short, and Lingua's confidence naturally drops on short strings.
    if (
        detected_language in {"en", "fr", "zh"}
        and unanimous_hint == detected_language
    ):
        return {
            "language": detected_language,
            "confidence": confidence,
            "raw_detected_language": detected_language,
            "method": "lingua+search_language",
            "requires_review": False,
            "review_reason": "",
        }

    # A supported high-confidence Lingua result can stand on its own.
    if (
        detected_language in {"en", "fr", "zh"}
        and confidence >= 0.65
    ):
        return {
            "language": detected_language,
            "confidence": confidence,
            "raw_detected_language": detected_language,
            "method": "lingua",
            "requires_review": False,
            "review_reason": "",
        }

    # When Lingua is uncertain and the article was observed only in one
    # supported search-language context, use that context as a prior. Keep the
    # item reviewable so this override remains auditable.
    if unanimous_hint and confidence < 0.65:
        return {
            "language": unanimous_hint,
            "confidence": confidence,
            "raw_detected_language": detected_language,
            "method": "search_language_override",
            "requires_review": True,
            "review_reason": (
                f"low-confidence Lingua result '{detected_language}' "
                f"overridden by search-language context '{unanimous_hint}'"
            ),
        }

    # Otherwise keep Lingua's result but flag uncertainty. Unsupported labels
    # are preserved instead of being silently translated through the wrong
    # language model.
    requires_review = (
        confidence < MIN_DETECTION_CONFIDENCE
        or detected_language not in {"en", "fr", "zh"}
    )

    reasons = []
    if confidence < MIN_DETECTION_CONFIDENCE:
        reasons.append("low language-detection confidence")
    if detected_language not in {"en", "fr", "zh"}:
        reasons.append(
            f"language '{detected_language}' not routed in pilot translation profile"
        )

    return {
        "language": detected_language,
        "confidence": confidence,
        "raw_detected_language": detected_language,
        "method": "lingua",
        "requires_review": requires_review,
        "review_reason": "; ".join(reasons),
    }


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def register_model_version(
    client: Client,
    model_name: str,
    revision: str,
) -> str:
    response = (
        client.table("model_versions")
        .upsert(
            {
                "provider": "huggingface",
                "model_name": model_name,
                "model_revision": revision,
                "task": "headline_translation_to_english",
                "language_scope": "language-specific",
                "notes": (
                    "Apache-2.0 Helsinki-NLP OPUS-MT model used for "
                    "English headline normalization."
                ),
            },
            on_conflict="provider,model_name,model_revision,task",
        )
        .select("model_version_id")
        .execute()
    )
    return str(
        first_row(response, f"registering translation model {model_name}")[
            "model_version_id"
        ]
    )


def start_run(
    client: Client,
    collection_run_id: str,
    detector_version: str,
) -> tuple[str, str]:
    now = utc_now()
    run_key = now.strftime("translate_%Y%m%dT%H%M%SZ")
    response = (
        client.table("translation_runs")
        .insert(
            {
                "collection_run_id": collection_run_id,
                "run_key": run_key,
                "started_at": iso_z(now),
                "status": "running",
                "translation_profile": TRANSLATION_PROFILE,
                "detector_name": "lingua-language-detector",
                "detector_version": detector_version,
                "pipeline_version": PIPELINE_VERSION,
            }
        )
        .select("translation_run_id")
        .execute()
    )
    translation_run_id = str(
        first_row(response, "starting translation run")["translation_run_id"]
    )
    return translation_run_id, run_key


def finish_run(
    client: Client,
    *,
    translation_run_id: str,
    status: str,
    article_count: int,
    passthrough_count: int,
    translated_count: int,
    unsupported_count: int,
    failed_count: int,
    review_required_count: int,
) -> None:
    (
        client.table("translation_runs")
        .update(
            {
                "completed_at": iso_z(utc_now()),
                "status": status,
                "article_count": article_count,
                "passthrough_count": passthrough_count,
                "translated_count": translated_count,
                "unsupported_count": unsupported_count,
                "failed_count": failed_count,
                "review_required_count": review_required_count,
            }
        )
        .eq("translation_run_id", translation_run_id)
        .execute()
    )


def translate_batch(
    model_name: str,
    texts: list[str],
) -> tuple[list[str], str, str]:
    api = HfApi()
    revision = api.model_info(model_name).sha or "unknown"

    tokenizer = AutoTokenizer.from_pretrained(model_name, revision=revision)
    model = AutoModelForSeq2SeqLM.from_pretrained(
        model_name,
        revision=revision,
    )
    model.eval()

    outputs: list[str] = []

    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start:start + BATCH_SIZE]
        encoded = tokenizer(
            batch,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=192,
        )

        with torch.inference_mode():
            generated = model.generate(
                **encoded,
                num_beams=4,
                max_new_tokens=128,
                early_stopping=True,
            )

        outputs.extend(
            text.strip()
            for text in tokenizer.batch_decode(
                generated,
                skip_special_tokens=True,
            )
        )

    del model
    del tokenizer
    gc.collect()

    return outputs, revision, model_name


def upsert_translation(
    client: Client,
    payload: dict[str, Any],
) -> None:
    (
        client.table("article_translations")
        .upsert(
            payload,
            on_conflict="article_id,translation_profile,original_text_hash",
        )
        .execute()
    )


def main() -> int:
    client: Client = create_client(
        required_env("SUPABASE_URL"),
        required_env("SUPABASE_SECRET_KEY"),
    )

    collection = latest_collection(client)
    articles = load_articles(client, str(collection["run_id"]))

    detector_version = importlib.metadata.version("lingua-language-detector")
    detector = build_detector()

    translation_run_id, run_key = start_run(
        client,
        str(collection["run_id"]),
        detector_version,
    )

    detected: list[dict[str, Any]] = []
    by_language: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for article in articles:
        resolution = resolve_source_language(
            detector,
            article["headline"],
            article["observed_search_languages"],
        )
        language = resolution["language"]
        item = {
            **article,
            "source_language_iso2": language,
            "detection_confidence": round(float(resolution["confidence"]), 4),
            "raw_detected_language": resolution["raw_detected_language"],
            "detection_method": resolution["method"],
            "language_requires_review": bool(resolution["requires_review"]),
            "language_review_reason": resolution["review_reason"],
        }
        detected.append(item)
        by_language[language].append(item)

    review_rows: list[dict[str, Any]] = []
    counts = {
        "passthrough": 0,
        "translated": 0,
        "unsupported": 0,
        "failed": 0,
        "review": 0,
    }

    try:
        # English passthrough first.
        for item in by_language.get("en", []):
            language_review = item["language_requires_review"]
            payload = {
                "article_id": item["article_id"],
                "translation_run_id": translation_run_id,
                "model_version_id": None,
                "source_language_iso2": "en",
                "detection_confidence": item["detection_confidence"],
                "detected_by": item["detection_method"],
                "observed_search_languages": item["observed_search_languages"],
                "target_language_iso2": TARGET_LANGUAGE,
                "translation_profile": TRANSLATION_PROFILE,
                "original_headline": item["headline"],
                "translated_headline": item["headline"],
                "original_text_hash": text_hash(item["headline"]),
                "status": "passthrough",
                "requires_review": language_review,
                "review_reason": (
                    item["language_review_reason"] or None
                ),
                "updated_at": iso_z(utc_now()),
            }
            upsert_translation(client, payload)
            counts["passthrough"] += 1
            if language_review:
                counts["review"] += 1

        # Translate supported non-English languages one model at a time.
        for language, model_name in MODEL_BY_LANGUAGE.items():
            items = by_language.get(language, [])
            if not items:
                continue

            try:
                translated, revision, _ = translate_batch(
                    model_name,
                    [item["headline"] for item in items],
                )
                model_version_id = register_model_version(
                    client,
                    model_name,
                    revision,
                )

                for item, english in zip(items, translated):
                    empty_translation = not english.strip()
                    requires_review = (
                        item["language_requires_review"]
                        or empty_translation
                    )

                    reasons = []
                    if item["language_review_reason"]:
                        reasons.append(item["language_review_reason"])
                    if empty_translation:
                        reasons.append("empty translation")

                    status = "failed" if empty_translation else "translated"

                    payload = {
                        "article_id": item["article_id"],
                        "translation_run_id": translation_run_id,
                        "model_version_id": model_version_id,
                        "source_language_iso2": language,
                        "detection_confidence": item["detection_confidence"],
                        "detected_by": item["detection_method"],
                        "observed_search_languages": item["observed_search_languages"],
                        "target_language_iso2": TARGET_LANGUAGE,
                        "translation_profile": TRANSLATION_PROFILE,
                        "original_headline": item["headline"],
                        "translated_headline": english.strip() or item["headline"],
                        "original_text_hash": text_hash(item["headline"]),
                        "status": status,
                        "requires_review": requires_review,
                        "review_reason": "; ".join(reasons) or None,
                        "updated_at": iso_z(utc_now()),
                    }
                    upsert_translation(client, payload)

                    counts[status] += 1
                    if requires_review:
                        counts["review"] += 1

                    review_rows.append(
                        {
                            "article_id": item["article_id"],
                            "publisher": item["publisher"],
                            "source_language": language,
                            "detection_confidence": item["detection_confidence"],
                            "raw_detected_language": item["raw_detected_language"],
                            "detection_method": item["detection_method"],
                            "observed_search_languages": item["observed_search_languages"],
                            "original_headline": item["headline"],
                            "english_headline": english.strip() or item["headline"],
                            "model_name": model_name,
                            "model_revision": revision,
                            "status": status,
                            "requires_review": requires_review,
                            "review_reason": "; ".join(reasons),
                            "url": item.get("canonical_url"),
                        }
                    )

            except Exception as exc:
                print(
                    f"Translation failure for language {language}: {exc}",
                    file=sys.stderr,
                )
                for item in items:
                    payload = {
                        "article_id": item["article_id"],
                        "translation_run_id": translation_run_id,
                        "model_version_id": None,
                        "source_language_iso2": language,
                        "detection_confidence": item["detection_confidence"],
                        "detected_by": item["detection_method"],
                        "observed_search_languages": item["observed_search_languages"],
                        "target_language_iso2": TARGET_LANGUAGE,
                        "translation_profile": TRANSLATION_PROFILE,
                        "original_headline": item["headline"],
                        "translated_headline": item["headline"],
                        "original_text_hash": text_hash(item["headline"]),
                        "status": "failed",
                        "requires_review": True,
                        "review_reason": f"translation model failure: {type(exc).__name__}",
                        "updated_at": iso_z(utc_now()),
                    }
                    upsert_translation(client, payload)
                    counts["failed"] += 1
                    counts["review"] += 1
                    review_rows.append(
                        {
                            "article_id": item["article_id"],
                            "publisher": item["publisher"],
                            "source_language": language,
                            "detection_confidence": item["detection_confidence"],
                            "raw_detected_language": item["raw_detected_language"],
                            "detection_method": item["detection_method"],
                            "observed_search_languages": item["observed_search_languages"],
                            "original_headline": item["headline"],
                            "english_headline": item["headline"],
                            "model_name": model_name,
                            "model_revision": None,
                            "status": "failed",
                            "requires_review": True,
                            "review_reason": (
                                f"translation model failure: {type(exc).__name__}"
                            ),
                            "url": item.get("canonical_url"),
                        }
                    )

        # Unsupported languages are preserved, never silently mistranslated.
        for language, items in by_language.items():
            if language in {"en", *MODEL_BY_LANGUAGE.keys()}:
                continue

            for item in items:
                payload = {
                    "article_id": item["article_id"],
                    "translation_run_id": translation_run_id,
                    "model_version_id": None,
                    "source_language_iso2": (
                        language if len(language) == 2 else "un"
                    ),
                    "detection_confidence": item["detection_confidence"],
                    "detected_by": item["detection_method"],
                    "observed_search_languages": item["observed_search_languages"],
                    "target_language_iso2": TARGET_LANGUAGE,
                    "translation_profile": TRANSLATION_PROFILE,
                    "original_headline": item["headline"],
                    "translated_headline": item["headline"],
                    "original_text_hash": text_hash(item["headline"]),
                    "status": "unsupported",
                    "requires_review": True,
                    "review_reason": (
                        item["language_review_reason"]
                        or f"language '{language}' not routed in pilot translation profile"
                    ),
                    "updated_at": iso_z(utc_now()),
                }
                upsert_translation(client, payload)
                counts["unsupported"] += 1
                counts["review"] += 1
                review_rows.append(
                    {
                        "article_id": item["article_id"],
                        "publisher": item["publisher"],
                        "source_language": language,
                        "detection_confidence": item["detection_confidence"],
                        "original_headline": item["headline"],
                        "english_headline": item["headline"],
                        "model_name": None,
                        "model_revision": None,
                        "status": "unsupported",
                        "requires_review": True,
                        "review_reason": (
                            item["language_review_reason"]
                            or f"language '{language}' not routed in pilot translation profile"
                        ),
                        "url": item.get("canonical_url"),
                    }
                )

        status = "success" if counts["failed"] == 0 else "partial"
        finish_run(
            client,
            translation_run_id=translation_run_id,
            status=status,
            article_count=len(articles),
            passthrough_count=counts["passthrough"],
            translated_count=counts["translated"],
            unsupported_count=counts["unsupported"],
            failed_count=counts["failed"],
            review_required_count=counts["review"],
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
                        "detector": (
                            "lingua-language-detector + search-language context "
                            "+ Han-script override"
                        ),
                        "detector_version": detector_version,
                        "article_count": len(articles),
                        **counts,
                        "principle": (
                            "Original evidence is retained. English is an "
                            "additional normalized representation."
                        ),
                    },
                    "translations": sorted(
                        review_rows,
                        key=lambda row: (
                            row["requires_review"] is False,
                            row["source_language"],
                            row["publisher"],
                            row["original_headline"],
                        ),
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        print(f"Translation run: {run_key}")
        print(f"Articles: {len(articles)}")
        print(f"English passthrough: {counts['passthrough']}")
        print(f"Translated: {counts['translated']}")
        print(f"Unsupported: {counts['unsupported']}")
        print(f"Failed: {counts['failed']}")
        print(f"Review required: {counts['review']}")
        print(f"Review file: {REVIEW_PATH}")
        return 0

    except Exception:
        finish_run(
            client,
            translation_run_id=translation_run_id,
            status="failed",
            article_count=len(articles),
            passthrough_count=counts["passthrough"],
            translated_count=counts["translated"],
            unsupported_count=counts["unsupported"],
            failed_count=counts["failed"],
            review_required_count=counts["review"],
        )
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TranslationError as exc:
        print(f"Translation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
