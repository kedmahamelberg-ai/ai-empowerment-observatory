#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import csv
from datetime import datetime, timezone
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
    "tdm_unavailable",
    "browser_error",
    "browser_timeout",
    "browser_data_unavailable",
    "db_error",
    "stored",
}

TERMINAL_PRIOR_OUTCOMES = {
    "blocked_paywall_or_login",
    "blocked_robots",
    "blocked_tdm_reserved",
    "blocked_access_control",
    "blocked_bot_challenge",
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

from source_evidence_quality import assess_body

def scoped_rows(client, table, columns, target_ids=None):
    if target_ids is None:
        yield from paged_rows(client, table, columns)
        return
    ids = sorted(target_ids)
    for offset in range(0, len(ids), 100):
        start = 0
        while True:
            rows = (client.table(table).select(columns).in_("article_id", ids[offset:offset+100])
                .order("article_id").range(start, start+499).execute().data or [])
            yield from rows
            if len(rows) < 500:
                break
            start += 500


def load_state(client, target_ids=None, *, details=False):
    stored, invalid_stored = set(), set()
    for row in scoped_rows(client, "brief_article_content_snapshots",
            "article_id,is_current,body_text,text_sha256,content_basis,paywall_detected", target_ids):
        if row.get("is_current"):
            (stored if assess_body(row)["usable_complete_body"] else invalid_stored).add(str(row.get("article_id") or ""))
    latest = {}
    for row in scoped_rows(client, "brief_article_fetch_attempts",
            "article_id,outcome,attempted_at,retrieval_method,workflow_run_id,metadata", target_ids):
        article_id = str(row.get("article_id") or "")
        if article_id and (article_id not in latest or str(row.get("attempted_at") or "") >= str(latest[article_id].get("attempted_at") or "")):
            latest[article_id] = row
    for article_id in invalid_stored:
        latest.pop(article_id, None)
    outcomes = {key: str(row.get("outcome") or "unknown") for key, row in latest.items()}
    return (stored, outcomes, latest) if details else (stored, outcomes)


def should_skip(article_id, stored, latest_outcome, retry_mode, *, detail=None, session_id="", now=None):
    if article_id in stored:
        return True, "already_stored"
    detail = detail or {}
    metadata = detail.get("metadata") or {}
    if session_id and metadata.get("recovery_session") == session_id:
        return True, "checked_this_recovery"
    prior = latest_outcome.get(article_id)
    if not prior or retry_mode == "all":
        return False, None
    if retry_mode == "none":
        return True, "already_attempted"
    # Old heuristics can misidentify a comment CAPTCHA or footer as a gate.
    # Re-evaluate with the new detector; current publisher restrictions still apply.
    if detail.get("retrieval_method") != base.RECOVERY_STRATEGY_VERSION:
        return False, None
    try:
        stamp = datetime.fromisoformat(str(detail.get("attempted_at", "")).replace("Z", "+00:00"))
        age = ((now or datetime.now(timezone.utc)) - stamp).total_seconds()
    except (ValueError, TypeError):
        age = float("inf")
    cooldown = 7*86400 if prior in TERMINAL_PRIOR_OUTCOMES or prior == "pdf_needs_review" else 6*3600
    if prior == "stored":
        # The attempt ledger alone never proves the snapshot write succeeded.
        return False, None
    return (True, "retry_cooldown") if age < cooldown else (False, None)


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
    parser.add_argument("--session-id", default="")
    args = parser.parse_args()

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    workflow_run_id = os.environ.get("GITHUB_RUN_ID")
    target_ids, target_label = target_scope(client, args.scope, args.release_id)
    stored, latest_outcome, attempt_details = load_state(client, target_ids, details=True)
    stored_before = set(stored)
    rows = list(article_rows(client, target_ids))
    # Oldest/unattempted failures first prevents starvation after a bounded pass.
    rows.sort(key=lambda row: (str(attempt_details.get(str(row.get("article_id")), {}).get("attempted_at") or ""), str(row.get("article_id"))))
    checked = set()

    counts = defaultdict(int)
    methods = defaultdict(int)
    processed = 0
    scanned = 0
    soft_stopped = False
    started = time.monotonic()

    for row in rows:
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

        skip, reason = should_skip(article_id, stored, latest_outcome, args.retry_mode, detail=attempt_details.get(article_id), session_id=args.session_id)
        if skip:
            counts[f"skipped_{reason}"] += 1
            continue

        if args.limit and processed >= args.limit:
            break

        processed += 1
        print(f"[{processed}] {article_id} {source_url}", flush=True)

        try:
            with source_deadline(args.per_source_timeout_seconds):
                result = media_result() if is_obvious_media(source_url) else base.fetch_and_extract(source_url, expected_title=str(row.get("headline_original") or row.get("title") or row.get("headline") or ""))
        except SourceDeadlineExceeded as exc:
            result = {
                "outcome": "source_timeout",
                "error_class": type(exc).__name__,
                "error_message": str(exc),
            }
        except Exception as exc:
            result = base.exception_result(exc)

        outcome = str(result.get("outcome") or "unknown")
        counts[outcome] += 1
        method = result.get("extraction_method")
        if method:
            methods[str(method)] += 1

        print(
            f"  -> {outcome} ({result.get('word_count','-')} evidence units; {method or '-'})",
            flush=True,
        )

        result["recovery_session"] = args.session_id
        checked.add(article_id)
        if not args.dry_run:
            try:
                if outcome == "stored":
                    # Report stored only after the complete snapshot was persisted.
                    base.store_snapshot(client, row, source_url, result)
                    stored.add(article_id)
                base.insert_attempt(client, article_id, source_url, result, workflow_run_id)
                latest_outcome[article_id] = outcome
            except Exception as exc:
                counts["db_error"] += 1
                print(f"  DB ERROR: {type(exc).__name__}: {exc}", flush=True)

        time.sleep(max(0.0, args.sleep))

    stored_after, latest_after, details_after = load_state(client, target_ids, details=True)
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
        "schema_version": "aieo_body_collection_report_v2",
        "strategy": base.RECOVERY_STRATEGY_VERSION,
        "new_bodies_saved": len((stored_after - stored_before) & target_set),
        "remaining_unattempted": len([i for i in unresolved if i not in checked and not should_skip(i, stored_after, latest_after, args.retry_mode, detail=details_after.get(i), session_id=args.session_id)[0]]),
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
    rows_by_id = {str(row.get("article_id")): row for row in rows}
    records = []
    for article_id in sorted(target_set):
        detail = details_after.get(article_id, {})
        meta = detail.get("metadata") or {}
        row = rows_by_id.get(article_id, {})
        records.append({"article_id": article_id, "source_url": article_url(row),
            "title": str(row.get("headline_original") or row.get("title") or row.get("headline") or ""),
            "full_body_available": article_id in full_ids,
            "recovered_this_pass": article_id in (stored_after - stored_before),
            "outcome": "stored" if article_id in full_ids else latest_after.get(article_id, "never_attempted"),
            "attempted_at": detail.get("attempted_at"), "strategy": detail.get("retrieval_method"),
            "final_url": meta.get("final_url"), "extraction_method": meta.get("extraction_method"),
            "error_class": meta.get("error_class"), "error_message": meta.get("error_message"),
            "error_location": meta.get("error_location"),
            "robots_url": meta.get("robots_url"),
            "robots_http_status": meta.get("robots_http_status", (meta.get("robots_detail") or {}).get("http_status")),
            "robots_policy_state": meta.get("robots_policy_state", (meta.get("robots_detail") or {}).get("policy_state")),
            "robots_error_class": meta.get("robots_error_class", (meta.get("robots_detail") or {}).get("error_class")),
            "robots_error_message": meta.get("robots_error_message", (meta.get("robots_detail") or {}).get("error_message")),
            "tdmrep_url": meta.get("tdmrep_url"),
            "tdm_http_status": meta.get("tdm_http_status"),
            "tdm_check_state": meta.get("tdm_check_state"),
            "tdm_error_class": meta.get("tdm_error_class"),
            "tdm_error_message": meta.get("tdm_error_message"),
            "recovery_trace": meta.get("recovery_trace", [])})
    summary["articles"] = records
    print(json.dumps({k: v for k, v in summary.items() if k != "articles"}, indent=2))

    if args.report_output:
        report_path = Path(args.report_output)
        if not report_path.is_absolute():
            report_path = ROOT / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        fields = [key for key in records[0] if key != "recovery_trace"] if records else ["article_id", "outcome"]
        with report_path.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in records:
                # Spreadsheet formula injection is possible in publisher titles/URLs.
                writer.writerow({key: ("'"+value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value) for key, value in row.items()})

    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"new_bodies_saved={summary['new_bodies_saved']}\n")
            handle.write(f"remaining_unattempted={summary['remaining_unattempted']}\n")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("## Article body recovery\n\n")
            handle.write(f"Scope: **{target_label or args.scope}**. **{len(full_ids)} / {target_total}** sources now have usable bodies.\n\n")
            handle.write(f"New bodies saved this pass: **{summary['new_bodies_saved']}**. Still unavailable: **{len(unresolved)}**.\n\n")
            handle.write(f"Sources still awaiting an attempt: **{summary['remaining_unattempted']}**.\n\n")
            handle.write("The artifact lists every source, method and remaining reason. No article text is exposed in the report.\n\n")
            for outcome, count in sorted(unresolved_outcomes.items()):
                handle.write(f"- {outcome}: {count}\n")
    if counts.get("db_error"):
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
