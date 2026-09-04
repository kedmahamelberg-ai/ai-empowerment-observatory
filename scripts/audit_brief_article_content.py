#!/usr/bin/env python3
"""Audit stored private article bodies without exposing article text in logs.

The audit uses the same language-neutral evidence-unit counter as collection.
It can inspect the latest collection or one release, so a future weekly run
checks every source in scope rather than an arbitrary English-biased sample.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from supabase import create_client

from brief_content_common import MIN_FULL_BODY_EVIDENCE_UNITS, evidence_unit_count

ROOT = Path(__file__).resolve().parents[1]


def page_rows(client, table, columns, *, page_size=500):
    start = 0
    while True:
        response = client.table(table).select(columns).range(start, start + page_size - 1).execute()
        rows = response.data or []
        if not rows:
            return
        yield from rows
        if len(rows) < page_size:
            return
        start += page_size


def release_article_ids(release_id: str) -> set[str]:
    path = ROOT / "data" / "releases" / "weekly" / f"{release_id}.json"
    if not path.exists():
        current = ROOT / "data" / "releases" / "current.json"
        payload = json.loads(current.read_text(encoding="utf-8"))
        if str(payload.get("release_id") or "") != release_id:
            raise SystemExit(f"Could not find release {release_id}.")
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))

    ids: set[str] = set()
    for row in (payload.get("units") or {}).get("coverage_articles") or []:
        if isinstance(row, dict) and row.get("article_id"):
            ids.add(str(row["article_id"]))
    for event in payload.get("evidence") or []:
        if not isinstance(event, dict):
            continue
        for article_id in event.get("member_article_ids") or []:
            if article_id:
                ids.add(str(article_id))
        for source in event.get("sources") or []:
            if isinstance(source, dict) and source.get("article_id"):
                ids.add(str(source["article_id"]))
    return ids


def page_rows_for_run(client, run_id: str, *, page_size=500):
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
            return
        yield from rows
        if len(rows) < page_size:
            return
        start += page_size


def latest_collection_article_ids(client) -> set[str]:
    response = (
        client.table("collection_runs")
        .select("run_id")
        .in_("status", ["success", "partial"])
        .order("started_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    if not rows:
        raise SystemExit("No successful or partial collection run was found.")
    run_id = str(rows[0]["run_id"])
    return {
        str(row.get("article_id") or "")
        for row in page_rows_for_run(client, run_id)
        if row.get("article_id")
    }


def target_article_ids(client, scope: str, release_id: str) -> set[str] | None:
    if scope == "all":
        return None
    if scope == "release":
        if not release_id:
            raise SystemExit("--release-id is required when --scope=release.")
        return release_article_ids(release_id)
    return latest_collection_article_ids(client)


def snapshots_for_ids(client, article_ids: set[str] | None, limit: int):
    columns = (
        "article_id,source_domain,word_count,extraction_quality,"
        "retrieval_method,retrieved_at,is_current,body_text"
    )
    rows = []
    if article_ids is None:
        for row in page_rows(client, "brief_article_content_snapshots", columns):
            if row.get("is_current"):
                rows.append(row)
                if limit and len(rows) >= limit:
                    break
    else:
        ordered = sorted(article_ids)
        for start in range(0, len(ordered), 150):
            batch = ordered[start : start + 150]
            response = (
                client.table("brief_article_content_snapshots")
                .select(columns)
                .eq("is_current", True)
                .in_("article_id", batch)
                .execute()
            )
            rows.extend(response.data or [])
            if limit and len(rows) >= limit:
                break
    rows.sort(key=lambda row: str(row.get("retrieved_at") or ""), reverse=True)
    return rows[:limit] if limit else rows


def latest_fetch_outcomes(client, article_ids: set[str] | None):
    """Summarise the most recent retrieval result for this audit's scope."""
    columns = "article_id,outcome,attempted_at"
    rows = []
    if article_ids is None:
        rows = list(page_rows(client, "brief_article_fetch_attempts", columns))
    else:
        ordered = sorted(article_ids)
        for start in range(0, len(ordered), 150):
            response = (
                client.table("brief_article_fetch_attempts")
                .select(columns)
                .in_("article_id", ordered[start : start + 150])
                .execute()
            )
            rows.extend(response.data or [])

    newest = {}
    for row in rows:
        article_id = str(row.get("article_id") or "")
        if not article_id:
            continue
        stamp = str(row.get("attempted_at") or "")
        if article_id not in newest or stamp >= str(newest[article_id].get("attempted_at") or ""):
            newest[article_id] = row

    counts = {}
    for row in newest.values():
        outcome = str(row.get("outcome") or "unknown")
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="0 audits every stored body in scope.")
    parser.add_argument(
        "--scope",
        choices=["latest_collection", "release", "all"],
        default="latest_collection",
    )
    parser.add_argument("--release-id", default="")
    args = parser.parse_args()

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    ids = target_article_ids(client, args.scope, args.release_id)
    rows = snapshots_for_ids(client, ids, args.limit)

    issues = []
    audited = []
    for row in rows:
        text = row.get("body_text") or ""
        units = evidence_unit_count(text)
        stored = int(row.get("word_count") or 0)
        if units != stored:
            issues.append(
                {
                    "article_id": row["article_id"],
                    "issue": "evidence_unit_count_mismatch",
                    "stored": stored,
                    "actual": units,
                }
            )
        if units < MIN_FULL_BODY_EVIDENCE_UNITS:
            issues.append(
                {
                    "article_id": row["article_id"],
                    "issue": "too_little_multilingual_evidence",
                    "actual": units,
                }
            )
        audited.append(
            {
                "article_id": row["article_id"],
                "domain": row.get("source_domain"),
                "evidence_units": units,
                "quality": row.get("extraction_quality"),
                "method": row.get("retrieval_method"),
                "retrieved_at": row.get("retrieved_at"),
            }
        )

    stored_ids = {
        str(row.get("article_id") or "")
        for row in rows
        if row.get("article_id")
    }
    outcome_counts = latest_fetch_outcomes(client, ids)

    result = {
        "audit_policy": "multilingual_full_body_evidence_units_v1",
        "scope": args.scope,
        "release_id": args.release_id or None,
        "target_article_count": len(ids) if ids is not None else None,
        "stored_bodies_audited": len(audited),
        "target_articles_without_current_body": (
            len(ids - stored_ids) if ids is not None else None
        ),
        "minimum_evidence_units": MIN_FULL_BODY_EVIDENCE_UNITS,
        "stored_body_metadata": audited,
        "issues": issues,
        "latest_fetch_outcomes_in_scope": outcome_counts,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if issues:
        raise SystemExit("Content quality audit found structural issues.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
