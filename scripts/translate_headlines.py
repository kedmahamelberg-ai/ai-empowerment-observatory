#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import requests
from huggingface_hub import HfApi
from lingua import LanguageDetectorBuilder
from sentence_transformers import SentenceTransformer
from supabase import Client, create_client

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = ROOT / "review" / "translations" / "latest.json"

PROFILE = "validated_language_routing_v3"
PIPELINE_VERSION = "7B.2B-0f"
TARGET_LANGUAGE = "en"

HYMT_REPO = "tencent/Hy-MT2-1.8B-GGUF"
HYMT_QUANT = "Q4_K_M"
QWEN_REPO = "Qwen/Qwen3-4B-GGUF"
QWEN_QUANT = "Q4_K_M"

AUDIT_EMBEDDER = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
AUDIT_SIM_THRESHOLD = 0.86

LLAMA_SERVER_BIN = os.environ.get(
    "LLAMA_SERVER_BIN",
    "/tmp/llama.cpp/build/bin/llama-server",
)
SERVER_URL = "http://127.0.0.1:8080/v1/chat/completions"
HEALTH_URL = "http://127.0.0.1:8080/health"


class PipelineError(RuntimeError):
    pass


def now_utc():
    return datetime.now(timezone.utc)


def iso_z(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def required_env(name):
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise PipelineError(f"{name} is missing.")
    return value


def first_row(response, context):
    data = getattr(response, "data", None)
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict) and data:
        return data
    raise PipelineError(f"No Supabase row while {context}.")


def latest_collection(client: Client):
    response = (
        client.table("collection_runs")
        .select("run_id,run_key,started_at,status")
        .in_("status", ["success", "partial"])
        .order("started_at", desc=True)
        .limit(1)
        .execute()
    )
    return first_row(response, "reading latest collection")


def load_articles(client: Client, run_id: str):
    obs = (
        client.table("article_observations")
        .select("article_id,search_language")
        .eq("run_id", run_id)
        .execute()
    )
    observations = getattr(obs, "data", None) or []
    if not observations:
        raise PipelineError("No article observations found.")

    search_languages = defaultdict(set)
    for row in observations:
        if row.get("search_language"):
            search_languages[str(row["article_id"])].add(
                str(row["search_language"]).lower()
            )

    ids = sorted(search_languages)
    rows = []
    for start in range(0, len(ids), 150):
        response = (
            client.table("articles")
            .select(
                "article_id,headline,publisher,canonical_url,"
                "published_at,first_seen_at"
            )
            .in_("article_id", ids[start:start+150])
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
    vals = detector.compute_language_confidence_values(text)
    if not vals:
        return "und", 0.0
    best = vals[0]
    iso = best.language.iso_code_639_1
    return (iso.name.lower() if iso else "und", float(best.value))


def contains_han(text):
    return any(
        "\u3400" <= c <= "\u4dbf" or "\u4e00" <= c <= "\u9fff"
        for c in text
    )


def normalize_search_lang(value):
    value = str(value or "").lower()
    if value.startswith("zh"):
        return "zh"
    if value.startswith("fr"):
        return "fr"
    if value.startswith("en"):
        return "en"
    return value.split("-", 1)[0] if value else ""


def resolve_language(detector, headline, observed):
    detected, confidence = detect_language(detector, headline)
    hints = {normalize_search_lang(v) for v in observed if normalize_search_lang(v)}
    hint = next(iter(hints)) if len(hints) == 1 else None

    if contains_han(headline):
        return "zh", confidence, "han_script+lingua", False, ""
    if hint and detected == hint:
        return detected, confidence, "lingua+search_language", False, ""
    if confidence >= 0.65:
        return detected, confidence, "lingua", False, ""
    if hint:
        return (
            hint,
            confidence,
            "search_language_override",
            True,
            f"low-confidence '{detected}' overridden by '{hint}'",
        )
    return detected if len(detected) == 2 else "un", confidence, "lingua", True, "uncertain source language"


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def nums(text):
    return re.findall(r"\d+(?:[.,]\d+)?", text)


def register_model(client, repo, revision, task, notes):
    response = (
        client.table("model_versions")
        .upsert(
            {
                "provider": "huggingface",
                "model_name": repo,
                "model_revision": revision,
                "task": task,
                "language_scope": "multilingual",
                "notes": notes,
            },
            on_conflict="provider,model_name,model_revision,task",
        )
        .select("model_version_id")
        .execute()
    )
    return str(first_row(response, f"registering {repo}")["model_version_id"])


def start_run(client, collection_run_id, detector_version):
    started = now_utc()
    run_key = started.strftime("translate_validated_%Y%m%dT%H%M%SZ")
    response = (
        client.table("translation_runs")
        .insert({
            "collection_run_id": collection_run_id,
            "run_key": run_key,
            "started_at": iso_z(started),
            "status": "running",
            "translation_profile": PROFILE,
            "detector_name": "lingua + search-language context + Han-script override",
            "detector_version": detector_version,
            "pipeline_version": PIPELINE_VERSION,
        })
        .select("translation_run_id")
        .execute()
    )
    return str(first_row(response, "starting translation run")["translation_run_id"]), run_key


def finish_run(client, run_id, status, total, passthrough, translated, unsupported, failed, review):
    (
        client.table("translation_runs")
        .update({
            "completed_at": iso_z(now_utc()),
            "status": status,
            "article_count": total,
            "passthrough_count": passthrough,
            "translated_count": translated,
            "unsupported_count": unsupported,
            "failed_count": failed,
            "review_required_count": review,
        })
        .eq("translation_run_id", run_id)
        .execute()
    )


def wait_server(process, timeout=480):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise PipelineError("llama.cpp server exited before becoming healthy.")
        try:
            if requests.get(HEALTH_URL, timeout=3).ok:
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    raise PipelineError("llama.cpp server health timeout.")


def start_server(repo, quant, log_name):
    log_path = Path(f"/tmp/{log_name}.log")
    handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            LLAMA_SERVER_BIN,
            "-hf", f"{repo}:{quant}",
            "--host", "127.0.0.1",
            "--port", "8080",
            "-c", "2048",
            "-np", "1",
            "--jinja",
            "-ngl", "0",
        ],
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    try:
        wait_server(process)
    except Exception:
        handle.flush()
        try:
            print(log_path.read_text(encoding="utf-8")[-10000:], file=sys.stderr)
        except Exception:
            pass
        raise
    return process, handle


