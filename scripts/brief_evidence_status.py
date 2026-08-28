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

    articles = list(paged(client, "articles", "article_id"))
    full = {
        str(r["article_id"])
        for r in paged(
            client,
            "brief_article_content_snapshots",
            "article_id,is_current",
        )
        if r.get("is_current")
    }
    snippets = {
        str(r["article_id"])
        for r in paged(
            client,
            "brief_article_evidence_snapshots",
            "article_id,evidence_type,is_current",
        )
        if r.get("is_current") and r.get("evidence_type") in {"discovery_snippet","source_excerpt"}
    }

    bases = Counter()
    for row in paged(
        client,
        "brief_article_best_evidence",
        "article_id,evidence_basis",
    ):
        bases[str(row.get("evidence_basis") or "unknown")] += 1

    total = len(articles)
    result = {
        "articles_total": total,
        "articles_with_full_source": len(full),
        "articles_with_discovery_or_excerpt": len(snippets),
        "articles_with_any_richer_than_headline": len(full | snippets),
        "best_evidence_distribution": dict(sorted(bases.items())),
    }
    print(json.dumps(result, indent=2))

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## AIEO Brief evidence coverage\n\n")
            for key, value in result.items():
                if key != "best_evidence_distribution":
                    handle.write(f"- {key.replace('_',' ').title()}: **{value}**\n")
            handle.write("\n```json\n")
            handle.write(json.dumps(result["best_evidence_distribution"], indent=2))
            handle.write("\n```\n")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
