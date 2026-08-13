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


AI_TEXT_TERMS = (
    "artificial intelligence",
    "generative ai",
    "generative artificial intelligence",
    "machine learning",
    "large language model",
    "llm",
    "chatgpt",
    "openai",
    "anthropic",
    "gemini",
    "claude",
    "deepmind",
    "intelligence artificielle",
    "intelligence artificielle générative",
    "人工智能",
    "生成式人工智能",
    "大模型",
)

TECH_TOPIC_TERMS = (
    "technology",
    "tech",
    "technologie",
    "科技",
    "技术",
)

AI_SECTION_TERMS = (
    "artificial intelligence",
    "ai",
    "intelligence artificielle",
    "人工智能",
)


def text_matches_any(value: str, terms: tuple[str, ...]) -> bool:
    text = str(value or "").casefold()
    return any(term.casefold() in text for term in terms)


def display_text(node: dict[str, Any]) -> str:
    candidates = [
        node.get("title"),
        node.get("name"),
        node.get("label"),
    ]

    highlight = node.get("highlight")
    if isinstance(highlight, dict):
        candidates.append(highlight.get("title"))

    stories = node.get("stories")
    if isinstance(stories, list) and stories:
        first = stories[0]
        if isinstance(first, dict):
            candidates.append(first.get("title"))

    return " | ".join(
        str(value).strip()
        for value in candidates
        if value and str(value).strip()
    )


def recursive_token_nodes(
    value: Any,
    token_key: str,
) -> list[dict[str, Any]]:
    found = []

    if isinstance(value, dict):
        if value.get(token_key):
            found.append(value)

        for child in value.values():
            found.extend(
                recursive_token_nodes(
                    child,
                    token_key,
                )
            )

    elif isinstance(value, list):
        for child in value:
            found.extend(
                recursive_token_nodes(
                    child,
                    token_key,
                )
            )

    return found


def iter_story_groups(
    news_results: list[Any],
    *,
    require_ai_relevance: bool = False,
) -> list[dict[str, Any]]:
    groups = []

    for node in recursive_token_nodes(
        news_results,
        "story_token",
    ):
        token = node.get("story_token")
        if not token:
            continue

        seed_title = display_text(node)

        if (
            require_ai_relevance
            and not text_matches_any(
                seed_title,
                AI_TEXT_TERMS,
            )
        ):
            continue

        embedded = node.get("stories")
        if not isinstance(embedded, list):
            embedded = []

        groups.append(
            {
                "story_token": str(token),
                "seed_title": seed_title,
                "seed_source": source_name(
                    node.get("source")
                ),
                "embedded_stories": embedded,
            }
        )

    deduped = {}

    for group in groups:
        token = group["story_token"]
        if token not in deduped:
            deduped[token] = group

    return list(deduped.values())


def find_topic_candidates(
    data: dict[str, Any],
    terms: tuple[str, ...],
) -> list[dict[str, Any]]:
    matches = []

    for node in recursive_token_nodes(
        data,
        "topic_token",
    ):
        label = display_text(node)

        if text_matches_any(
            label,
            terms,
        ):
            matches.append(
                {
                    "topic_token": str(
                        node["topic_token"]
                    ),
                    "label": label,
                }
            )

    deduped = {}

    for item in matches:
        deduped.setdefault(
            item["topic_token"],
            item,
        )

    return list(deduped.values())


