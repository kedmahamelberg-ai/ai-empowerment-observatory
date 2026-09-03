#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

from supabase import create_client

import brief_backfill_article_content as base
from brief_content_common import article_url

RETRYABLE = {
    "http_error",
    "exception",
    "source_timeout",
    "too_little_extractable_text",
    "robots_unavailable",
}

TERMINAL_PRIOR_OUTCOMES = {
    "blocked_paywall_or_login",
    "blocked_robots",
    "blocked_tdm_reserved",
    "non_article_media",
}

ROOT = Path(__file__).resolve().parents[1]


class SourceDeadlineExceeded(BaseException):
    """Escape extraction helpers that intentionally swallow ordinary errors."""

    pass


@contextmanager
def source_deadline(seconds):
    """Bound every source, including parsing code and robots checks."""

    seconds = float(seconds or 0)
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)

    def raise_timeout(_signum, _frame):
        raise SourceDeadlineExceeded(
            f"Source processing exceeded {seconds:g} seconds"
        )

    signal.signal(signal.SIGALRM, raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)

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

def release_article_ids(release_id):
    candidate = ROOT / "data" / "releases" / "weekly" / f"{release_id}.json"
    if not candidate.exists():
        current = ROOT / "data" / "releases" / "current.json"
        payload = json.loads(current.read_text(encoding="utf-8"))
        if str(payload.get("release_id") or "") != release_id:
            raise SystemExit(f"Could not find release {release_id}")
    else:
        payload = json.loads(candidate.read_text(encoding="utf-8"))

    found = set()
    for row in (payload.get("units") or {}).get("coverage_articles") or []:
        if isinstance(row, dict) and row.get("article_id"):
            found.add(str(row["article_id"]))
    for event in payload.get("evidence") or []:
        if not isinstance(event, dict):
            continue
        for article_id in event.get("member_article_ids") or []:
            if article_id:
                found.add(str(article_id))
        for source in event.get("sources") or []:
            if isinstance(source, dict) and source.get("article_id"):
                found.add(str(source["article_id"]))
    return found

def latest_collection_article_ids(client):
    runs = (
        client.table("collection_runs")
        .select("run_id,started_at,status")
        .in_("status", ["success", "partial"])
        .order("started_at", desc=True)
        .limit(1)
        .execute()
        .data or []
    )
    if not runs:
        raise SystemExit("No successful or partial collection run was found")
    run_id = str(runs[0]["run_id"])
    found = {
        str(row.get("article_id") or "")
        for row in paged_rows_for_run(client, run_id)
        if row.get("article_id")
    }
    return found, run_id

def paged_rows_for_run(client, run_id, page_size=500):
    start = 0
    while True:
        response = (
            client.table("article_observations")
            .select("article_id")
            .eq("run_id", run_id)
            .range(start, start + page_size - 1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            break
        yield from rows
        if len(rows) < page_size:
            break
        start += page_size

def target_scope(client, scope, release_id):
    if scope == "all":
        return None, None
    if scope == "release":
        if not release_id:
            raise SystemExit("--release-id is required when --scope=release")
        return release_article_ids(release_id), f"release:{release_id}"
    article_ids, run_id = latest_collection_article_ids(client)
    return article_ids, f"collection:{run_id}"

def article_rows(client, target_ids):
    if target_ids is None:
        yield from paged_rows(client, "articles", "*", page_size=200)
        return
    ordered = sorted(target_ids)
    for start in range(0, len(ordered), 150):
        rows = (
            client.table("articles")
            .select("*")
            .in_("article_id", ordered[start:start + 150])
            .execute()
            .data or []
        )
        yield from rows

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
    if prior in TERMINAL_PRIOR_OUTCOMES:
        return True, f"terminal_prior_{prior}"
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
    parser.add_argument(
        "--per-source-timeout-seconds",
        type=float,
        default=75,
        help="Hard wall-clock budget for one source, including policy checks and parsing.",
    )
    parser.add_argument("--sleep", type=float, default=0.25)
    parser.add_argument(
        "--scope",
        choices=["all", "latest_collection", "release"],
        default="all",
        help="Limit extraction to the newest collection, one weekly release, or all stored articles.",
    )
    parser.add_argument("--release-id", default="")
    parser.add_argument("--report-output", default="")
    args = parser.parse_args()

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    workflow_run_id = os.environ.get("GITHUB_RUN_ID")
    stored, latest_outcome = load_state(client)
    target_ids, target_label = target_scope(client, args.scope, args.release_id)

    counts = defaultdict(int)
    methods = defaultdict(int)
    processed = 0
    scanned = 0
    soft_stopped = False
    started = time.monotonic()

    for row in article_rows(client, target_ids):
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
            with source_deadline(args.per_source_timeout_seconds):
                result = media_result() if is_obvious_media(source_url) else base.fetch_and_extract(source_url)
        except SourceDeadlineExceeded as exc:
            result = {
                "outcome": "source_timeout",
                "error": str(exc),
            }
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

    stored_after, latest_after = load_state(client)
    target_set = target_ids if target_ids is not None else {
        str(row.get("article_id") or "")
        for row in paged_rows(client, "articles", "article_id")
        if row.get("article_id")
    }
    target_total = len(target_set)
    full_ids = target_set & stored_after
    unresolved = target_set - full_ids
    unresolved_outcomes = defaultdict(int)
    for article_id in unresolved:
        unresolved_outcomes[latest_after.get(article_id, "never_attempted")] += 1

    summary = {
        "schema_version": "aieo_body_collection_report_v1",
        "scope": args.scope,
        "target": target_label or "all_articles",
        "release_id": args.release_id or None,
        "target_articles": target_total,
        "target_articles_with_full_body": len(full_ids),
        "target_articles_without_full_body": len(unresolved),
        "full_body_coverage_rate": round(len(full_ids) / target_total, 6) if target_total else 0.0,
        "unresolved_outcomes": dict(sorted(unresolved_outcomes.items())),
        "processed": processed,
        "scanned": scanned,
        "soft_stopped": soft_stopped,
        "retry_mode": args.retry_mode,
        "per_source_timeout_seconds": args.per_source_timeout_seconds,
        "counts": dict(sorted(counts.items())),
        "extraction_methods": dict(sorted(methods.items())),
    }
    print(json.dumps(summary, indent=2))

    if args.report_output:
        report_path = Path(args.report_output)
        if not report_path.is_absolute():
            report_path = ROOT / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

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
