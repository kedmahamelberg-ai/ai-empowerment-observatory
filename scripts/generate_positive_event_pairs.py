#!/usr/bin/env python3
"""Stage 7B.2E — model-agreed positive-enrichment sample.

The current human-gold datasets are strongly negative-heavy. This stage
deliberately finds likely SAME-EVENT pairs in the existing Observatory corpus.

Selection principle:
- MiniLM retrieves plausible candidate pairs.
- ModernBERT scores event identity.
- Qwen3-4B independently verifies SAME / NOT SAME / UNCLEAR.
- Human reviewers see NONE of those model predictions.

Primary sample:
  ModernBERT = SAME and Qwen = SAME.

Fallback if exact agreement is sparse:
  Qwen = SAME with ModernBERT near the decision boundary.

This is an enrichment sample, not a prevalence sample.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
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

from translation_policy import SUPPORTED_TRANSLATION_PROFILES, preferred_translation_rows

ROOT = Path(__file__).resolve().parents[1]

OUTPUT = (
    ROOT
    / "review"
    / "events"
    / "positive-enrichment"
    / "latest.json"
)

VALIDATION_FILES = [
    ROOT / "validation" / "event_pair_gold_v1.csv",
    ROOT / "validation" / "event_pair_hard_negatives_v1.csv",
]

EMBEDDING_MODEL = (
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)

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
MIN_MINILM_SIMILARITY = 0.55

MODERNBERT_SAME_THRESHOLD = 0.45
MODERNBERT_FALLBACK_THRESHOLD = 0.34

QWEN_PRESELECT_N = 75
TARGET_SAMPLE_N = 24

MAX_ARTICLE_APPEARANCES = 4

# We want a mix of obvious and harder positives.
TARGET_CROSS_LANGUAGE = 8
TARGET_LOWER_SIMILARITY = 6


class PositiveEnrichmentError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()

    if not value:
        raise PositiveEnrichmentError(
            f"{name} is missing."
        )

    return value


def canonical_pair_id(a: str, b: str) -> str:
    return "__".join(
        sorted(
            [
                str(a),
                str(b),
            ]
        )
    )


def previously_labelled_pair_ids() -> set[str]:
    result = set()

    for path in VALIDATION_FILES:
        if not path.exists():
            continue

        frame = pd.read_csv(path)

        if "pair_id" not in frame.columns:
            continue

        for value in (
            frame["pair_id"]
            .dropna()
            .astype(str)
        ):
            parts = value.split(
                "__",
                1,
            )

            if len(parts) == 2:
                result.add(
                    canonical_pair_id(
                        parts[0],
                        parts[1],
                    )
                )

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
        raise PositiveEnrichmentError(
            "No successful collection run found."
        )

    return data[0]


def load_articles(
    client: Client,
    run_id: str,
) -> list[dict[str, Any]]:
    obs_response = (
        client.table("article_observations")
        .select(
            "article_id,search_country_iso3,"
            "search_language,search_rank"
        )
        .eq("run_id", run_id)
        .execute()
    )

    observations = getattr(
        obs_response,
        "data",
        None,
    ) or []

    if not observations:
        raise PositiveEnrichmentError(
            "No observations in latest collection."
        )

    meta: dict[str, dict[str, Any]] = {}

    for row in observations:
        aid = str(
            row["article_id"]
        )

        item = meta.setdefault(
            aid,
            {
                "markets": set(),
                "languages": set(),
                "rank": 9999,
            },
        )

        if row.get(
            "search_country_iso3"
        ):
            item["markets"].add(
                str(
                    row["search_country_iso3"]
                )
            )

        if row.get(
            "search_language"
        ):
            item["languages"].add(
                str(
                    row["search_language"]
                ).lower()
            )

        if row.get(
            "search_rank"
        ) is not None:
            item["rank"] = min(
                item["rank"],
                int(
                    row["search_rank"]
                ),
            )

    ids = sorted(meta)
    rows = []

    for start in range(
        0,
        len(ids),
        150,
    ):
        batch = ids[
            start:start + 150
        ]

        response = (
            client.table("articles")
            .select(
                "article_id,headline,publisher,"
                "canonical_url,published_at,"
                "first_seen_at,source_metadata"
            )
            .in_("article_id", batch)
            .execute()
        )

        rows.extend(
            getattr(
                response,
                "data",
                None,
            ) or []
        )

    result = []

    for row in rows:
        headline = str(
            row.get("headline")
            or ""
        ).strip()

        if not headline:
            continue

        aid = str(
            row["article_id"]
        )

        metadata = row.get(
            "source_metadata"
        )

        if not isinstance(
            metadata,
            dict,
        ):
            metadata = {}

        snippet = ""

        for key in [
            "snippet",
            "description",
            "summary",
            "source_snippet",
        ]:
            value = metadata.get(key)

            if value and str(
                value
            ).strip():
                snippet = str(
                    value
                ).strip()
                break

        result.append(
            {
                "article_id": aid,
                "headline": headline,
                "publisher": str(
                    row.get(
                        "publisher"
                    )
                    or "Unknown source"
                ),
                "url": row.get(
                    "canonical_url"
                ),
                "published_at": row.get(
                    "published_at"
                ),
                "first_seen_at": row.get(
                    "first_seen_at"
                ),
                "snippet": snippet,
                "story_token": (
                    str(
                        metadata.get(
                            "story_token"
                        )
                    ).strip()
                    if metadata.get(
                        "story_token"
                    )
                    else None
                ),
                "markets": sorted(
                    meta[aid][
                        "markets"
                    ]
                ),
                "search_languages": sorted(
                    meta[aid][
                        "languages"
                    ]
                ),
                "rank": meta[aid][
                    "rank"
                ],
            }
        )

    return result


def load_translations(
    client: Client,
    article_ids: list[str],
) -> dict[str, dict[str, Any]]:
    rows = []

    for start in range(
        0,
        len(article_ids),
        150,
    ):
        batch = article_ids[
            start:start + 150
        ]

        response = (
            client.table(
                "article_translations"
            )
            .select(
                "article_id,source_language_iso2,"
                "translated_headline,"
                "requires_review,review_reason,"
                "translation_profile,created_at"
            )
            .in_("translation_profile", list(SUPPORTED_TRANSLATION_PROFILES))
            .in_(
                "article_id",
                batch,
            )
            .order(
                "created_at",
                desc=True,
            )
            .execute()
        )

        rows.extend(
            getattr(
                response,
                "data",
                None,
            ) or []
        )

    return preferred_translation_rows(rows)


def normalized_headline(
    article: dict[str, Any],
    translations: dict[str, dict[str, Any]],
) -> str:
    row = translations.get(
        article["article_id"]
    )

    if not row:
        return article[
            "headline"
        ]

    value = str(
        row.get(
            "translated_headline"
        )
        or ""
    ).strip()

    return (
        value
        or article["headline"]
    )


def language_of(
    article: dict[str, Any],
    translations: dict[str, dict[str, Any]],
) -> str:
    row = translations.get(
        article["article_id"]
    )

    if row and row.get(
        "source_language_iso2"
    ):
        return str(
            row[
                "source_language_iso2"
            ]
        )

    values = article.get(
        "search_languages"
    ) or []

    if not values:
        return "unknown"

    value = str(
        values[0]
    ).lower()

    if value.startswith("zh"):
        return "zh"
    if value.startswith("fr"):
        return "fr"
    if value.startswith("en"):
        return "en"

    return value.split(
        "-",
        1,
    )[0]


def timestamp(value: str | None) -> float:
    if not value:
        return 0.0

    try:
        return datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00",
            )
        ).timestamp()
    except Exception:
        return 0.0


def day_gap(
    a: dict[str, Any],
    b: dict[str, Any],
) -> float:
    ta = timestamp(
        a.get(
            "published_at"
        )
        or a.get(
            "first_seen_at"
        )
    )

    tb = timestamp(
        b.get(
            "published_at"
        )
        or b.get(
            "first_seen_at"
        )
    )

    if not ta or not tb:
        return 0.0

    return abs(
        ta - tb
    ) / 86400.0


def start_server() -> tuple[subprocess.Popen, Any]:
    log_path = Path(
        "/tmp/positive-enrichment-qwen.log"
    )

    handle = log_path.open(
        "w",
        encoding="utf-8",
    )

    process = subprocess.Popen(
        [
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
        ],
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

            raise PositiveEnrichmentError(
                "Qwen server exited during startup."
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

    raise PositiveEnrichmentError(
        "Qwen server health timeout."
    )


def stop_server(
    process: subprocess.Popen | None,
    handle: Any | None,
) -> None:
    if process is not None and process.poll() is None:
        process.terminate()

        try:
            process.wait(
                timeout=20
            )
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(
                timeout=10
            )

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
        raise PositiveEnrichmentError(
            f"No JSON in Qwen output: {text}"
        )

    return json.loads(
        match.group(0)
    )


def qwen_verify(
    a: dict[str, Any],
    b: dict[str, Any],
    english_a: str,
    english_b: str,
    gap: float,
    token_match: bool,
) -> dict[str, Any]:
    prompt = f"""
