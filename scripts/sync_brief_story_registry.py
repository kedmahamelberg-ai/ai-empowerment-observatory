#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from collections import Counter
from supabase import create_client

def paged(client, table, columns, page_size=500):
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

def slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")
    return (text[:72].strip("-") or "development")

def main():
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])

    events = list(paged(
        client,
        "events",
        "event_id,event_title,event_summary,event_date,first_seen_at,last_seen_at,"
        "event_state,primary_country_iso3,additional_country_iso3",
    ))
    events = [row for row in events if row.get("event_state") == "active"]

    existing = {
        str(row["event_id"]): row
        for row in paged(client, "brief_stories", "story_id,event_id,slug,status")
        if row.get("event_id")
    }

    counts = Counter()
    for event in events:
        event_id = str(event["event_id"])
        if event_id in existing:
            counts["already_exists"] += 1
            continue

        title = str(event.get("event_title") or "").strip()
        slug = f"{slugify(title)}-{event_id.replace('-','')[:8]}"
        client.table("brief_stories").insert({
            "event_id": event_id,
            "slug": slug,
            "status": "draft",
        }).execute()
        counts["created"] += 1

    result = {
        "active_events": len(events),
        "counts": dict(counts),
    }
    print(json.dumps(result, indent=2))

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## AIEO Brief story registry\n\n")
            handle.write(f"- Active events: **{len(events)}**\n")
            handle.write(f"- New story IDs created: **{counts['created']}**\n")
            handle.write(f"- Already present: **{counts['already_exists']}**\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
