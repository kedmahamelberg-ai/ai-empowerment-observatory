#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supabase import Client, create_client

ROOT = Path(__file__).resolve().parents[1]

LENSES_PATH = ROOT / "data" / "lenses" / "latest.json"
EVENTS_PATH = ROOT / "data" / "events" / "latest.json"

INSIGHTS_PATH = ROOT / "data" / "insights" / "latest.json"
HISTORY_PATH = ROOT / "data" / "history" / "releases.json"

TRANSLATION_PROFILE = "validated_language_routing_v3"

MARKET_NAMES = {
    "USA": "United States",
    "CHN": "China",
    "GBR": "United Kingdom",
    "FRA": "France",
    "CAN": "Canada",
}

TOPIC_LABELS = {
    "work_employment": "Work & jobs",
    "business_productivity": "Business & productivity",
    "consumer_services": "Consumer services",
    "creativity_ip": "Creativity & intellectual property",
    "education_research": "Education & research",
    "healthcare": "Healthcare",
    "government_regulation": "Government & rules",
    "privacy_security": "Privacy & security",
    "infrastructure_investment": "Infrastructure & investment",
    "other": "Other",
}

THEME_RULES = [
    (
        "Safety, security & misinformation",
        [
            r"\bsecurity\b", r"\bvulnerab", r"\bcyber", r"\bsandbox\b",
            r"\bmisinformation\b", r"\bdisinformation\b", r"\bdeepfake",
            r"\bnuclear\b", r"\bweapon", r"\bvirus", r"\bthreat",
            r"\bfraud\b", r"\bprivacy\b", r"\brisk\b", r"\bsafety\b",
        ],
    ),
    (
        "Government, rights & regulation",
        [
            r"\bai act\b", r"\bregulat", r"\blaw\b", r"\bpolicy\b",
            r"\bgovernment\b", r"\bgovernance\b", r"\bright",
            r"\bcopyright\b", r"\btransparen", r"\bstandard",
            r"\bethic", r"\baccountab", r"\bdiplomat",
        ],
    ),
    (
        "Work, jobs & skills",
        [
            r"\bjob", r"\bemployment\b", r"\bworker", r"\bworkplace\b",
            r"\blabour\b", r"\blabor\b", r"\bcareer\b", r"\bintern",
            r"\bproductiv", r"\breplace", r"\bskills?\b",
        ],
    ),
    (
        "Education & learning",
        [
            r"\buniversity\b", r"\bschool\b", r"\bcurriculum\b",
            r"\beducation\b", r"\bstudent", r"\bteacher", r"\bclassroom\b",
            r"\bcollege\b", r"\binstitute\b", r"\btraining\b",
        ],
    ),
    (
        "Science & research",
        [
            r"\bresearch\b", r"\bscience\b", r"\bscientist",
            r"\bastronom", r"\bglacier", r"\bphysics\b", r"\bdiscovery\b",
            r"\blaboratory\b", r"\blab\b",
        ],
    ),
    (
        "Health & medicine",
        [
            r"\bhealth", r"\bmedical\b", r"\bmedicine\b", r"\bhospital\b",
            r"\bcancer\b", r"\bpharma", r"\bdrug\b", r"\bpatient",
            r"\bclinical\b",
        ],
    ),
    (
        "Business, markets & investment",
        [
            r"\bbusiness\b", r"\bcompany\b", r"\bstock", r"\bmarket\b",
            r"\binvest", r"\bfinancial\b", r"\bfunding\b", r"\bstartup\b",
            r"\benterprise\b", r"\bceo\b", r"\beconom",
        ],
    ),
    (
        "AI products & model releases",
        [
            r"\bmodel\b", r"\blaunch", r"\brelease", r"\bopen-weight\b",
            r"\bchatbot\b", r"\bgemini\b", r"\bclaude\b", r"\bchatgpt\b",
            r"\bopenai\b", r"\banthropic\b", r"\bkimi\b",
        ],
    ),
    (
        "Infrastructure, chips & energy",
        [
            r"\bdata cent", r"\bdatacent", r"\benergy\b", r"\bpower\b",
            r"\bchip", r"\bsemiconductor", r"\binfrastructure\b",
            r"\bcompute\b",
        ],
    ),
    (
        "Creativity, media & culture",
        [
            r"\bcreator", r"\bcreative\b", r"\bart\b", r"\bmusic\b",
            r"\bfilm\b", r"\bperformance\b", r"\bmedia\b",
            r"\brelationship", r"\bwriting\b",
        ],
    ),
    (
        "International affairs & defence",
        [
            r"\bmilitary\b", r"\barmy\b", r"\bdefen[cs]e\b",
            r"\bnational security\b", r"\bgeopolit", r"\bexport control",
            r"\bsovereign", r"\bdiplomatic\b",
        ],
    ),
    (
        "Events & ecosystem",
        [
            r"\bconference\b", r"\bchallenge\b", r"\bcompetition\b",
            r"\bforum\b", r"\bsummit\b", r"\bevent\b",
        ],
    ),
]