/no_think

Decide whether these two news records report the SAME SPECIFIC REAL-WORLD
EVENT.

Allowed labels:
- same_event
- not_same_event
- unclear

same_event = the same concrete occurrence, announcement, study result,
decision, launch, incident, meeting, speech, policy action, etc.

not_same_event = merely the same topic, organization, technology, law, trend,
debate or broad story area is NOT enough.

unclear = supplied evidence is genuinely insufficient.

ARTICLE A
Publisher: {a["publisher"]}
Original headline: {a["headline"]}
English normalization: {english_a}
Snippet: {a.get("snippet") or ""}

ARTICLE B
Publisher: {b["publisher"]}
Original headline: {b["headline"]}
English normalization: {english_b}
Snippet: {b.get("snippet") or ""}

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
                {
                    "role": "system",
                    "content": (
                        "You are a conservative news "
                        "event-identity verifier."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 220,
            "stream": False,
        },
        timeout=180,
    )

    response.raise_for_status()

    result = extract_json(
        str(
            response.json()[
                "choices"
            ][0][
                "message"
            ][
                "content"
            ]
        )
    )

    relationship = str(
        result.get(
            "relationship"
        )
        or ""
    ).strip()

    if relationship not in {
        "same_event",
        "not_same_event",
        "unclear",
    }:
        raise PositiveEnrichmentError(
            f"Unexpected Qwen label: {relationship}"
        )

    try:
        confidence = float(
            result.get(
                "confidence",
                0.0,
            )
        )
    except Exception:
        confidence = 0.0

    return {
        "relationship": relationship,
        "confidence": max(
            0.0,
            min(
                1.0,
                confidence,
            ),
        ),
        "reason": str(
            result.get(
                "reason"
            )
            or ""
        ),
    }