def stop_server(process, handle):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    if handle is not None:
        handle.close()
    time.sleep(3)


def chat(model, messages, temperature=0.2, top_p=0.8):
    response = requests.post(
        SERVER_URL,
        json={
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": 160,
            "stream": False,
        },
        timeout=180,
    )
    response.raise_for_status()
    data = response.json()
    text = str(data["choices"][0]["message"]["content"]).strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = text.strip('"').strip()
    if not text:
        raise PipelineError("Model returned empty translation.")
    return text


def qwen_translate(headline, source_lang):
    lang = {"fr": "French", "zh": "Chinese"}.get(source_lang, source_lang)
    prompt = (
        "/no_think\n"
        f"Translate this {lang} news headline into natural, precise English. "
        "Preserve specific event meaning, named entities, places, organizations, "
        "people, numbers, negation, modality and comparisons. Translate idioms "
        "by meaning, not word-for-word. Do not summarize or add facts. "
        f"Return only the English headline.\n\nHeadline: {headline}"
    )
    return chat(
        f"{QWEN_REPO}:{QWEN_QUANT}",
        [
            {"role": "system", "content": "You are a precise multilingual news-headline translator."},
            {"role": "user", "content": prompt},
        ],
    )


def hymt_translate(headline):
    prompt = (
        "Translate the following text into English. "
        "Note that you should only output the translated result without "
        "any additional explanation:\n"
        f"{headline}"
    )
    return chat(
        f"{HYMT_REPO}:{HYMT_QUANT}",
        [{"role": "user", "content": prompt}],
        temperature=0.7,
        top_p=0.6,
    )


def primary_qc(source_lang, original, english):
    reasons = []
    if not english.strip():
        reasons.append("empty translation")
    if source_lang != "en" and english.strip() == original.strip():
        reasons.append("translation identical to non-English source")
    if contains_han(english):
        reasons.append("Han characters remain in English normalization")
    missing = [n for n in nums(original) if n not in english]
    if missing:
        reasons.append("numeric information not preserved: " + ", ".join(missing))
    return reasons


def upsert_translation(client, payload):
    response = (
        client.table("article_translations")
        .upsert(
            payload,
            on_conflict="article_id,translation_profile,original_text_hash",
        )
        .select("translation_id")
        .execute()
    )
    return str(first_row(response, "upserting translation")["translation_id"])


def upsert_audit(client, translation_id, auditor_model_version_id, auditor_translation, agreement_score, status, reason):
    (
        client.table("translation_audits")
        .upsert(
            {
                "translation_id": translation_id,
                "auditor_model_version_id": auditor_model_version_id,
                "auditor_translation": auditor_translation,
                "agreement_score": round(float(agreement_score), 4),
                "audit_status": status,
                "audit_reason": reason or None,
                "updated_at": iso_z(now_utc()),
            },
            on_conflict="translation_id,auditor_model_version_id",
        )
        .execute()
    )