class InsightError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise InsightError(f"{name} is missing.")
    return value


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise InsightError(f"Missing required artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def batch_in(
    client: Client,
    table: str,
    select: str,
    column: str,
    values: list[str],
    size: int = 120,
) -> list[dict[str, Any]]:
    rows = []

    for start in range(0, len(values), size):
        response = (
            client.table(table)
            .select(select)
            .in_(column, values[start:start + size])
            .execute()
        )
        rows.extend(getattr(response, "data", None) or [])

    return rows


def latest_translations(
    client: Client,
    article_ids: list[str],
) -> dict[str, dict[str, Any]]:
    rows = []

    for start in range(0, len(article_ids), 120):
        response = (
            client.table("article_translations")
            .select(
                "article_id,translated_headline,"
                "source_language_iso2,created_at"
            )
            .eq("translation_profile", TRANSLATION_PROFILE)
            .in_("article_id", article_ids[start:start + 120])
            .order("created_at", desc=True)
            .execute()
        )

        rows.extend(getattr(response, "data", None) or [])

    newest = {}

    for row in rows:
        aid = str(row["article_id"])
        if aid not in newest:
            newest[aid] = row

    return newest


def theme_for(text: str, model_topic: str | None = None) -> str:
    topic = str(model_topic or "other")

    if topic != "other" and topic in TOPIC_LABELS:
        return TOPIC_LABELS[topic]

    normalized = str(text or "").casefold()

    for label, patterns in THEME_RULES:
        if any(re.search(pattern, normalized, flags=re.I) for pattern in patterns):
            return label

    return "Other AI developments"


def theme_distribution(
    rows: list[dict[str, Any]],
    title_lookup: dict[str, str],
    id_field: str,
) -> list[dict[str, Any]]:
    counts = Counter()

    for row in rows:
        unit_id = str(row.get(id_field) or "")
        title = title_lookup.get(unit_id, "")
        label = theme_for(title, row.get("topic"))
        counts[label] += 1

    total = sum(counts.values())

    return [
        {
            "label": label,
            "count": int(count),
            "share": round(count / total, 4) if total else 0.0,
        }
        for label, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]


def simple_distribution(
    rows: list[dict[str, Any]],
    field: str,
) -> dict[str, dict[str, Any]]:
    counts = Counter(
        str(row.get(field) or "unclear")
        for row in rows
    )
    total = sum(counts.values())

    return {
        key: {
            "count": int(count),
            "share": round(count / total, 4) if total else 0.0,
        }
        for key, count in sorted(counts.items())
    }


def observation_range(events: dict[str, Any]) -> tuple[str | None, str | None]:
    values = []

    for event in events.get("events", []):
        if event.get("event_date"):
            values.append(str(event["event_date"])[:10])

        for source in event.get("sources", []):
            if source.get("published_at"):
                values.append(str(source["published_at"])[:10])

    values = sorted(value for value in values if value)

    return (
        values[0] if values else None,
        values[-1] if values else None,
    )


