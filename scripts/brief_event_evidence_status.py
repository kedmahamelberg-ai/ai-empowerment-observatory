#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from collections import Counter
from supabase import create_client

def paged(client, table, columns, page_size=1000):
    start = 0
    while True:
        response = client.table(table).select(columns).range(start, start + page_size - 1).execute()
        rows = response.data or []
        if not rows:
            break
        yield from rows
        if len(rows) < page_size:
            break
        start += page_size

def main():
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    rows = list(paged(
        client,
        "brief_event_evidence_readiness",
        "event_id,event_title,source_count,full_source_count,source_excerpt_count,"
        "discovery_snippet_count,headline_only_count,editorial_evidence_level",
    ))

    levels = Counter(str(row.get("editorial_evidence_level") or "unknown") for row in rows)
    total_events = len(rows)
    any_full = sum(1 for row in rows if int(row.get("full_source_count") or 0) > 0)
    multi_full = sum(1 for row in rows if int(row.get("full_source_count") or 0) >= 2)
    headline_only_events = sum(
        1
        for row in rows
        if str(row.get("editorial_evidence_level")) == "headline_only"
    )

    weakest = sorted(
        rows,
        key=lambda row: (
            int(row.get("full_source_count") or 0),
            -int(row.get("headline_only_count") or 0),
            str(row.get("event_title") or ""),
        ),
    )[:20]

    result = {
        "active_events_total": total_events,
        "events_with_at_least_one_full_source": any_full,
        "events_with_two_or_more_full_sources": multi_full,
        "headline_only_events": headline_only_events,
        "editorial_evidence_levels": dict(sorted(levels.items())),
        "weakest_20_events": [
            {
                "event_id": row.get("event_id"),
                "event_title": row.get("event_title"),
                "sources": row.get("source_count"),
                "full_sources": row.get("full_source_count"),
                "headline_only_sources": row.get("headline_only_count"),
                "level": row.get("editorial_evidence_level"),
            }
            for row in weakest
        ],
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## AIEO Brief event evidence readiness\n\n")
            handle.write(f"- Active events: **{total_events}**\n")
            handle.write(f"- Events with at least one full source: **{any_full}**\n")
            handle.write(f"- Events with 2+ full sources: **{multi_full}**\n")
            handle.write(f"- Headline-only events: **{headline_only_events}**\n\n")
            handle.write("```json\n")
            handle.write(json.dumps(result["editorial_evidence_levels"], indent=2))
            handle.write("\n```\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