def main():
    client = create_client(
        required_env("SUPABASE_URL"),
        required_env("SUPABASE_SECRET_KEY"),
    )
    collection = latest_collection(client)
    articles = load_articles(client, str(collection["run_id"]))
    detector = build_detector()
    detector_version = importlib.metadata.version("lingua-language-detector")

    hymt_revision = HfApi().model_info(HYMT_REPO).sha or "unknown"
    qwen_revision = HfApi().model_info(QWEN_REPO).sha or "unknown"

    hymt_primary_version = register_model(
        client, HYMT_REPO, hymt_revision,
        "headline_translation_to_english",
        "Chinese primary translator selected after blind benchmark.",
    )
    qwen_primary_version = register_model(
        client, QWEN_REPO, qwen_revision,
        "headline_translation_to_english",
        "French primary translator selected after blind benchmark.",
    )
    qwen_audit_version = register_model(
        client, QWEN_REPO, qwen_revision,
        "headline_translation_audit",
        "Independent Chinese audit translation.",
    )

    translation_run_id, run_key = start_run(
        client, str(collection["run_id"]), detector_version
    )

    prepared = []
    for article in articles:
        lang, conf, method, lreview, lreason = resolve_language(
            detector, article["headline"], article["observed_search_languages"]
        )
        prepared.append({
            **article,
            "source_language": lang,
            "detection_confidence": conf,
            "detection_method": method,
            "language_review": lreview,
            "language_reason": lreason,
        })

    french = [x for x in prepared if x["source_language"] == "fr"]
    chinese = [x for x in prepared if x["source_language"] == "zh"]

    outputs = {}

    # Qwen: French primary + Chinese independent audit
    qp = qlog = None
    try:
        if french or chinese:
            qp, qlog = start_server(QWEN_REPO, QWEN_QUANT, "qwen-routing")
        for item in french:
            outputs[item["article_id"]] = {
                "primary": qwen_translate(item["headline"], "fr"),
                "primary_model_version": qwen_primary_version,
                "auditor": None,
                "audit_model_version": None,
            }
        for item in chinese:
            outputs.setdefault(item["article_id"], {})
            outputs[item["article_id"]]["auditor"] = qwen_translate(item["headline"], "zh")
            outputs[item["article_id"]]["audit_model_version"] = qwen_audit_version
    finally:
        stop_server(qp, qlog)

    # Hy-MT2: Chinese primary
    hp = hlog = None
    try:
        if chinese:
            hp, hlog = start_server(HYMT_REPO, HYMT_QUANT, "hymt-routing")
        for item in chinese:
            outputs.setdefault(item["article_id"], {})
            outputs[item["article_id"]]["primary"] = hymt_translate(item["headline"])
            outputs[item["article_id"]]["primary_model_version"] = hymt_primary_version
    finally:
        stop_server(hp, hlog)

    # English passthrough
    for item in prepared:
        if item["source_language"] == "en":
            outputs[item["article_id"]] = {
                "primary": item["headline"],
                "primary_model_version": None,
                "auditor": None,
                "audit_model_version": None,
            }

    embedder = SentenceTransformer(AUDIT_EMBEDDER)
    audit_texts = []
    for item in chinese:
        d = outputs[item["article_id"]]
        audit_texts.extend([d["primary"], d["auditor"]])

    embeddings = None
    if audit_texts:
        embeddings = np.asarray(
            embedder.encode(
                audit_texts,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            ),
            dtype=np.float32,
        )

    counts = {
        "passthrough": 0,
        "translated": 0,
        "unsupported": 0,
        "failed": 0,
        "review": 0,
        "audited": 0,
        "audit_disagreement": 0,
    }
    review_rows = []
    pair_i = 0

    try:
        for item in prepared:
            aid = item["article_id"]
            lang = item["source_language"]

            if lang not in {"en", "fr", "zh"}:
                primary = item["headline"]
                primary_model_version = None
                auditor = None
                audit_model_version = None
                status = "unsupported"
                counts["unsupported"] += 1
            else:
                d = outputs[aid]
                primary = d["primary"]
                primary_model_version = d["primary_model_version"]
                auditor = d.get("auditor")
                audit_model_version = d.get("audit_model_version")
                status = "passthrough" if lang == "en" else "translated"
                counts[status] += 1

            reasons = []
            if item["language_reason"]:
                reasons.append(item["language_reason"])
            if status == "unsupported":
                reasons.append(f"language '{lang}' not routed in validated pilot")
            else:
                reasons.extend(primary_qc(lang, item["headline"], primary))

            requires_review = bool(reasons)

            translation_id = upsert_translation(
                client,
                {
                    "article_id": aid,
                    "translation_run_id": translation_run_id,
                    "model_version_id": primary_model_version,
                    "source_language_iso2": lang if len(lang) == 2 else "un",
                    "detection_confidence": round(float(item["detection_confidence"]), 4),
                    "detected_by": item["detection_method"],
                    "observed_search_languages": item["observed_search_languages"],
                    "target_language_iso2": TARGET_LANGUAGE,
                    "translation_profile": PROFILE,
                    "original_headline": item["headline"],
                    "translated_headline": primary,
                    "original_text_hash": text_hash(item["headline"]),
                    "status": status,
                    "requires_review": requires_review,
                    "review_reason": "; ".join(reasons) or None,
                    "updated_at": iso_z(now_utc()),
                },
            )

            audit_score = None
            audit_status = None
            audit_reason = ""

            if lang == "zh" and auditor:
                counts["audited"] += 1
                v1 = embeddings[pair_i]
                v2 = embeddings[pair_i + 1]
                pair_i += 2
                audit_score = float(np.dot(v1, v2))

                audit_reasons = []
                if audit_score < AUDIT_SIM_THRESHOLD:
                    audit_reasons.append("cross-model semantic disagreement")
                if nums(primary) != nums(auditor):
                    audit_reasons.append("cross-model numeric disagreement")
                if contains_han(auditor):
                    audit_reasons.append("Han characters remain in audit translation")
                if auditor.strip() == item["headline"].strip():
                    audit_reasons.append("audit translation identical to Chinese source")

                if audit_reasons:
                    audit_status = "review"
                    audit_reason = "; ".join(audit_reasons)
                    counts["audit_disagreement"] += 1
                    requires_review = True
                    reasons.append("Chinese translation audit disagreement")
                else:
                    audit_status = "agree"

                upsert_audit(
                    client,
                    translation_id,
                    audit_model_version,
                    auditor,
                    audit_score,
                    audit_status,
                    audit_reason,
                )

                if requires_review:
                    (
                        client.table("article_translations")
                        .update({
                            "requires_review": True,
                            "review_reason": "; ".join(dict.fromkeys(reasons)),
                            "updated_at": iso_z(now_utc()),
                        })
                        .eq("translation_id", translation_id)
                        .execute()
                    )

            if requires_review:
                counts["review"] += 1

            if lang != "en" or requires_review:
                review_rows.append({
                    "article_id": aid,
                    "publisher": item["publisher"],
                    "source_language": lang,
                    "original_headline": item["headline"],
                    "english_headline": primary,
                    "primary_model": (
                        HYMT_REPO if lang == "zh"
                        else (QWEN_REPO if lang == "fr" else None)
                    ),
                    "auditor_translation": auditor,
                    "auditor_model": QWEN_REPO if lang == "zh" else None,
                    "audit_agreement_score": round(audit_score, 4) if audit_score is not None else None,
                    "audit_status": audit_status,
                    "audit_reason": audit_reason,
                    "status": status,
                    "requires_review": requires_review,
                    "review_reason": "; ".join(dict.fromkeys(reasons)),
                    "url": item.get("canonical_url"),
                })

        run_status = "success" if counts["failed"] == 0 else "partial"
        finish_run(
            client,
            translation_run_id,
            run_status,
            len(prepared),
            counts["passthrough"],
            counts["translated"],
            counts["unsupported"],
            counts["failed"],
            counts["review"],
        )

        REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
        REVIEW_PATH.write_text(
            json.dumps(
                {
                    "meta": {
                        "stage": PIPELINE_VERSION,
                        "status": run_status,
                        "translation_run_id": translation_run_id,
                        "translation_run_key": run_key,
                        "collection_run_key": collection["run_key"],
                        "translation_profile": PROFILE,
                        "article_count": len(prepared),
                        **counts,
                        "routing": {
                            "en": "passthrough",
                            "fr": f"{QWEN_REPO}:{QWEN_QUANT}",
                            "zh_primary": f"{HYMT_REPO}:{HYMT_QUANT}",
                            "zh_auditor": f"{QWEN_REPO}:{QWEN_QUANT}",
                        },
                        "principle": (
                            "Original evidence is retained. English is an additional "
                            "normalized representation. Chinese cross-model disagreement "
                            "is preserved as an audit signal."
                        ),
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
        print(f"Passthrough: {counts['passthrough']}")
        print(f"Translated: {counts['translated']}")
        print(f"Unsupported: {counts['unsupported']}")
        print(f"Chinese audited: {counts['audited']}")
        print(f"Chinese audit disagreement: {counts['audit_disagreement']}")
        print(f"Review required: {counts['review']}")
        return 0

    except Exception:
        finish_run(
            client,
            translation_run_id,
            "failed",
            len(prepared),
            counts["passthrough"],
            counts["translated"],
            counts["unsupported"],
            counts["failed"],
            counts["review"],
        )
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PipelineError as exc:
        print(f"Validated translation routing failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
