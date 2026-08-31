#!/usr/bin/env python3
"""Generate public source/theme insights from the canonical weekly release.

The weekly release is the single public source of truth. This script deliberately
avoids querying an independently selected classification run, because that can
produce public derivatives whose numbers do not match ``data/releases/current.json``.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RELEASE_PATH = ROOT / "data" / "releases" / "current.json"
RELEASE_INDEX_PATH = ROOT / "data" / "releases" / "index.json"
INSIGHTS_PATH = ROOT / "data" / "insights" / "latest.json"
HISTORY_PATH = ROOT / "data" / "history" / "releases.json"

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


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise InsightError(f"Missing required artifact: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise InsightError(f"Expected a JSON object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def theme_for(text: str, model_topic: str | None = None) -> str:
    topic = str(model_topic or "other")
    if topic != "other" and topic in TOPIC_LABELS:
        return TOPIC_LABELS[topic]

    normalized = str(text or "").casefold()
    for label, patterns in THEME_RULES:
        if any(re.search(pattern, normalized, flags=re.I) for pattern in patterns):
            return label
    return "Other AI developments"


def classification(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("classification")
    return value if isinstance(value, dict) else {}


def ai_relevant(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if classification(row).get("ai_relevant") is True]


def title_for(row: dict[str, Any], *, lens: str) -> str:
    if lens == "coverage":
        return str(row.get("headline_english") or row.get("headline_original") or "")
    return str(row.get("event_title") or row.get("event_summary") or "")


def theme_distribution(rows: list[dict[str, Any]], *, lens: str) -> list[dict[str, Any]]:
    counts = Counter()
    for row in rows:
        cls = classification(row)
        counts[theme_for(title_for(row, lens=lens), cls.get("topic"))] += 1
    total = sum(counts.values())
    return [
        {
            "label": label,
            "count": int(count),
            "share": round(count / total, 4) if total else 0.0,
        }
        for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def simple_distribution(rows: list[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    counts = Counter(str(classification(row).get(field) or "unclear") for row in rows)
    total = sum(counts.values())
    return {
        key: {
            "count": int(count),
            "share": round(count / total, 4) if total else 0.0,
        }
        for key, count in sorted(counts.items())
    }


def discovery_markets(coverage_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    market_articles: dict[str, set[str]] = defaultdict(set)
    market_languages: dict[str, set[str]] = defaultdict(set)
    market_publishers: dict[str, Counter[str]] = defaultdict(Counter)

    for row in coverage_rows:
        article_id = str(row.get("article_id") or "").strip()
        if not article_id:
            continue
        publisher = str(row.get("publisher") or "Unknown source")
        languages = [str(value) for value in (row.get("search_languages") or []) if value]
        for market in [str(value) for value in (row.get("search_markets") or []) if value]:
            market_articles[market].add(article_id)
            market_publishers[market][publisher] += 1
            market_languages[market].update(languages)

    result = []
    for iso3 in sorted(market_articles, key=lambda code: (-len(market_articles[code]), code)):
        publishers = market_publishers[iso3]
        result.append(
            {
                "country_iso3": iso3,
                "name": MARKET_NAMES.get(iso3, iso3),
                "article_count": len(market_articles[iso3]),
                "unique_publishers": len(publishers),
                "languages": sorted(market_languages[iso3]),
                "top_publishers": [
                    {"publisher": publisher, "article_count": int(count)}
                    for publisher, count in publishers.most_common(5)
                ],
                "note": (
                    "Discovery-market counts can overlap because the same article may "
                    "appear in more than one search market."
                ),
            }
        )
    return result


def source_inventory(coverage_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    article_counts: Counter[str] = Counter()
    event_sets: dict[str, set[str]] = defaultdict(set)
    for row in coverage_rows:
        publisher = str(row.get("publisher") or "Unknown source")
        article_counts[publisher] += 1
        event_id = str(row.get("effective_event_id") or row.get("event_id") or "").strip()
        if event_id:
            event_sets[publisher].add(event_id)

    rows = [
        {
            "publisher": publisher,
            "article_count": int(article_counts[publisher]),
            "unique_event_count": len(event_sets[publisher]),
        }
        for publisher in article_counts
    ]
    rows.sort(key=lambda row: (-row["article_count"], -row["unique_event_count"], row["publisher"].casefold()))
    return rows


def history_from_release_index(index: dict[str, Any]) -> dict[str, Any]:
    points = []
    for row in index.get("weekly") or []:
        articles = int(row.get("articles") or row.get("ai_relevant_articles") or 0)
        events = int(row.get("event_records") or row.get("ai_relevant_event_records") or 0)
        points.append(
            {
                "release_id": row.get("release_id"),
                "revision": int(row.get("revision") or 1),
                "window_start": row.get("period_start"),
                "window_end": row.get("period_end"),
                "coverage_count": articles,
                "event_count": events,
                "extra_article_instances": max(0, articles - events),
                "coverage_index": row.get("coverage_index"),
                "event_index": row.get("event_index"),
                "amplification_gap": row.get("amplification_gap"),
                "coverage_event_ratio": round(articles / events, 4) if events else None,
            }
        )
    points.sort(key=lambda row: (str(row.get("window_end") or ""), str(row.get("release_id") or "")))
    return {
        "meta": {
            "series": "weekly_public_releases",
            "cumulative": False,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source": "/data/releases/index.json",
            "note": (
                "Each point is one standardized weekly Observatory release. "
                "Counts are not cumulatively summed."
            ),
        },
        "points": points,
    }


def main() -> int:
    release = load_json(RELEASE_PATH)
    index = load_json(RELEASE_INDEX_PATH)

    release_id = str(release.get("release_id") or "").strip()
    if not release_id:
        raise InsightError("Current release has no release_id.")
    if str(index.get("current_release_id") or "") != release_id:
        raise InsightError("Release index does not point to the current release.")

    units = release.get("units") or {}
    coverage_rows = ai_relevant(list(units.get("coverage_articles") or []))
    event_rows = ai_relevant(list(units.get("event_records") or []))

    expected_coverage = int((release.get("counts") or {}).get("ai_relevant_articles") or 0)
    expected_events = int((release.get("counts") or {}).get("ai_relevant_event_records") or 0)
    if len(coverage_rows) != expected_coverage or len(event_rows) != expected_events:
        raise InsightError(
            "Canonical release unit counts do not reconcile: "
            f"coverage {len(coverage_rows)}/{expected_coverage}, "
            f"events {len(event_rows)}/{expected_events}."
        )

    non_emp_coverage = [row for row in coverage_rows if classification(row).get("empowerment_status") == "non_empowerment"]
    non_emp_event = [row for row in event_rows if classification(row).get("empowerment_status") == "non_empowerment"]
    sources = source_inventory(coverage_rows)

    insights = {
        "meta": {
            "version": "public_insights_v4_release_bound",
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "release_id": release_id,
            "release_revision": int(release.get("revision") or 1),
            "classification_run_id": (release.get("lineage") or {}).get("classification_run_id"),
            "collection_run_id": (release.get("lineage") or {}).get("collection_run_id"),
            "observation_start": release.get("period_start"),
            "observation_end": release.get("period_end"),
            "source_of_truth": "/data/releases/current.json",
            "coverage_units": expected_coverage,
            "event_units": expected_events,
            "extra_coverage": int((release.get("counts") or {}).get("extra_coverage") or 0),
            "first_time_event_records": int((release.get("counts") or {}).get("first_time_event_records") or 0),
            "follow_on_event_records": int((release.get("counts") or {}).get("follow_on_event_records") or 0),
            "recurring_event_records": int((release.get("counts") or {}).get("recurring_event_records") or 0),
            "source_method": (
                "Publications and organisations are dynamically observed through the "
                "current discovery workflow; this is not a fixed journal whitelist."
            ),
            "theme_method": (
                "Descriptive themes are a separate navigation layer. They do not affect "
                "the empowerment index."
            ),
        },
        "discovery_markets": discovery_markets(coverage_rows),
        "sources": {
            "unique_publishers": len(sources),
            "rows": sources,
        },
        "coverage": {
            "themes": theme_distribution(coverage_rows, lens="coverage"),
            "by_status": simple_distribution(coverage_rows, "empowerment_status"),
            "by_narrative": simple_distribution(coverage_rows, "narrative_frame"),
        },
        "event": {
            "themes": theme_distribution(event_rows, lens="event"),
            "by_status": simple_distribution(event_rows, "empowerment_status"),
            "by_narrative": simple_distribution(event_rows, "narrative_frame"),
        },
        "non_empowerment": {
            "coverage": {
                "unit_count": len(non_emp_coverage),
                "themes": theme_distribution(non_emp_coverage, lens="coverage"),
            },
            "event": {
                "unit_count": len(non_emp_event),
                "themes": theme_distribution(non_emp_event, lens="event"),
            },
        },
    }

    history = history_from_release_index(index)
    write_json(INSIGHTS_PATH, insights)
    write_json(HISTORY_PATH, history)

    print(
        json.dumps(
            {
                "release_id": release_id,
                "coverage_units": len(coverage_rows),
                "event_units": len(event_rows),
                "unique_sources": len(sources),
                "discovery_markets": len(insights["discovery_markets"]),
                "history_points": len(history["points"]),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InsightError as exc:
        import sys

        print(f"Public insights failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