def modernbert_scores(
    tokenizer,
    model,
    pairs: list[dict[str, Any]],
) -> list[float]:
    result = []

    for start in range(
        0,
        len(pairs),
        32,
    ):
        batch = pairs[
            start:start + 32
        ]

        inputs = tokenizer(
            text=[
                item["english_a"]
                for item in batch
            ],
            text_pair=[
                item["english_b"]
                for item in batch
            ],
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=128,
        )

        with torch.inference_mode():
            logits = model(
                **inputs
            ).logits

            probs = F.softmax(
                logits,
                dim=-1,
            )[:, 1]

        result.extend(
            float(value)
            for value in probs.tolist()
        )

    return result


def selection_priority(
    item: dict[str, Any],
) -> tuple:
    """Higher-priority model-agreed positives first, but enrich hard positives."""

    exact_agreement = (
        item["modernbert_same_probability"]
        >= MODERNBERT_SAME_THRESHOLD
        and item["qwen_relationship"]
        == "same_event"
    )

    lower_similarity = (
        item["minilm_similarity"]
        < 0.78
    )

    cross_language = item[
        "cross_language"
    ]

    return (
        0 if exact_agreement else 1,
        0 if cross_language else 1,
        0 if lower_similarity else 1,
        -item[
            "qwen_confidence"
        ],
        -item[
            "modernbert_same_probability"
        ],
        item[
            "minilm_similarity"
        ],
        item[
            "pair_id"
        ],
    )