def main() -> int:
    client: Client = create_client(
        required_env("SUPABASE_URL"),
        required_env("SUPABASE_SECRET_KEY"),
    )

    lenses = load_json(LENSES_PATH)
    events = load_json(EVENTS_PATH)

    run_id = str(
        lenses.get("meta", {}).get("classification_run_id")
        or ""
    ).strip()

    if not run_id:
        raise InsightError(
            "No classification_run_id in data/lenses/latest.json."
        )

    run_response = (
        client.table("classification_runs")
        .select("classification_run_id,collection_run_id,run_key")
        .eq("classification_run_id", run_id)
        .limit(1)
        .execute()
    )

    run_rows = getattr(run_response, "data", None) or []

    if not run_rows:
        raise InsightError(
            f"Classification run not found: {run_id}"
        )

    collection_run_id = str(
        run_rows[0]["collection_run_id"]
    )

    response = (
        client.table("lens_classifications")
        .select(
            "lens_classification_id,lens,article_id,event_id,"
            "ai_relevant,empowerment_status,narrative_frame,"
            "distribution_breadth,dominant_dimension,topic,"
            "primary_country_iso3"
        )
        .eq("classification_run_id", run_id)
        .execute()
    )

    rows = getattr(response, "data", None) or []

    coverage_rows = [
        row for row in rows
        if row["lens"] == "coverage" and row["ai_relevant"]
    ]

    event_rows = [
        row for row in rows
        if row["lens"] == "event" and row["ai_relevant"]
    ]

    coverage_article_ids = [
        str(row["article_id"])
        for row in coverage_rows
        if row.get("article_id")
    ]

    event_ids = [
        str(row["event_id"])
        for row in event_rows
        if row.get("event_id")
    ]

    article_rows = batch_in(
        client,
        "articles",
        (
            "article_id,headline,publisher,canonical_url,"
            "published_at,first_seen_at"
        ),
        "article_id",
        coverage_article_ids,
    )

    article_map = {
        str(row["article_id"]): row
        for row in article_rows
    }

    translations = latest_translations(
        client,
        coverage_article_ids,
    )

    article_title = {}

    for aid in coverage_article_ids:
        article = article_map.get(aid) or {}
        translation = translations.get(aid) or {}

        article_title[aid] = str(
            translation.get("translated_headline")
            or article.get("headline")
            or ""
        )

    public_event_map = {
        str(event["event_id"]): event
        for event in events.get("events", [])
    }

    event_title = {
        eid: str(
            (public_event_map.get(eid) or {}).get("event_title")
            or ""
        )
        for eid in event_ids
    }

    non_emp_coverage = [
        row for row in coverage_rows
        if row["empowerment_status"] == "non_empowerment"
    ]

    non_emp_event = [
        row for row in event_rows
        if row["empowerment_status"] == "non_empowerment"
    ]

    # Source inventory.
    publisher_article_counts = Counter(
        str(
            (article_map.get(aid) or {}).get("publisher")
            or "Unknown source"
        )
        for aid in coverage_article_ids
    )

    event_article_rows = batch_in(
        client,
        "event_articles",
        "event_id,article_id",
        "event_id",
        event_ids,
        size=100,
    )

    all_event_article_ids = sorted(
        {
            str(row["article_id"])
            for row in event_article_rows
        }
    )

    missing_ids = [
        aid
        for aid in all_event_article_ids
        if aid not in article_map
    ]

    if missing_ids:
        extra_articles = batch_in(
            client,
            "articles",
            (
                "article_id,headline,publisher,canonical_url,"
                "published_at,first_seen_at"
            ),
            "article_id",
            missing_ids,
        )

        for row in extra_articles:
            article_map[str(row["article_id"])] = row

    event_publishers = defaultdict(set)

    for row in event_article_rows:
        eid = str(row["event_id"])
        aid = str(row["article_id"])

        publisher = str(
            (article_map.get(aid) or {}).get("publisher")
            or "Unknown source"
        )

        event_publishers[eid].add(publisher)

    publisher_event_counts = Counter()

    for publishers in event_publishers.values():
        for publisher in publishers:
            publisher_event_counts[publisher] += 1

    source_names = sorted(
        set(publisher_article_counts)
        | set(publisher_event_counts)
    )

    sources = [
        {
            "publisher": publisher,
            "article_count": int(
                publisher_article_counts[publisher]
            ),
            "unique_event_count": int(
                publisher_event_counts[publisher]
            ),
        }
        for publisher in source_names
    ]

    sources.sort(
        key=lambda row: (
            -row["article_count"],
            -row["unique_event_count"],
            row["publisher"].casefold(),
        )
    )

    # Search-market discovery statistics.
    obs_response = (
        client.table("article_observations")
        .select(
            "article_id,search_country_iso3,"
            "search_language,search_rank"
        )
        .eq("run_id", collection_run_id)
        .execute()
    )

    observations = getattr(obs_response, "data", None) or []

    coverage_set = set(coverage_article_ids)
    market_articles = defaultdict(set)
    market_languages = defaultdict(set)
    market_publishers = defaultdict(Counter)

    for row in observations:
        aid = str(row["article_id"])

        if aid not in coverage_set:
            continue

        market = str(
            row.get("search_country_iso3")
            or "UNK"
        )

        market_articles[market].add(aid)

        language = str(
            row.get("search_language")
            or ""
        ).strip()

        if language:
            market_languages[market].add(language)

        publisher = str(
            (article_map.get(aid) or {}).get("publisher")
            or "Unknown source"
        )

        market_publishers[market][publisher] += 1

    discovery_markets = []

    for iso3 in sorted(
        market_articles,
        key=lambda code: (
            -len(market_articles[code]),
            code,
        ),
    ):
        publishers = market_publishers[iso3]

        discovery_markets.append(
            {
                "country_iso3": iso3,
                "name": MARKET_NAMES.get(iso3, iso3),
                "article_count": len(
                    market_articles[iso3]
                ),
                "unique_publishers": len(
                    publishers
                ),
                "languages": sorted(
                    market_languages[iso3]
                ),
                "top_publishers": [
                    {
                        "publisher": publisher,
                        "article_count": int(count),
                    }
                    for publisher, count in publishers.most_common(5)
                ],
                "note": (
                    "Discovery-market counts can overlap because the same "
                    "article may appear in more than one search market."
                ),
            }
        )

    start, end = observation_range(events)

    insights = {
        "meta": {
            "version": "public_insights_v3",
            "generated_at": (
                datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            ),
            "classification_run_id": run_id,
            "collection_run_id": collection_run_id,
            "observation_start": start,
            "observation_end": end,
            "source_method": (
                "Publications and organisations are dynamically observed "
                "through the current discovery workflow; this is not a fixed "
                "journal whitelist."
            ),
            "theme_method": (
                "Descriptive themes are a separate navigation layer. "
                "They do not affect the empowerment index."
            ),
        },
        "discovery_markets": discovery_markets,
        "sources": {
            "unique_publishers": len(sources),
            "rows": sources,
        },
        "coverage": {
            "themes": theme_distribution(
                coverage_rows,
                article_title,
                "article_id",
            ),
            "by_status": simple_distribution(
                coverage_rows,
                "empowerment_status",
            ),
            "by_narrative": simple_distribution(
                coverage_rows,
                "narrative_frame",
            ),
        },
        "event": {
            "themes": theme_distribution(
                event_rows,
                event_title,
                "event_id",
            ),
            "by_status": simple_distribution(
                event_rows,
                "empowerment_status",
            ),
            "by_narrative": simple_distribution(
                event_rows,
                "narrative_frame",
            ),
        },
        "non_empowerment": {
            "coverage": {
                "unit_count": len(non_emp_coverage),
                "themes": theme_distribution(
                    non_emp_coverage,
                    article_title,
                    "article_id",
                ),
            },
            "event": {
                "unit_count": len(non_emp_event),
                "themes": theme_distribution(
                    non_emp_event,
                    event_title,
                    "event_id",
                ),
            },
        },
    }

    INSIGHTS_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    INSIGHTS_PATH.write_text(
        json.dumps(
            insights,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    coverage = lenses["global"]["coverage"]
    event = lenses["global"]["event"]
    amplification = lenses["global"]["amplification"]

    point = {
        "release_id": run_id,
        "window_start": start,
        "window_end": end,
        "coverage_count": int(
            coverage.get("unit_count_ai_relevant")
            or 0
        ),
        "event_count": int(
            event.get("unit_count_ai_relevant")
            or 0
        ),
        "extra_article_instances": max(
            0,
            int(
                coverage.get(
                    "unit_count_ai_relevant"
                )
                or 0
            )
            - int(
                event.get(
                    "unit_count_ai_relevant"
                )
                or 0
            ),
        ),
        "coverage_index": coverage.get(
            "empowerment_index"
        ),
        "event_index": event.get(
            "empowerment_index"
        ),
        "amplification_gap": amplification.get(
            "directional_amplification_gap"
        ),
        "coverage_event_ratio": amplification.get(
            "coverage_event_ratio"
        ),
    }

    history = {
        "meta": {
            "series": "weekly_public_releases",
            "cumulative": False,
            "note": (
                "Each point is one weekly Observatory release. "
                "Counts are not cumulatively summed."
            ),
        },
        "points": [],
    }

    if HISTORY_PATH.exists():
        try:
            existing = json.loads(
                HISTORY_PATH.read_text(
                    encoding="utf-8"
                )
            )

            if isinstance(existing, dict):
                history.update(existing)
        except Exception:
            pass

    points = [
        row
        for row in history.get(
            "points",
            [],
        )
        if row.get("release_id") != run_id
    ]

    points.append(point)

    points.sort(
        key=lambda row: (
            str(row.get("window_end") or ""),
            str(row.get("release_id") or ""),
        )
    )

    history["points"] = points

    HISTORY_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    HISTORY_PATH.write_text(
        json.dumps(
            history,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "classification_run_id": run_id,
                "coverage_units": len(
                    coverage_rows
                ),
                "event_units": len(
                    event_rows
                ),
                "unique_sources": len(
                    sources
                ),
                "discovery_markets": len(
                    discovery_markets
                ),
                "history_points": len(
                    points
                ),
            },
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