def find_ai_sections(
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    matches = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            token = value.get(
                "section_token"
            )

            if token:
                label = display_text(value)

                if text_matches_any(
                    label,
                    AI_SECTION_TERMS,
                ):
                    matches.append(
                        {
                            "section_token": str(
                                token
                            ),
                            "topic_token": (
                                str(
                                    value.get(
                                        "topic_token"
                                    )
                                )
                                if value.get(
                                    "topic_token"
                                )
                                else None
                            ),
                            "label": label,
                        }
                    )

            for child in value.values():
                walk(child)

        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)

    deduped = {}

    for item in matches:
        deduped.setdefault(
            item["section_token"],
            item,
        )

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
    """Discover story tokens through grouped topic/section pages.

    Normal Google News q= searches frequently return flat article lists with no
    story_token. Topic pages, by contrast, expose Google News story groups.

    Strategy per market:
    1. Run the existing AI query.
       - accept any story_token if Google happens to group results
       - inspect the response for an AI-specific topic_token
    2. If an AI topic_token exists, fetch that topic page.
    3. Otherwise fetch the market front page, discover Technology, then:
       - use an AI subsection if present
       - otherwise use Technology and retain only AI-relevant story groups
    """
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

        context = {
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

        print(
            f"Discovering grouped AI stories: "
            f"{search.get('country')} ({gl}, {hl})"
        )

        # Step 1 — normal AI query.
        query_data = serpapi_get(
            api_key,
            {
                "engine": "google_news",
                "q": query,
                "gl": gl,
                "hl": hl,
            },
        )

        direct_groups = iter_story_groups(
            query_data.get(
                "news_results"
            ) or [],
        )

        for group in direct_groups:
            discovered.append(
                {
                    **group,
                    **context,
                    "discovery_route": (
                        "query_group"
                    ),
                }
            )

        # Search responses can now surface topic metadata in sidebar/related
        # structures. Prefer AI-specific topic tokens when present.
        ai_topics = find_topic_candidates(
            query_data,
            AI_SECTION_TERMS,
        )

        route_groups = []

        for topic in ai_topics[:2]:
            print(
                f"  AI topic route: "
                f"{topic['label'][:80]}"
            )

            topic_data = serpapi_get(
                api_key,
                {
                    "engine": "google_news",
                    "topic_token": topic[
                        "topic_token"
                    ],
                    "gl": gl,
                    "hl": hl,
                },
            )

            route_groups.extend(
                iter_story_groups(
                    topic_data.get(
                        "news_results"
                    ) or [],
                )
            )

            time.sleep(
                REQUEST_SLEEP_SECONDS
            )

        # Step 3 — fallback via Home -> Technology -> AI section.
        if not route_groups:
            print(
                "  No AI topic token from query; "
                "trying Technology topic route"
            )

            front_page = serpapi_get(
                api_key,
                {
                    "engine": "google_news",
                    "gl": gl,
                    "hl": hl,
                },
            )

            tech_topics = find_topic_candidates(
                front_page,
                TECH_TOPIC_TERMS,
            )

            for tech in tech_topics[:1]:
                tech_data = serpapi_get(
                    api_key,
                    {
                        "engine": "google_news",
                        "topic_token": tech[
                            "topic_token"
                        ],
                        "gl": gl,
                        "hl": hl,
                    },
                )

                ai_sections = find_ai_sections(
                    tech_data
                )

                if ai_sections:
                    for section in ai_sections[:1]:
                        print(
                            f"  AI subsection: "
                            f"{section['label'][:80]}"
                        )

                        section_params = {
                            "engine": "google_news",
                            "section_token": section[
                                "section_token"
                            ],
                            "gl": gl,
                            "hl": hl,
                        }

                        # SerpApi documents section_token together with
                        # topic_token. Prefer the section's own token when
                        # present, otherwise use the Technology topic.
                        section_params[
                            "topic_token"
                        ] = (
                            section.get(
                                "topic_token"
                            )
                            or tech[
                                "topic_token"
                            ]
                        )

                        section_data = serpapi_get(
                            api_key,
                            section_params,
                        )

                        route_groups.extend(
                            iter_story_groups(
                                section_data.get(
                                    "news_results"
                                ) or [],
                            )
                        )
                else:
                    print(
                        "  No AI subsection exposed; "
                        "filtering Technology story groups for AI relevance"
                    )

                    route_groups.extend(
                        iter_story_groups(
                            tech_data.get(
                                "news_results"
                            ) or [],
                            require_ai_relevance=True,
                        )
                    )

                time.sleep(
                    REQUEST_SLEEP_SECONDS
                )

        for group in route_groups:
            discovered.append(
                {
                    **group,
                    **context,
                    "discovery_route": (
                        "ai_or_technology_topic"
                    ),
                }
            )

        print(
            f"  Story tokens found so far: "
            f"{len(discovered)}"
        )

        time.sleep(
            REQUEST_SLEEP_SECONDS
        )

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
            "No Google News story_token values were discovered even after "
            "query, AI-topic, and Technology-topic fallback routes."
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
