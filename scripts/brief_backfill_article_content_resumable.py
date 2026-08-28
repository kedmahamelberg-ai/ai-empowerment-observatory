#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict

from supabase import create_client

import brief_backfill_article_content as base
from brief_content_common import article_url

RETRYABLE = {
    "http_error",
    "exception",
    "too_little_extractable_text",
    "robots_unavailable",
}

def paged_rows(client, table, columns, page_size=500):
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

def load_state(client):
    stored = set()
    for row in paged_rows(
        client,
        "brief_article_content_snapshots",
        "article_id,is_current",
    ):
        if row.get("is_current"):
            stored.add(str(row.get("article_id") or ""))

    latest = {}
    for row in paged_rows(
        client,
        "brief_article_fetch_attempts",
        "article_id,outcome,attempted_at",
    ):
        article_id = str(row.get("article_id") or "")
        if not article_id:
            continue
        stamp = str(row.get("attempted_at") or "")
        if article_id not in latest or stamp >= latest[article_id][0]:
            latest[article_id] = (stamp, str(row.get("outcome") or "unknown"))
    return stored, {k: v[1] for k, v in latest.items()}

def should_skip(article_id, stored, latest_outcome, retry_mode):
    if article_id in stored:
        return True, "already_stored"
    prior = latest_outcome.get(article_id)
    if not prior:
        return False, None
    if retry_mode == "none":
        return True, "already_attempted"
    if retry_mode == "retryable" and prior not in RETRYABLE:
        return True, f"terminal_prior_{prior}"
    return False, None

def is_obvious_media(url):
    value = str(url or "").casefold()
    return any(part in value for part in (
        "/player/play/video/",
        "/video/player/",
        "/watch/live/",
    ))

def media_result():
    return {
        "outcome": "non_article_media",
        "metadata_note": "Video/player URL reserved for the later transcript/media pipeline.",
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--retry-mode", choices=["none", "retryable", "all"], default="none")
    parser.add_argument("--max-runtime-minutes", type=int, default=150)
    parser.add_argument("--sleep", type=float, default=0.25)
    args = parser.parse_args()

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    workflow_run_id = os.environ.get("GITHUB_RUN_ID")
    stored, latest_outcome = load_state(client)

    counts = defaultdict(int)
    methods = defaultdict(int)
    processed = 0
    scanned = 0
    soft_stopped = False
    started = time.monotonic()

    for row in paged_rows(client, "articles", "*", page_size=200):
        scanned += 1

        if (time.monotonic() - started) / 60 >= args.max_runtime_minutes:
            soft_stopped = True
            print("Soft runtime stop reached. Run the workflow again to continue.", flush=True)
            break

        article_id = str(row.get("article_id") or row.get("id") or "").strip()
        source_url = article_url(row)
        if not article_id or not source_url:
            counts["missing_id_or_url"] += 1
            continue

        skip, reason = should_skip(article_id, stored, latest_outcome, args.retry_mode)
        if skip:
            counts[f"skipped_{reason}"] += 1
            continue

        if args.limit and processed >= args.limit:
            break

        processed += 1
        print(f"[{processed}] {article_id} {source_url}", flush=True)

        try:
            result = media_result() if is_obvious_media(source_url) else base.fetch_and_extract(source_url)
        except Exception as exc:
            result = {
                "outcome": "exception",
                "error": f"{type(exc).__name__}: {exc}",
            }

        outcome = str(result.get("outcome") or "unknown")
        counts[outcome] += 1
        method = result.get("extraction_method")
        if method:
            methods[str(method)] += 1

        print(
            f"  -> {outcome} ({result.get('word_count','-')} words; {method or '-'})",
            flush=True,
        )

        if not args.dry_run:
            try:
                base.insert_attempt(client, article_id, source_url, result, workflow_run_id)
                latest_outcome[article_id] = outcome
                if outcome == "stored":
                    base.store_snapshot(client, row, source_url, result)
                    stored.add(article_id)
            except Exception as exc:
                counts["db_error"] += 1
                print(f"  DB ERROR: {type(exc).__name__}: {exc}", flush=True)

        time.sleep(max(0.0, args.sleep))

    summary = {
        "processed": processed,
        "scanned": scanned,
        "soft_stopped": soft_stopped,
        "retry_mode": args.retry_mode,
        "counts": dict(sorted(counts.items())),
        "extraction_methods": dict(sorted(methods.items())),
    }
    print(json.dumps(summary, indent=2))

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("## AIEO Brief resumable backfill\n\n")
            handle.write(f"- Processed: **{processed}**\n")
            handle.write(f"- Soft stopped: **{soft_stopped}**\n")
            handle.write(f"- Retry mode: **{args.retry_mode}**\n\n")
            handle.write("```json\n")
            handle.write(json.dumps(summary, indent=2))
            handle.write("\n```\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
