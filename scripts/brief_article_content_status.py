#!/usr/bin/env python3
from __future__ import annotations
import json
import os
from collections import Counter
from supabase import create_client

def page(client, table, columns, size=1000):
    start = 0
    while True:
        response = client.table(table).select(columns).range(start, start + size - 1).execute()
        rows = response.data or []
        if not rows:
            break
        yield from rows
        if len(rows) < size:
            break
        start += size

def main():
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])

    article_ids = {str(r.get("article_id") or "") for r in page(client, "articles", "article_id")}
    stored_ids = {
        str(r.get("article_id") or "")
        for r in page(client, "brief_article_content_snapshots", "article_id,is_current")
        if r.get("is_current")
    }
    attempts = list(page(client, "brief_article_fetch_attempts", "article_id,outcome,attempted_at"))
    attempted_ids = {str(r.get("article_id") or "") for r in attempts if r.get("article_id")}
    outcomes = Counter(str(r.get("outcome") or "unknown") for r in attempts)

    result = {
        "observatory_articles": len(article_ids),
        "current_full_text_snapshots": len(stored_ids),
        "articles_with_any_attempt": len(attempted_ids),
        "never_attempted_articles": len(article_ids - attempted_ids - stored_ids),
        "attempt_outcomes_all_time": dict(sorted(outcomes.items())),
    }
    print(json.dumps(result, indent=2))

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## AIEO Brief article-content status\n\n")
            handle.write(f"- Observatory articles: **{result['observatory_articles']}**\n")
            handle.write(f"- Full-text snapshots: **{result['current_full_text_snapshots']}**\n")
            handle.write(f"- Articles attempted: **{result['articles_with_any_attempt']}**\n")
            handle.write(f"- Never attempted: **{result['never_attempted_articles']}**\n\n")
            handle.write("```json\n")
            handle.write(json.dumps(result["attempt_outcomes_all_time"], indent=2))
            handle.write("\n```\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
