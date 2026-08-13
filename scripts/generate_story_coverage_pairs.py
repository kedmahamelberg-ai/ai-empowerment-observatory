#!/usr/bin/env python3
"""Stage 7B.2D — Google News story-coverage positive-candidate acquisition.

Purpose:
The existing active-learning batch produced mostly hard negatives. This stage
deliberately enriches likely SAME-EVENT examples by following Google News
story_token coverage.

Important:
A Google News story_token is ONLY a candidate-generation signal. It is not
treated as human gold and never automatically creates an Observatory event.

Inputs:
- data/review/latest.json from the current weekly news collection
- SERPAPI_KEY GitHub secret

Output:
- review/events/story-coverage/latest.json
- up to 32 blind human-review pairs from up to 8 Google News story groups
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

ROOT = Path(__file__).resolve().parents[1]

COLLECTION_REVIEW = ROOT / "data" / "review" / "latest.json"

OUTPUT = (
    ROOT
    / "review"
    / "events"
    / "story-coverage"
    / "latest.json"
)

SERPAPI_ENDPOINT = "https://serpapi.com/search"

MAX_STORY_TOKENS = int(
    os.environ.get("STORY_COVERAGE_MAX_TOKENS", "8")
)

MAX_ARTICLES_PER_STORY = int(
    os.environ.get("STORY_COVERAGE_MAX_ARTICLES", "6")
)

MAX_PAIRS_PER_STORY = int(
    os.environ.get("STORY_COVERAGE_MAX_PAIRS_PER_STORY", "4")
)

REQUEST_SLEEP_SECONDS = float(
    os.environ.get("STORY_COVERAGE_SLEEP_SECONDS", "0.25")
)

SEED = 20260813


class StoryCoverageError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()

    if not value:
        raise StoryCoverageError(f"{name} is missing.")

    return value


def normalize_url(url: str) -> str:
    url = str(url or "").strip()

    if not url:
        return ""

    try:
        parts = urlsplit(url)

        host = parts.netloc.lower()
        path = re.sub(r"/+$", "", parts.path)

        return urlunsplit(
            (
                parts.scheme.lower(),
                host,
                path,
                "",
                "",
            )
        )
    except Exception:
        return url


def source_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(
            value.get("name")
            or value.get("title")
            or "Unknown source"
        ).strip()

    return str(
        value or "Unknown source"
    ).strip()


def article_from_result(
    result: dict[str, Any],
) -> dict[str, Any] | None:
    title = str(
        result.get("title") or ""
    ).strip()

    link = str(
        result.get("link") or ""
    ).strip()

    if not title or not link:
        return None

    return {
        "title": title,
        "link": link,
        "normalized_url": normalize_url(link),
        "source": source_name(
            result.get("source")
        ),
        "iso_date": result.get("iso_date"),
        "date": result.get("date"),
        "snippet": str(
            result.get("snippet")
            or result.get("description")
            or ""
        ).strip(),
    }


def iter_story_groups(
    news_results: list[Any],
) -> list[dict[str, Any]]:
    groups = []

    for item in news_results or []:
        if not isinstance(item, dict):
            continue

        token = item.get("story_token")

        if token:
            groups.append(
                {
                    "story_token": str(token),
                    "seed_title": str(
                        item.get("title") or ""
                    ).strip(),
                    "seed_source": source_name(
                        item.get("source")
                    ),
                    "embedded_stories": (
                        item.get("stories")
                        if isinstance(
                            item.get("stories"),
                            list,
                        )
                        else []
                    ),
                }
            )

        # Some responses can nest story-bearing structures.
        nested = item.get("stories")

        if isinstance(nested, list):
            for child in nested:
                if (
                    isinstance(child, dict)
                    and child.get("story_token")
                ):
                    groups.append(
                        {
                            "story_token": str(
                                child["story_token"]
                            ),
                            "seed_title": str(
                                child.get("title") or ""
                            ).strip(),
                            "seed_source": source_name(
                                child.get("source")
                            ),
                            "embedded_stories": (
                                child.get("stories")
                                if isinstance(
                                    child.get("stories"),
                                    list,
                                )
                                else []
                            ),
                        }
                    )

    deduped = {}

    for group in groups:
        token = group["story_token"]

        if token not in deduped:
            deduped[token] = group

    return list(deduped.values())


def market_params(
    search: dict[str, Any],
) -> tuple[str, str]:
    iso3 = str(
        search.get("iso3") or ""
    ).upper()

    language = str(
        search.get("language") or "en"
    ).lower()

    gl_map = {
        "USA": "us",
        "CHN": "cn",
        "GBR": "uk",
        "FRA": "fr",
        "CAN": "ca",
    }

    gl = gl_map.get(
        iso3,
        "us",
    )

    hl_map = {
        "zh": "zh-CN",
        "zh-cn": "zh-CN",
        "fr": "fr",
        "en": "en",
    }

    hl = hl_map.get(
        language,
        language or "en",
    )

    return gl, hl


def serpapi_get(
    api_key: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    request_params = {
        **params,
        "api_key": api_key,
        "output": "json",
    }

    response = requests.get(
        SERPAPI_ENDPOINT,
        params=request_params,
        timeout=60,
    )

    try:
        data = response.json()
    except ValueError:
        data = {}

    if not response.ok:
        api_error = data.get("error") if isinstance(data, dict) else None
        raise StoryCoverageError(
            f"SerpApi HTTP {response.status_code}: "
            f"{api_error or response.text[:500]}"
        )

    if data.get("error"):
        raise StoryCoverageError(
            f"SerpApi error: {data['error']}"
        )

    return data


def rediscover_story_tokens(
    api_key: str,
    searches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    discovered = []

    for search in searches:
        if search.get("status") != "success":
            continue

        query = str(
            search.get("query") or ""
        ).strip()

        if not query:
            continue

        gl, hl = market_params(search)

        print(
            f"Rediscovering story tokens: "
            f"{search.get('country')} ({gl}, {hl})"
        )

        # `q` cannot be combined with Google News advanced parameters.
        # `so` is valid with story_token/section_token, not with a normal
        # query search. Relevance is already the default.
        data = serpapi_get(
            api_key,
            {
                "engine": "google_news",
                "q": query,
                "gl": gl,
                "hl": hl,
            },
        )

        groups = iter_story_groups(
            data.get("news_results") or []
        )

        for group in groups:
            discovered.append(
                {
                    **group,
                    "search_country": search.get(
                        "country"
                    ),
                    "search_iso3": search.get(
                        "iso3"
                    ),
                    "search_language": search.get(
                        "language"
                    ),
                    "gl": gl,
                    "hl": hl,
                    "query": query,
                }
            )

        time.sleep(
            REQUEST_SLEEP_SECONDS
        )

    # Deduplicate story tokens globally.
    deduped = {}

    for item in discovered:
        token = item["story_token"]

        if token not in deduped:
            deduped[token] = item

    return list(
        deduped.values()
    )


def full_story_coverage(
    api_key: str,
    group: dict[str, Any],
) -> list[dict[str, Any]]:
    data = serpapi_get(
        api_key,
        {
            "engine": "google_news",
            "story_token": group["story_token"],
            "gl": group["gl"],
            "so": "0",
        },
    )

    articles = []

    for item in data.get(
        "news_results"
    ) or []:
        if not isinstance(item, dict):
            continue

        article = article_from_result(
            item
        )

        if article:
            articles.append(article)

        nested = item.get("stories")

        if isinstance(nested, list):
            for child in nested:
                if not isinstance(
                    child,
                    dict,
                ):
                    continue

                article = article_from_result(
                    child
                )

                if article:
                    articles.append(article)

    # Use any embedded stories from the seed response too.
    for child in group.get(
        "embedded_stories"
    ) or []:
        if not isinstance(child, dict):
            continue

        article = article_from_result(
            child
        )

        if article:
            articles.append(article)

    deduped = {}

    for article in articles:
        key = (
            article["normalized_url"]
            or article["title"].casefold()
        )

        if key not in deduped:
            deduped[key] = article

    result = list(
        deduped.values()
    )

    result.sort(
        key=lambda article: (
            article.get("iso_date") or "",
            article["source"].casefold(),
            article["title"].casefold(),
        )
    )

    return result


def stable_pair_id(
    token: str,
    a: dict[str, Any],
    b: dict[str, Any],
) -> str:
    payload = (
        token
        + "\n"
        + (a["normalized_url"] or a["title"])
        + "\n"
        + (b["normalized_url"] or b["title"])
    )

    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()[:32]


def pair_candidates_for_group(
    group: dict[str, Any],
    articles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(articles) < 2:
        return []

    # Prefer the most informative anchor:
    # a mainstream/top-ranked-looking first article with the richest title.
    anchor = sorted(
        articles,
        key=lambda article: (
            -len(article["title"]),
            article["source"].casefold(),
        ),
    )[0]

    others = [
        article
        for article in articles
        if article is not anchor
    ]

    # Prefer source diversity.
    others.sort(
        key=lambda article: (
            article["source"].casefold()
            == anchor["source"].casefold(),
            -len(article["title"]),
            article["source"].casefold(),
        )
    )

    pairs = []

    for other in others[
        :MAX_PAIRS_PER_STORY
    ]:
        pairs.append(
            {
                "pair_id": stable_pair_id(
                    group["story_token"],
                    anchor,
                    other,
                ),
                "article_a": anchor,
                "article_b": other,
                # Hidden provenance for later analysis.
                "story_token": group["story_token"],
                "search_country": group[
                    "search_country"
                ],
                "search_iso3": group[
                    "search_iso3"
                ],
                "search_language": group[
                    "search_language"
                ],
                "seed_title": group[
                    "seed_title"
                ],
                "coverage_size": len(
                    articles
                ),
            }
        )

    return pairs


def main() -> int:
    api_key = required_env(
        "SERPAPI_KEY"
    )

    if not COLLECTION_REVIEW.exists():
        raise StoryCoverageError(
            f"Missing collection review: {COLLECTION_REVIEW}"
        )

    collection = json.loads(
        COLLECTION_REVIEW.read_text(
            encoding="utf-8"
        )
    )

    searches = collection.get(
        "searches"
    ) or []

    if not searches:
        raise StoryCoverageError(
            "Collection review contains no searches."
        )

    discovered = rediscover_story_tokens(
        api_key,
        searches,
    )

    if not discovered:
        raise StoryCoverageError(
            "No Google News story_token values were discovered. "
            "The current queries may not be returning grouped stories."
        )

    # Prefer groups whose seed looks AI-specific and has embedded stories.
    discovered.sort(
        key=lambda group: (
            -len(
                group.get(
                    "embedded_stories"
                ) or []
            ),
            -len(
                group.get(
                    "seed_title"
                ) or ""
            ),
            group["story_token"],
        )
    )

    selected_groups = discovered[
        :MAX_STORY_TOKENS
    ]

    output_groups = []
    all_pairs = []

    for index, group in enumerate(
        selected_groups,
        start=1,
    ):
        print(
            f"[{index}/{len(selected_groups)}] "
            f"Fetching full story coverage: "
            f"{group['seed_title'][:90]}"
        )

        articles = full_story_coverage(
            api_key,
            group,
        )

        articles = articles[
            :MAX_ARTICLES_PER_STORY
        ]

        pairs = pair_candidates_for_group(
            group,
            articles,
        )

        if pairs:
            all_pairs.extend(pairs)

            output_groups.append(
                {
                    "group_id": (
                        f"story_{index:02d}"
                    ),
                    "story_token": group[
                        "story_token"
                    ],
                    "seed_title": group[
                        "seed_title"
                    ],
                    "search_country": group[
                        "search_country"
                    ],
                    "search_iso3": group[
                        "search_iso3"
                    ],
                    "search_language": group[
                        "search_language"
                    ],
                    "coverage_size": len(
                        articles
                    ),
                    "article_count_used": len(
                        articles
                    ),
                    "pair_count": len(
                        pairs
                    ),
                    "articles": articles,
                }
            )

        time.sleep(
            REQUEST_SLEEP_SECONDS
        )

    if not all_pairs:
        raise StoryCoverageError(
            "Story coverage was found but no multi-article pairs "
            "could be generated."
        )

    rng = random.Random(SEED)

    # Keep group diversity in display order.
    rng.shuffle(all_pairs)

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT.write_text(
        json.dumps(
            {
                "meta": {
                    "stage": "7B.2D",
                    "purpose": (
                        "human validation of same-event-enriched "
                        "Google News story coverage"
                    ),
                    "source": "SerpApi Google News story_token full coverage",
                    "story_tokens_discovered": len(
                        discovered
                    ),
                    "story_groups_fetched": len(
                        selected_groups
                    ),
                    "usable_story_groups": len(
                        output_groups
                    ),
                    "pair_count": len(
                        all_pairs
                    ),
                    "labels": [
                        "same_event",
                        "not_same_event",
                        "unclear_from_headlines",
                    ],
                    "warning": (
                        "Google News story grouping is used only to enrich "
                        "candidate positives. It is not treated as ground truth."
                    ),
                },
                # Groups retain story provenance for later engineering analysis.
                "groups": output_groups,
                # UI shows only pairs and hides story-token identity.
                "pairs": all_pairs,
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
                "story_tokens_discovered": len(
                    discovered
                ),
                "story_groups_fetched": len(
                    selected_groups
                ),
                "usable_story_groups": len(
                    output_groups
                ),
                "pair_count": len(
                    all_pairs
                ),
            },
            indent=2,
        )
    )

    print(f"Output: {OUTPUT}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except StoryCoverageError as exc:
        print(
            f"Story coverage failed: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
