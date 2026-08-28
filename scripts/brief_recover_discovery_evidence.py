#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone

from supabase import create_client

from brief_discovery_evidence_common import (
    canonicalize_url,
    decode_storage_json,
    domain_of,
    iter_news_items,
    normalize_space,
    sha256_text,
    snippet_from_item,
)

BUCKET = os.environ.get("RAW_NEWS_BUCKET", "raw-news")

def utc_now():
    return datetime.now(timezone.utc).isoformat()

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

def load_article_map(client):
    rows = list(paged(
        client,
        "articles",
        "article_id,canonical_url,headline,publisher,source_metadata",
    ))
    by_url = {}
    by_id = {}
    for row in rows:
        canonical = canonicalize_url(row.get("canonical_url"))
        if canonical:
            by_url[canonical] = row
        by_id[str(row["article_id"])] = row
    return by_url, by_id

def merge_source_metadata(client, article_row, snippet, raw_field, evidence_id, raw_storage_path):
    metadata = article_row.get("source_metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    existing = ""
    for key in ("snippet","description","summary","source_snippet"):
        value = normalize_space(metadata.get(key))
        if len(value) > len(existing):
            existing = value

    changed = False
    if len(snippet) > len(existing):
        metadata["snippet"] = snippet
        metadata["snippet_source"] = "serpapi_google_news_raw"
        metadata["snippet_raw_field"] = raw_field
        metadata["snippet_evidence_id"] = evidence_id
        metadata["snippet_raw_storage_path"] = raw_storage_path
        metadata["snippet_recovered_at"] = utc_now()
        changed = True

    if changed:
        (
            client.table("articles")
            .update({"source_metadata": metadata, "updated_at": utc_now()})
            .eq("article_id", article_row["article_id"])
            .execute()
        )
    return changed

def search_rows(client, latest_only=False):
    query = (
        client.table("search_runs")
        .select(
            "search_id,run_id,country_iso3,search_language,"
            "raw_storage_path,status,created_at"
        )
        .eq("status", "success")
        .not_.is_("raw_storage_path", "null")
        .order("created_at")
    )
    rows = query.execute().data or []
    if not latest_only:
        return rows

    runs = (
        client.table("collection_runs")
        .select("run_id,started_at,status")
        .in_("status", ["success","partial"])
        .order("started_at", desc=True)
        .limit(1)
        .execute()
        .data or []
    )
    if not runs:
        return []
    run_id = str(runs[0]["run_id"])
    return [row for row in rows if str(row.get("run_id")) == run_id]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit-searches", type=int, default=0)
    args = parser.parse_args()

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    article_by_url, _ = load_article_map(client)

    counters = Counter()
    fields = Counter()
    searches = search_rows(client, latest_only=args.latest_only)
    if args.limit_searches:
        searches = searches[:args.limit_searches]

    for index, search in enumerate(searches, start=1):
        path = normalize_space(search.get("raw_storage_path"))
        print(f"[search {index}/{len(searches)}] {path}", flush=True)
        if not path:
            counters["missing_raw_path"] += 1
            continue

        try:
            raw = client.storage.from_(BUCKET).download(path)
            payload = decode_storage_json(raw)
        except Exception as exc:
            counters["raw_download_error"] += 1
            print(f"  raw download error: {type(exc).__name__}: {exc}", flush=True)
            continue

        rank = 0
        for item in iter_news_items(payload.get("news_results") or []):
            rank += 1
            link = canonicalize_url(item.get("link"))
            if not link:
                counters["raw_item_no_url"] += 1
                continue

            article = article_by_url.get(link)
            if not article:
                counters["raw_item_unmatched_url"] += 1
                continue

            snippet, raw_field = snippet_from_item(item)
            if not snippet:
                counters["matched_no_snippet"] += 1
                continue

            headline = normalize_space(article.get("headline"))
            if snippet.casefold() == headline.casefold():
                counters["snippet_same_as_headline"] += 1
                continue

            digest = sha256_text(snippet)
            fields[raw_field or "unknown"] += 1
            counters["snippet_candidates"] += 1

            if args.dry_run:
                print(
                    f"  {article['article_id']} {raw_field}: {snippet[:180]}",
                    flush=True,
                )
                continue

            # Insert every unique contextual discovery snippet. The unique constraint
            # makes repeated executions idempotent.
            row = {
                "article_id": article["article_id"],
                "evidence_type": "discovery_snippet",
                "evidence_text": snippet,
                "evidence_language": search.get("search_language"),
                "publisher": article.get("publisher"),
                "source_url": article.get("canonical_url"),
                "source_domain": domain_of(article.get("canonical_url")),
                "collection_run_id": search.get("run_id"),
                "search_id": search.get("search_id"),
                "search_country_iso3": search.get("country_iso3"),
                "search_language": search.get("search_language"),
                "search_rank": rank,
                "raw_storage_path": path,
                "raw_field": raw_field,
                "text_sha256": digest,
                "is_current": True,
            }

            try:
                inserted = (
                    client.table("brief_article_evidence_snapshots")
                    .upsert(
                        row,
                        on_conflict="article_id,evidence_type,text_sha256,search_id",
                    )
                    .select("evidence_id")
                    .execute()
                    .data or []
                )
                evidence_id = str(inserted[0]["evidence_id"]) if inserted else None
                counters["snippet_rows_written"] += 1
                if evidence_id and merge_source_metadata(
                    client,
                    article,
                    snippet,
                    raw_field,
                    evidence_id,
                    path,
                ):
                    counters["article_metadata_enriched"] += 1
            except Exception as exc:
                counters["db_error"] += 1
                print(f"  DB error for {article['article_id']}: {exc}", flush=True)

    summary = {
        "latest_only": args.latest_only,
        "dry_run": args.dry_run,
        "search_runs_examined": len(searches),
        "counts": dict(sorted(counters.items())),
        "snippet_fields": dict(sorted(fields.items())),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as handle:
            handle.write("## AIEO Brief discovery evidence recovery\n\n")
            handle.write("```json\n")
            handle.write(json.dumps(summary, indent=2, ensure_ascii=False))
            handle.write("\n```\n")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
