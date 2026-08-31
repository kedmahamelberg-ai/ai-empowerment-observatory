#!/usr/bin/env python3
"""Validate and publish the canonical AIEO weekly release status.

Every public derivative is release-bound. The final Pages deployment is blocked
when a derivative points at a different week, which prevents a new homepage from
silently mixing current and stale numbers.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from release_common import iso_week_id, previous_complete_week

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "data" / "releases" / "current.json"
RELEASE_INDEX = ROOT / "data" / "releases" / "index.json"
PERIOD_INDEX = ROOT / "data" / "releases" / "period-index.json"
INSIGHTS = ROOT / "data" / "insights" / "latest.json"
REPORT_META = ROOT / "data" / "reports" / "latest.json"
HISTORY = ROOT / "data" / "history" / "releases.json"
SYMBIOSIS = ROOT / "data" / "symbiosis" / "current.json"
STATUS = ROOT / "data" / "status" / "latest.json"


class ReleaseError(RuntimeError):
    pass


def load(path: Path, *, optional: bool = False) -> dict[str, Any] | None:
    if not path.exists():
        if optional:
            return None
        raise ReleaseError(f"Required release artifact is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReleaseError(f"Invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ReleaseError(f"Expected a JSON object: {path}")
    return payload


def check_index(value: Any, name: str) -> float:
    if value is None:
        raise ReleaseError(f"{name} is null.")
    number = float(value)
    if not -100 <= number <= 100:
        raise ReleaseError(f"{name} is out of range: {number}")
    return number


def require_release_id(payload: dict[str, Any] | None, release_id: str, label: str) -> None:
    if payload is None:
        raise ReleaseError(f"{label} is missing.")
    candidate = (
        payload.get("release_id")
        or (payload.get("meta") or {}).get("release_id")
        or (payload.get("latest") or {}).get("release_id")
    )
    if str(candidate or "") != release_id:
        raise ReleaseError(
            f"{label} is stale or mismatched: expected {release_id}, got {candidate!r}."
        )


def nearly_equal(left: Any, right: Any, *, tolerance: float = 1e-6) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return False


def validate_period_summaries(period_index: dict[str, Any], period_end: str) -> None:
    summaries = list(period_index.get("summaries") or [])
    if not summaries:
        raise ReleaseError("Period summary index has no summaries.")
    current_map = period_index.get("current") or {}
    for period_type in ("monthly", "quarterly", "annual"):
        period_id = current_map.get(period_type)
        if not period_id:
            raise ReleaseError(f"Period index has no current {period_type} summary.")
        row = next(
            (
                item for item in summaries
                if item.get("period_type") == period_type and item.get("period_id") == period_id
            ),
            None,
        )
        if not row:
            raise ReleaseError(f"Current {period_type} summary row is missing: {period_id}")
        if str(row.get("observed_week_end") or "") != period_end:
            raise ReleaseError(
                f"{period_type} summary is stale: observed_week_end="
                f"{row.get('observed_week_end')!r}, expected {period_end}."
            )
        public_path = str(row.get("path") or "")
        if not public_path.startswith("/data/releases/"):
            raise ReleaseError(f"Invalid {period_type} summary path: {public_path!r}.")
        summary = load(ROOT / public_path.lstrip("/"))
        assert summary
        if str(summary.get("observed_week_end") or "") != period_end:
            raise ReleaseError(f"{period_type} summary file is stale: {public_path}.")
        if str(summary.get("content_sha256") or "") != str(row.get("content_sha256") or ""):
            raise ReleaseError(f"{period_type} summary index hash differs from {public_path}.")


def validate(*, allow_stale: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    release = load(RELEASE)
    index = load(RELEASE_INDEX)
    insights = load(INSIGHTS)
    report_meta = load(REPORT_META)
    history = load(HISTORY)
    symbiosis = load(SYMBIOSIS)
    period_index = load(PERIOD_INDEX)
    assert release and index and insights and report_meta and history and symbiosis and period_index

    release_id = str(release.get("release_id") or "").strip()
    period_start = str(release.get("period_start") or "").strip()
    period_end = str(release.get("period_end") or "").strip()
    if not release_id or not period_start or not period_end:
        raise ReleaseError("Current release is missing release_id or period bounds.")

    expected = previous_complete_week()
    expected_id = iso_week_id(expected)
    if not allow_stale:
        if release_id != expected_id:
            raise ReleaseError(
                f"Public release is stale: expected {expected_id} "
                f"({expected.start} to {expected.end}), got {release_id} "
                f"({period_start} to {period_end})."
            )
        if period_start != expected.start.isoformat() or period_end != expected.end.isoformat():
            raise ReleaseError("Release ID and calendar period do not match the expected completed week.")

    if str(index.get("current_release_id") or "") != release_id:
        raise ReleaseError("Release index does not point to current.json.")
    weekly_path = ROOT / "data" / "releases" / "weekly" / f"{release_id}.json"
    weekly = load(weekly_path)
    assert weekly
    if weekly.get("content_sha256") != release.get("content_sha256"):
        raise ReleaseError("current.json and the versioned weekly release differ.")

    counts = release.get("counts") or {}
    coverage_n = int(counts.get("ai_relevant_articles") or 0)
    event_n = int(counts.get("ai_relevant_event_records") or 0)
    extra = int(counts.get("extra_coverage") or 0)
    if coverage_n <= 0 or event_n <= 0:
        raise ReleaseError(f"Invalid canonical counts: coverage={coverage_n}, events={event_n}")
    if extra != coverage_n - event_n:
        raise ReleaseError("Canonical extra coverage does not reconcile to coverage minus events.")

    novelty_total = sum(
        int(counts.get(key) or 0)
        for key in (
            "first_time_event_records",
            "follow_on_event_records",
            "recurring_event_records",
            "possible_historical_match_event_records",
            "unclassified_novelty_event_records",
        )
    )
    if novelty_total != event_n:
        raise ReleaseError(
            f"Novelty categories ({novelty_total}) do not reconcile to events ({event_n})."
        )
    if not bool((release.get("historical_pool") or {}).get("all_prior_events_considered")):
        raise ReleaseError("Current release lacks accepted longitudinal-history reconciliation.")

    lenses = release.get("lenses") or {}
    coverage = lenses.get("coverage") or {}
    event = lenses.get("event") or {}
    amplification = release.get("amplification") or {}
    coverage_index = check_index(coverage.get("empowerment_index"), "Coverage Empowerment Index")
    event_index = check_index(event.get("empowerment_index"), "Event Empowerment Index")
    gap = amplification.get("directional_gap")
    if gap is None:
        raise ReleaseError("Directional Amplification Gap is null.")
    if int(coverage.get("unit_count_ai_relevant") or 0) != coverage_n:
        raise ReleaseError("Coverage Lens count differs from canonical coverage count.")
    if int(event.get("unit_count_ai_relevant") or 0) != event_n:
        raise ReleaseError("Event Lens count differs from canonical event count.")

    units = release.get("units") or {}
    coverage_rows = [
        row for row in (units.get("coverage_articles") or [])
        if bool((row.get("classification") or {}).get("ai_relevant"))
    ]
    event_rows = [
        row for row in (units.get("event_records") or [])
        if bool((row.get("classification") or {}).get("ai_relevant"))
    ]
    if len(coverage_rows) != coverage_n or len(event_rows) != event_n:
        raise ReleaseError(
            "Canonical release units differ from the published top-line counts: "
            f"coverage={len(coverage_rows)}/{coverage_n}, events={len(event_rows)}/{event_n}."
        )

    current_row = next(
        (row for row in (index.get("weekly") or []) if row.get("release_id") == release_id),
        None,
    )
    if not current_row:
        raise ReleaseError("Release index has no row for current release.")
    if int(current_row.get("articles") or 0) != coverage_n or int(current_row.get("event_records") or 0) != event_n:
        raise ReleaseError("Release index current counts differ from current.json.")

    require_release_id(insights, release_id, "Public insights")
    require_release_id(report_meta, release_id, "Public PDF metadata")
    require_release_id(symbiosis, release_id, "Relationship-lens artifact")

    insight_meta = insights.get("meta") or {}
    if int(insight_meta.get("coverage_units") or 0) != coverage_n or int(insight_meta.get("event_units") or 0) != event_n:
        raise ReleaseError("Public insights counts differ from current.json.")
    if str(insight_meta.get("observation_start") or "") != period_start or str(insight_meta.get("observation_end") or "") != period_end:
        raise ReleaseError("Public insights observation window differs from current.json.")

    for key, expected_value in (("coverage_units", coverage_n), ("event_units", event_n)):
        if int(report_meta.get(key) or 0) != expected_value:
            raise ReleaseError(f"Public PDF metadata {key} differs from current.json.")
    if str(report_meta.get("period_start") or "") != period_start or str(report_meta.get("period_end") or "") != period_end:
        raise ReleaseError("Public PDF metadata period differs from current.json.")
    for key, expected_value in (
        ("coverage_index", coverage_index),
        ("event_index", event_index),
        ("directional_amplification_gap", float(gap)),
    ):
        if not nearly_equal(report_meta.get(key), expected_value, tolerance=1e-4):
            raise ReleaseError(f"Public PDF metadata {key} differs from current.json.")
    pdf_value = str(report_meta.get("file") or "")
    if not pdf_value.startswith("/reports/") or not (ROOT / pdf_value.lstrip("/")).exists():
        raise ReleaseError("Public PDF file referenced by metadata is missing.")

    history_points = list(history.get("points") or [])
    weekly_rows = list(index.get("weekly") or [])
    history_ids = [str(row.get("release_id") or "") for row in history_points]
    index_ids = [str(row.get("release_id") or "") for row in weekly_rows]
    if history_ids != index_ids:
        raise ReleaseError("Saved weekly history does not exactly match the release index.")
    if not history_points or history_points[-1].get("release_id") != release_id:
        raise ReleaseError("Saved weekly history does not end at the current release.")
    latest_history = history_points[-1]
    if int(latest_history.get("coverage_count") or 0) != coverage_n or int(latest_history.get("event_count") or 0) != event_n:
        raise ReleaseError("Saved weekly history current counts differ from current.json.")

    validate_period_summaries(period_index, period_end)

    symbiosis_status = str(symbiosis.get("public_status") or "review_in_progress")
    if symbiosis_status not in {"review_in_progress", "human_reviewed"}:
        raise ReleaseError(f"Unexpected relationship-lens public status: {symbiosis_status}")
    relationship_source_hash = str(symbiosis.get("source_release_sha256") or "")
    if relationship_source_hash and relationship_source_hash != str(release.get("content_sha256") or ""):
        raise ReleaseError("Relationship-lens source release hash differs from current.json.")
    relationship_review = symbiosis.get("review") or {}
    if int((symbiosis.get("event") or {}).get("expected_units") or 0) != event_n:
        raise ReleaseError("Relationship-lens event summary denominator differs from current.json.")
    if int((symbiosis.get("coverage") or {}).get("expected_units") or 0) != coverage_n:
        raise ReleaseError("Relationship-lens coverage summary denominator differs from current.json.")
    if int(relationship_review.get("event_total") or 0) != event_n:
        raise ReleaseError("Relationship-lens event denominator differs from current.json.")
    if int(relationship_review.get("coverage_total") or 0) != coverage_n:
        raise ReleaseError("Relationship-lens coverage denominator differs from current.json.")
    event_reviewed = int(relationship_review.get("event_reviewed") or 0)
    coverage_reviewed = int(relationship_review.get("coverage_reviewed") or 0)
    if not (0 <= event_reviewed <= event_n and 0 <= coverage_reviewed <= coverage_n):
        raise ReleaseError("Relationship-lens review counts are out of range.")
    if symbiosis_status == "human_reviewed" and (event_reviewed != event_n or coverage_reviewed != coverage_n):
        raise ReleaseError("Relationship lens says human_reviewed but review is incomplete.")

    governance = (release.get("reliability") or {}).get("governance") or {}
    audit_status = str(governance.get("audit_status") or "pending").lower()
    release_status = "human_audited" if audit_status in {"complete", "completed", "passed"} else "provisional_automated"
    review_queue_count = int(coverage.get("review_required_count") or 0) + int(event.get("review_required_count") or 0)

    status = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "system_status": "operational",
        "release_id": release_id,
        "release_revision": int(release.get("revision") or 1),
        "period_start": period_start,
        "period_end": period_end,
        "release_status": release_status,
        "release_label": release.get("public_label"),
        "human_audited": release_status == "human_audited",
        "structural_gate": "passed",
        "source_of_truth": "/data/releases/current.json",
        "latest": {
            "release_id": release_id,
            "coverage_units": coverage_n,
            "event_units": event_n,
            "coverage_empowerment_index": coverage_index,
            "event_empowerment_index": event_index,
            "directional_amplification_gap": float(gap),
            "review_queue_count": review_queue_count,
            "relationship_status": symbiosis_status,
        },
        "governance": {
            "weekly_publication_blocks_on": [
                "stale weekly release",
                "mismatched public derivatives",
                "missing or invalid artifacts",
                "unreconciled coverage and event counts",
                "null or out-of-range indices",
                "workflow or storage failure",
            ],
            "weekly_publication_does_not_block_on": [
                "pending stratified classification audit",
                "human relationship review still in progress",
                "ambiguous event merge review disclosed by the release",
                "ordinary model-confidence uncertainty",
            ],
            "human_role": (
                "asynchronous governance, periodic stratified audit, high-risk review "
                "and methodology version approval"
            ),
        },
    }
    return release, status


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate without rewriting data/status/latest.json.",
    )
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="Allow a non-current week. Intended only for local package validation/history repair.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    release, status = validate(allow_stale=args.allow_stale)
    if not args.check_only:
        STATUS.parent.mkdir(parents=True, exist_ok=True)
        STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "release_id": release.get("release_id"),
                "period": [release.get("period_start"), release.get("period_end")],
                "structural_gate": status["structural_gate"],
                "check_only": args.check_only,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseError as exc:
        import sys

        print(f"Release gate failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