def select_sample(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates = sorted(
        candidates,
        key=selection_priority,
    )

    selected = []
    used_ids = set()
    appearances: dict[str, int] = defaultdict(int)

    def can_add(item):
        if item["pair_id"] in used_ids:
            return False

        return (
            appearances[
                item["article_a_id"]
            ]
            < MAX_ARTICLE_APPEARANCES
            and
            appearances[
                item["article_b_id"]
            ]
            < MAX_ARTICLE_APPEARANCES
        )

    def add(item):
        selected.append(item)
        used_ids.add(
            item["pair_id"]
        )
        appearances[
            item["article_a_id"]
        ] += 1
        appearances[
            item["article_b_id"]
        ] += 1

    # First guarantee cross-language representation.
    cross_count = 0

    for item in candidates:
        if (
            cross_count
            >= TARGET_CROSS_LANGUAGE
        ):
            break

        if (
            item["cross_language"]
            and can_add(item)
        ):
            add(item)
            cross_count += 1

    # Then guarantee some lower-similarity positives.
    lower_count = 0

    for item in candidates:
        if (
            lower_count
            >= TARGET_LOWER_SIMILARITY
        ):
            break

        if (
            item["minilm_similarity"]
            < 0.78
            and can_add(item)
        ):
            add(item)
            lower_count += 1

    # Fill remaining slots.
    for item in candidates:
        if len(selected) >= TARGET_SAMPLE_N:
            break

        if can_add(item):
            add(item)

    return selected


def main() -> int:
    client: Client = create_client(
        required_env(
            "SUPABASE_URL"
        ),
        required_env(
            "SUPABASE_SECRET_KEY"
        ),
    )

    excluded_pairs = previously_labelled_pair_ids()

    collection = latest_collection(
        client
    )

    articles = load_articles(
        client,
        str(
            collection[
                "run_id"
            ]
        ),
    )

    article_ids = [
        article[
            "article_id"
        ]
        for article in articles
    ]

    translations = load_translations(
        client,
        article_ids,
    )

    english = [
        normalized_headline(
            article,
            translations,
        )
        for article in articles
    ]

    embedder = SentenceTransformer(
        EMBEDDING_MODEL
    )

    embeddings = embedder.encode(
        english,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=True,
    )

    embeddings = np.asarray(
        embeddings,
        dtype=np.float32,
    )

    similarity = embeddings @ embeddings.T

    candidates = []

    for i in range(
        len(articles)
    ):
        for j in range(
            i + 1,
            len(articles),
        ):
            a = articles[i]
            b = articles[j]

            pair_id = canonical_pair_id(
                a["article_id"],
                b["article_id"],
            )

            if pair_id in excluded_pairs:
                continue

            gap = day_gap(
                a,
                b,
            )

            if gap > MAX_DAY_GAP:
                continue

            sim = float(
                similarity[i, j]
            )

            if (
                sim
                < MIN_MINILM_SIMILARITY
            ):
                continue

            lang_a = language_of(
                a,
                translations,
            )

            lang_b = language_of(
                b,
                translations,
            )

            candidates.append(
                {
                    "pair_id": pair_id,
                    "article_a_id": a[
                        "article_id"
                    ],
                    "article_b_id": b[
                        "article_id"
                    ],
                    "a": a,
                    "b": b,
                    "english_a": normalized_headline(
                        a,
                        translations,
                    ),
                    "english_b": normalized_headline(
                        b,
                        translations,
                    ),
                    "language_a": lang_a,
                    "language_b": lang_b,
                    "cross_language": (
                        lang_a != lang_b
                    ),
                    "day_gap": round(
                        gap,
                        3,
                    ),
                    "minilm_similarity": round(
                        sim,
                        4,
                    ),
                    "story_token_match": bool(
                        a.get(
                            "story_token"
                        )
                        and b.get(
                            "story_token"
                        )
                        and a.get(
                            "story_token"
                        )
                        == b.get(
                            "story_token"
                        )
                    ),
                }
            )

    if not candidates:
        raise PositiveEnrichmentError(
            "No candidate pairs produced."
        )

    modern_revision = (
        HfApi()
        .model_info(
            MODERNBERT_MODEL
        )
        .sha
        or "unknown"
    )

    tokenizer = (
        AutoTokenizer
        .from_pretrained(
            MODERNBERT_MODEL,
            revision=modern_revision,
        )
    )

    modern_model = (
        AutoModelForSequenceClassification
        .from_pretrained(
            MODERNBERT_MODEL,
            revision=modern_revision,
        )
    )

    modern_model.eval()

    scores = modernbert_scores(
        tokenizer,
        modern_model,
        candidates,
    )

    for item, score in zip(
        candidates,
        scores,
    ):
        item[
            "modernbert_same_probability"
        ] = round(
            score,
            4,
        )

    # Prioritize candidates ModernBERT believes are plausible positives.
    qwen_pool = sorted(
        [
            item
            for item in candidates
            if item[
                "modernbert_same_probability"
            ]
            >= MODERNBERT_FALLBACK_THRESHOLD
        ],
        key=lambda item: (
            -item[
                "modernbert_same_probability"
            ],
            -item[
                "minilm_similarity"
            ],
            item[
                "pair_id"
            ],
        ),
    )[:QWEN_PRESELECT_N]

    if not qwen_pool:
        raise PositiveEnrichmentError(
            "ModernBERT produced no positive-enrichment candidates."
        )

    process = None
    handle = None

    try:
        process, handle = start_server()

        for index, item in enumerate(
            qwen_pool,
            start=1,
        ):
            qwen = qwen_verify(
                item["a"],
                item["b"],
                item["english_a"],
                item["english_b"],
                item["day_gap"],
                item["story_token_match"],
            )

            item[
                "qwen_relationship"
            ] = qwen[
                "relationship"
            ]

            item[
                "qwen_confidence"
            ] = round(
                qwen[
                    "confidence"
                ],
                4,
            )

            item[
                "qwen_reason"
            ] = qwen[
                "reason"
            ]

            print(
                f"[{index}/{len(qwen_pool)}] "
                f"{qwen['relationship']} "
                f"{item['modernbert_same_probability']:.3f} "
                f"{item['minilm_similarity']:.3f}"
            )

    finally:
        stop_server(
            process,
            handle,
        )

    # Exact agreement first.
    positive_candidates = [
        item
        for item in qwen_pool
        if (
            item[
                "qwen_relationship"
            ]
            == "same_event"
            and item[
                "modernbert_same_probability"
            ]
            >= MODERNBERT_SAME_THRESHOLD
        )
    ]

    # If exact agreement is too sparse, retain Qwen-positive + borderline
    # ModernBERT candidates. Human labels remain authoritative.
    if len(
        positive_candidates
    ) < TARGET_SAMPLE_N:
        fallback = [
            item
            for item in qwen_pool
            if (
                item[
                    "qwen_relationship"
                ]
                == "same_event"
                and item not in positive_candidates
            )
        ]

        positive_candidates.extend(
            fallback
        )

    selected = select_sample(
        positive_candidates
    )

    if not selected:
        raise PositiveEnrichmentError(
            "No Qwen-positive same-event candidates found."
        )

    public_pairs = []

    for index, item in enumerate(
        selected,
        start=1,
    ):
        public_pairs.append(
            {
                "sample_id": (
                    f"positive_{index:03d}"
                ),
                "pair_id": item[
                    "pair_id"
                ],
                "article_a": {
                    "article_id": item[
                        "article_a_id"
                    ],
                    "original_headline": item[
                        "a"
                    ][
                        "headline"
                    ],
                    "english_headline": item[
                        "english_a"
                    ],
                    "publisher": item[
                        "a"
                    ][
                        "publisher"
                    ],
                    "url": item[
                        "a"
                    ].get(
                        "url"
                    ),
                    "snippet": item[
                        "a"
                    ].get(
                        "snippet"
                    )
                    or "",
                    "source_language": item[
                        "language_a"
                    ],
                },
                "article_b": {
                    "article_id": item[
                        "article_b_id"
                    ],
                    "original_headline": item[
                        "b"
                    ][
                        "headline"
                    ],
                    "english_headline": item[
                        "english_b"
                    ],
                    "publisher": item[
                        "b"
                    ][
                        "publisher"
                    ],
                    "url": item[
                        "b"
                    ].get(
                        "url"
                    ),
                    "snippet": item[
                        "b"
                    ].get(
                        "snippet"
                    )
                    or "",
                    "source_language": item[
                        "language_b"
                    ],
                },
                "day_gap": item[
                    "day_gap"
                ],

                # Hidden from annotation UI, exported later for analysis.
                "minilm_similarity": item[
                    "minilm_similarity"
                ],
                "modernbert_same_probability": item[
                    "modernbert_same_probability"
                ],
                "qwen_relationship": item[
                    "qwen_relationship"
                ],
                "qwen_confidence": item[
                    "qwen_confidence"
                ],
                "qwen_reason": item[
                    "qwen_reason"
                ],
                "story_token_match": item[
                    "story_token_match"
                ],
                "cross_language": item[
                    "cross_language"
                ],
                "selection_type": (
                    "exact_model_agreement"
                    if item[
                        "modernbert_same_probability"
                    ]
                    >= MODERNBERT_SAME_THRESHOLD
                    else "qwen_positive_modernbert_borderline"
                ),
            }
        )

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT.write_text(
        json.dumps(
            {
                "meta": {
                    "stage": "7B.2E",
                    "purpose": (
                        "human validation of model-agreed "
                        "same-event-enriched candidates"
                    ),
                    "collection_run_key": collection[
                        "run_key"
                    ],
                    "candidate_pool": len(
                        candidates
                    ),
                    "qwen_scored": len(
                        qwen_pool
                    ),
                    "qwen_same_candidates": len(
                        positive_candidates
                    ),
                    "sample_size": len(
                        public_pairs
                    ),
                    "labels": [
                        "same_event",
                        "not_same_event",
                        "unclear_from_headlines",
                    ],
                    "warning": (
                        "This is an intentionally positive-enriched "
                        "development sample. It must not be used to estimate "
                        "real-world same-event prevalence."
                    ),
                    "annotation_blinding": (
                        "MiniLM, ModernBERT and Qwen predictions are stored "
                        "for later analysis but hidden in the web interface."
                    ),
                },
                "pairs": public_pairs,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "all_candidate_pairs": len(
                    candidates
                ),
                "qwen_scored": len(
                    qwen_pool
                ),
                "qwen_positive_candidates": len(
                    positive_candidates
                ),
                "selected_for_human_review": len(
                    public_pairs
                ),
                "cross_language_selected": sum(
                    1
                    for item in selected
                    if item[
                        "cross_language"
                    ]
                ),
                "lower_similarity_selected": sum(
                    1
                    for item in selected
                    if item[
                        "minilm_similarity"
                    ]
                    < 0.78
                ),
            },
            indent=2,
        )
    )

    print(
        f"Output: {OUTPUT}"
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(
            main()
        )
    except PositiveEnrichmentError as exc:
        print(
            f"Positive enrichment failed: {exc}",
            file=sys.stderr,
        )

        raise SystemExit(1)
