#!/usr/bin/env python3
from __future__ import annotations

import json
import os
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


class InsightError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise InsightError(f"{name} is missing.")
    return value


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise InsightError(f"Missing required public artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def date_range(events_payload: dict[str, Any]) -> tuple[str | None, str | None]:
    values = []

    for event in events_payload.get("events", []):
        if event.get("event_date"):
            values.append(str(event["event_date"])[:10])

        for source in event.get("sources", []):
            if source.get("published_at"):
                values.append(str(source["published_at"])[:10])

    values = sorted(v for v in values if len(v) >= 10)

    return (
        values[0] if values else None,
        values[-1] if values else None,
    )


def batch_in(
    client: Client,
    table: str,
    select: str,
    column: str,
    values: list[str],
    size: int = 100,
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


def distribution(
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
            "count": count,
            "share": round(count / total, 4) if total else 0.0,
        }
        for key, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    }


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
            "data/lenses/latest.json has no classification_run_id."
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

    all_rows = getattr(response, "data", None) or []

    coverage_rows = [
        row for row in all_rows
        if row["lens"] == "coverage" and row["ai_relevant"]
    ]

    event_rows = [
        row for row in all_rows
        if row["lens"] == "event" and row["ai_relevant"]
    ]

    if not coverage_rows or not event_rows:
        raise InsightError(
            "Latest classification run does not contain AI-relevant "
            "Coverage and Event Lens rows."
        )

    non_emp_coverage = [
        row for row in coverage_rows
        if row["empowerment_status"] == "non_empowerment"
    ]
    non_emp_event = [
        row for row in event_rows
        if row["empowerment_status"] == "non_empowerment"
    ]

    coverage_article_ids = [
        str(row["article_id"])
        for row in coverage_rows
        if row.get("article_id")
    ]

    article_rows = batch_in(
        client,
        "articles",
        "article_id,publisher,canonical_url,published_at",
        "article_id",
        coverage_article_ids,
        size=120,
    )

    article_map = {
        str(row["article_id"]): row
        for row in article_rows
    }

    publisher_article_counts = Counter(
        str(article_map[aid].get("publisher") or "Unknown source")
        for aid in coverage_article_ids
        if aid in article_map
    )

    event_ids = [
        str(row["event_id"])
        for row in event_rows
        if row.get("event_id")
    ]

    event_article_rows = batch_in(
        client,
        "event_articles",
        "event_id,article_id",
        "event_id",
        event_ids,
        size=100,
    )

    event_publishers: dict[str, set[str]] = defaultdict(set)

    missing_article_ids = sorted(
        {
            str(row["article_id"])
            for row in event_article_rows
            if str(row["article_id"]) not in article_map
        }
    )

    if missing_article_ids:
        extra_articles = batch_in(
            client,
            "articles",
            "article_id,publisher,canonical_url,published_at",
            "article_id",
            missing_article_ids,
            size=120,
        )
        article_map.update(
            {
                str(row["article_id"]): row
                for row in extra_articles
            }
        )

    for row in event_article_rows:
        event_id = str(row["event_id"])
        article_id = str(row["article_id"])
        publisher = str(
            (article_map.get(article_id) or {}).get("publisher")
            or "Unknown source"
        )
        event_publishers[event_id].add(publisher)

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
            "article_count": int(publisher_article_counts[publisher]),
            "unique_event_count": int(publisher_event_counts[publisher]),
        }
        for publisher in source_names
    ]

    sources.sort(
        key=lambda row: (
            -row["article_count"],
            -row["unique_event_count"],
            row["publisher"].lower(),
        )
    )

    def topic_breakdown(rows):
        counts = Counter(
            str(row.get("topic") or "other")
            for row in rows
        )
        total = sum(counts.values())

        return [
            {
                "topic": topic,
                "label": TOPIC_LABELS.get(topic, topic.replace("_", " ").title()),
                "count": int(count),
                "share": round(count / total, 4) if total else 0.0,
            }
            for topic, count in sorted(
                counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]

    start, end = date_range(events)

    insights = {
        "meta": {
            "generated_at": (
                datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            ),
            "classification_run_id": run_id,
            "observation_start": start,
            "observation_end": end,
            "source_method": (
                "Sources are dynamically observed through the current "
                "news-discovery workflow; this is not a fixed journal whitelist."
            ),
        },
        "sources": {
            "unique_publishers": len(sources),
            "rows": sources,
        },
        "coverage": {
            "by_topic": topic_breakdown(coverage_rows),
            "by_status": distribution(coverage_rows, "empowerment_status"),
            "by_narrative": distribution(coverage_rows, "narrative_frame"),
        },
        "event": {
            "by_topic": topic_breakdown(event_rows),
            "by_status": distribution(event_rows, "empowerment_status"),
            "by_narrative": distribution(event_rows, "narrative_frame"),
        },
        "non_empowerment": {
            "coverage": {
                "unit_count": len(non_emp_coverage),
                "by_topic": topic_breakdown(non_emp_coverage),
                "by_narrative": distribution(
                    non_emp_coverage,
                    "narrative_frame",
                ),
            },
            "event": {
                "unit_count": len(non_emp_event),
                "by_topic": topic_breakdown(non_emp_event),
                "by_narrative": distribution(
                    non_emp_event,
                    "narrative_frame",
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
            coverage.get("unit_count_ai_relevant") or 0
        ),
        "event_count": int(
            event.get("unit_count_ai_relevant") or 0
        ),
        "extra_article_instances": max(
            0,
            int(coverage.get("unit_count_ai_relevant") or 0)
            - int(event.get("unit_count_ai_relevant") or 0),
        ),
        "coverage_index": coverage.get("empowerment_index"),
        "event_index": event.get("empowerment_index"),
        "amplification_gap": amplification.get(
            "directional_amplification_gap"
        ),
        "coverage_event_ratio": amplification.get(
            "coverage_event_ratio"
        ),
        "event_status_distribution": event.get(
            "status_distribution",
            {},
        ),
        "coverage_narrative_distribution": coverage.get(
            "narrative_distribution",
            {},
        ),
        "event_narrative_distribution": event.get(
            "narrative_distribution",
            {},
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
                HISTORY_PATH.read_text(encoding="utf-8")
            )
            if isinstance(existing, dict):
                history.update(existing)
        except Exception:
            pass

    points = [
        p for p in history.get("points", [])
        if p.get("release_id") != run_id
    ]

    points.append(point)
    points.sort(
        key=lambda p: (
            str(p.get("window_end") or ""),
            str(p.get("release_id") or ""),
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
                "unique_publishers": len(sources),
                "coverage_units": len(coverage_rows),
                "event_units": len(event_rows),
                "non_empowerment_events": len(non_emp_event),
                "history_points": len(points),
            },
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
