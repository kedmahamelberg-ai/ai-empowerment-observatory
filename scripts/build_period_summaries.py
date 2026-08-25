#!/usr/bin/env python3
"""Build deduplicated monthly, quarterly and annual AIEO summaries.

Weekly releases remain the immutable measurement units. This script composes
those releases without summing event counts blindly:

- the Reality view deduplicates by effective event ID across the period;
- the Attention view retains article volume and recurring coverage episodes;
- launch baselines are excluded because they overlap the weekly series;
- every summary states the observation window, historical matching pool,
  reconciliation status and latest included weekly revision.

Run this after every weekly release. Re-running it is deterministic and safe.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from release_common import ROOT, WEEKLY_DIR, load_json, parse_date, stable_hash, write_json

RELEASE_ROOT = ROOT / "data" / "releases"
MONTHLY_DIR = RELEASE_ROOT / "monthly"
QUARTERLY_DIR = RELEASE_ROOT / "quarterly"
ANNUAL_DIR = RELEASE_ROOT / "annual"
PERIOD_INDEX = RELEASE_ROOT / "period-index.json"

SCHEMA_VERSION = "aieo_period_summary_v1.1"
INDEX_SCHEMA_VERSION = "aieo_period_index_v1.1"


class PeriodSummaryError(RuntimeError):
    """Raised when period summaries cannot be constructed safely."""


@dataclass(frozen=True)
class PeriodSpec:
    period_id: str
    period_type: str
    start: date
    end: date

    @property
    def closed(self) -> bool:
        return self.end < datetime.now(timezone.utc).date()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def list_weekly_releases() -> list[dict[str, Any]]:
    releases: list[dict[str, Any]] = []
    if not WEEKLY_DIR.exists():
        return releases
    for path in sorted(WEEKLY_DIR.glob("*.json")):
        if not path.is_file():
            continue
        item = load_json(path)
        if not isinstance(item, dict) or item.get("release_type") != "weekly":
            continue
        if not item.get("period_start") or not item.get("period_end"):
            continue
        item["_source_path"] = str(path.relative_to(ROOT))
        releases.append(item)
    releases.sort(key=lambda row: (str(row["period_end"]), str(row["release_id"])))
    return releases


def month_spec(value: date) -> PeriodSpec:
    start = value.replace(day=1)
    next_month = date(value.year + 1, 1, 1) if value.month == 12 else date(value.year, value.month + 1, 1)
    return PeriodSpec(start.strftime("%Y-%m"), "monthly", start, next_month - timedelta(days=1))


def quarter_spec(value: date) -> PeriodSpec:
    quarter = (value.month - 1) // 3 + 1
    start_month = (quarter - 1) * 3 + 1
    start = date(value.year, start_month, 1)
    next_quarter = date(value.year + 1, 1, 1) if quarter == 4 else date(value.year, start_month + 3, 1)
    return PeriodSpec(f"{value.year}-Q{quarter}", "quarterly", start, next_quarter - timedelta(days=1))


def annual_spec(value: date) -> PeriodSpec:
    return PeriodSpec(str(value.year), "annual", date(value.year, 1, 1), date(value.year, 12, 31))


def release_end(release: dict[str, Any]) -> date:
    parsed = parse_date(release.get("period_end"))
    if parsed is None:
        raise PeriodSummaryError(f"Invalid period_end in {release.get('release_id')}")
    return parsed


def in_spec(release: dict[str, Any], spec: PeriodSpec) -> bool:
    # A standardized week belongs to the period containing its Sunday end date.
    return spec.start <= release_end(release) <= spec.end


def effective_event_id(row: dict[str, Any]) -> str:
    return str(row.get("effective_event_id") or row.get("event_id") or "")


def ai_relevant(row: dict[str, Any]) -> bool:
    return bool((row.get("classification") or {}).get("ai_relevant"))


def numeric(values: Iterable[Any]) -> list[float]:
    result: list[float] = []
    for value in values:
        try:
            result.append(float(value))
        except (TypeError, ValueError):
            continue
    return result


def percentile(values: list[float], proportion: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    index = (len(ordered) - 1) * proportion
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    fraction = index - low
    return round(ordered[low] + (ordered[high] - ordered[low]) * fraction, 2)


def period_story(reality: dict[str, Any], attention: dict[str, Any]) -> str:
    new_developments = int(reality.get("new_developments") or 0)
    recurring = int(attention.get("recurring_event_appearances") or 0)
    resurfaced = int(attention.get("resurfaced_event_appearances") or 0)
    articles = int(attention.get("published_coverage_articles") or 0)
    sentence = (
        f"AIEO observed {articles} newly published AI-news articles. "
        f"They represented {new_developments} new developments"
    )
    if recurring:
        sentence += f" and {recurring} appearances of developments already in memory"
    sentence += "."
    if resurfaced:
        sentence += f" {resurfaced} events resurfaced after at least four weeks without observed coverage."
    return sentence


def aggregate(spec: PeriodSpec, releases: list[dict[str, Any]]) -> dict[str, Any]:
    included = [release for release in releases if in_spec(release, spec)]
    if not included:
        raise PeriodSummaryError(f"No weekly releases fall inside {spec.period_id}")

    article_rows: dict[str, dict[str, Any]] = {}
    event_rows: dict[str, dict[str, Any]] = {}
    story_ids: set[str] = set()
    publications: set[str] = set()
    domains: set[str] = set()
    topic_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    first_time_ids: set[str] = set()
    follow_on_ids: set[str] = set()

    recurring_appearances = 0
    resurfaced_appearances = 0
    rediscovered_articles = 0
    same_event_new_coverage_articles = 0
    coverage_episode_count = 0
    lag_values: list[float] = []
    rediscovery_lag_values: list[float] = []
    pool_starts: list[str] = []
    pool_through: list[str] = []
    reconciliation_times: list[str] = []

    for release in included:
        units = release.get("units") or {}
        for row in units.get("coverage_articles") or []:
            if not ai_relevant(row):
                continue
            article_id = str(row.get("article_id") or "")
            if article_id:
                article_rows.setdefault(article_id, row)
            if row.get("publisher"):
                publications.add(str(row["publisher"]))
            url = str(row.get("url") or "")
            if "://" in url:
                domain = url.split("://", 1)[1].split("/", 1)[0].lower().removeprefix("www.")
                if domain:
                    domains.add(domain)

        for row in units.get("event_records") or []:
            if not ai_relevant(row):
                continue
            event_id = effective_event_id(row)
            if not event_id:
                continue
            previous = event_rows.setdefault(event_id, row)
            if len(row.get("sources") or []) > len(previous.get("sources") or []):
                event_rows[event_id] = row
            story_id = row.get("story_family_id")
            if story_id:
                story_ids.add(str(story_id))
            novelty = str(row.get("novelty_status") or "")
            if (
                novelty == "first_time"
                or bool(row.get("first_time_in_period"))
                or (not novelty and bool(row.get("new_in_period")))
            ):
                first_time_ids.add(event_id)
            if novelty == "follow_on_development" or bool(row.get("follow_on_development")):
                follow_on_ids.add(event_id)

        dynamics = release.get("dynamics") or {}
        recurring_appearances += int(
            dynamics.get("recurring_event_appearances")
            or (release.get("counts") or {}).get("recurring_event_records")
            or 0
        )
        resurfaced_appearances += int(dynamics.get("resurfaced_event_appearances") or 0)
        rediscovered_articles += int(dynamics.get("rediscovered_article_records") or 0)
        same_event_new_coverage_articles += int(dynamics.get("same_event_new_coverage_articles") or 0)
        coverage_episode_count += int(dynamics.get("coverage_episode_count") or 0)
        lag_values.extend(numeric((dynamics.get("replication_lag_days") or {}).get("values") or []))
        rediscovery_lag_values.extend(
            numeric((dynamics.get("article_rediscovery_lag_days") or {}).get("values") or [])
        )

        pool = release.get("historical_pool") or {}
        if pool.get("starts_at"):
            pool_starts.append(str(pool["starts_at"]))
        if pool.get("considered_through"):
            pool_through.append(str(pool["considered_through"]))
        reconciliation = release.get("reconciliation") or {}
        if reconciliation.get("completed_at"):
            reconciliation_times.append(str(reconciliation["completed_at"]))

    for row in event_rows.values():
        classification = row.get("classification") or {}
        topic_counts[str(classification.get("topic") or "other")] += 1
        status_counts[str(classification.get("empowerment_status") or "unclear")] += 1

    new_development_ids = first_time_ids | follow_on_ids
    reality = {
        "distinct_event_records": len(event_rows),
        "new_developments": len(new_development_ids),
        "first_time_event_records": len(first_time_ids),
        "follow_on_developments": len(follow_on_ids),
        "active_story_families": len(story_ids),
    }
    attention = {
        "published_coverage_articles": len(article_rows),
        "coverage_episode_count": coverage_episode_count or len(article_rows),
        "recurring_event_appearances": recurring_appearances,
        "resurfaced_event_appearances": resurfaced_appearances,
        "same_event_new_coverage_articles": same_event_new_coverage_articles,
        "rediscovered_article_records": rediscovered_articles,
        "unique_publications": len(publications),
        "unique_domains": len(domains),
    }
    lag_summary = {
        "observed_recurrence_count": len(lag_values),
        "median_days": round(median(lag_values), 2) if lag_values else None,
        "p75_days": percentile(lag_values, 0.75),
        "p90_days": percentile(lag_values, 0.90),
    }
    rediscovery_lag_summary = {
        "observed_rediscovery_count": len(rediscovery_lag_values),
        "median_days": round(median(rediscovery_lag_values), 2) if rediscovery_lag_values else None,
        "p75_days": percentile(rediscovery_lag_values, 0.75),
        "p90_days": percentile(rediscovery_lag_values, 0.90),
    }
    revisions = {str(release["release_id"]): int(release.get("revision") or 1) for release in included}
    result = {
        "schema_version": SCHEMA_VERSION,
        "period_id": spec.period_id,
        "period_type": spec.period_type,
        "status": "complete" if spec.closed else "accumulating",
        "period_start": spec.start.isoformat(),
        "period_end": spec.end.isoformat(),
        "observed_week_start": min(str(release["period_start"]) for release in included),
        "observed_week_end": max(str(release["period_end"]) for release in included),
        "generated_at": utc_now_iso(),
        "revision": 1,
        "weekly_release_ids": [str(release["release_id"]) for release in included],
        "weekly_revisions": revisions,
        "reality": reality,
        "attention": attention,
        "replication_delay": lag_summary,
        "article_rediscovery_delay": rediscovery_lag_summary,
        "distributions": {
            "topics": dict(topic_counts.most_common()),
            "empowerment_status": dict(status_counts.most_common()),
        },
        "historical_pool": {
            "starts_at": min(pool_starts) if pool_starts else None,
            "considered_through": max(pool_through) if pool_through else None,
            "scope": "All prior event records available to the included reconciliations.",
        },
        "reconciliation": {
            "current_through": max(reconciliation_times) if reconciliation_times else None,
            "weekly_revisions_included": revisions,
            "revision_policy": "Original weekly revisions remain accessible; this summary uses the latest accepted restatement.",
        },
        "story": period_story(reality, attention),
        "disclosure": (
            "Distinct events are deduplicated across the full period by effective event ID. "
            "Recurring appearances remain in the Attention view and are not counted as new developments."
        ),
    }
    return result


def substantive_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in summary.items()
        if key not in {
            "generated_at", "revision", "revision_reason", "revision_history",
            "previous_revision_path", "content_sha256",
        }
    }


def period_change_reason(previous: dict[str, Any], current: dict[str, Any]) -> str:
    previous_ids = list(previous.get("weekly_release_ids") or [])
    current_ids = list(current.get("weekly_release_ids") or [])
    if previous_ids != current_ids:
        return "A newly completed weekly release was added to this period."
    if previous.get("weekly_revisions") != current.get("weekly_revisions"):
        return "An included weekly release was restated after longitudinal reconciliation."
    return "Accepted reconciliation changed the period-level event or attention history."


def write_summary(summary: dict[str, Any]) -> Path:
    directory = {
        "monthly": MONTHLY_DIR,
        "quarterly": QUARTERLY_DIR,
        "annual": ANNUAL_DIR,
    }[str(summary["period_type"])]
    path = directory / f"{summary['period_id']}.json"
    previous = load_json(path)
    generated_at = utc_now_iso()
    summary["generated_at"] = generated_at

    if isinstance(previous, dict):
        previous_hash = stable_hash(substantive_summary(previous))
        current_hash = stable_hash(substantive_summary(summary))
        if previous_hash == current_hash:
            # Keep an unchanged summary byte-for-byte stable.
            return path

        old_revision = int(previous.get("revision") or 1)
        new_revision = old_revision + 1
        archive = directory / "archive" / str(summary["period_id"]) / f"revision-{old_revision}.json"
        if not archive.exists():
            write_json(archive, previous)
        reason = period_change_reason(previous, summary)
        history = list(previous.get("revision_history") or [])
        history.append({
            "revision": new_revision,
            "changed_at": generated_at,
            "reason": reason,
            "weekly_revisions": summary.get("weekly_revisions"),
        })
        summary.update({
            "revision": new_revision,
            "revision_reason": reason,
            "revision_history": history,
            "previous_revision_path": "/" + str(archive.relative_to(ROOT)).replace("\\", "/"),
        })
    else:
        summary["revision"] = 1
        summary["revision_history"] = []

    summary["content_sha256"] = stable_hash(substantive_summary(summary))
    write_json(path, summary)
    return path


def index_row(summary: dict[str, Any], path: Path) -> dict[str, Any]:
    return {
        "period_id": summary["period_id"],
        "period_type": summary["period_type"],
        "status": summary["status"],
        "period_start": summary["period_start"],
        "period_end": summary["period_end"],
        "observed_week_start": summary["observed_week_start"],
        "observed_week_end": summary["observed_week_end"],
        "generated_at": summary["generated_at"],
        "revision": int(summary.get("revision") or 1),
        "revision_reason": summary.get("revision_reason"),
        "previous_revision_path": summary.get("previous_revision_path"),
        "path": "/" + str(path.relative_to(ROOT)).replace("\\", "/"),
        "articles": summary["attention"]["published_coverage_articles"],
        "distinct_event_records": summary["reality"]["distinct_event_records"],
        "new_developments": summary["reality"]["new_developments"],
        "first_time_event_records": summary["reality"]["first_time_event_records"],
        "follow_on_developments": summary["reality"]["follow_on_developments"],
        "recurring_event_appearances": summary["attention"]["recurring_event_appearances"],
        "resurfaced_event_appearances": summary["attention"]["resurfaced_event_appearances"],
        "content_sha256": summary["content_sha256"],
    }


def build_all(releases: list[dict[str, Any]]) -> dict[str, Any]:
    if not releases:
        raise PeriodSummaryError("No standardized weekly releases are available.")
    specs: dict[tuple[str, str], PeriodSpec] = {}
    for release in releases:
        end = release_end(release)
        for spec in (month_spec(end), quarter_spec(end), annual_spec(end)):
            specs[(spec.period_type, spec.period_id)] = spec

    rows: list[dict[str, Any]] = []
    for _, spec in sorted(specs.items(), key=lambda item: (item[0][0], item[0][1])):
        summary = aggregate(spec, releases)
        path = write_summary(summary)
        written = load_json(path)
        if not isinstance(written, dict):
            raise PeriodSummaryError(f"Could not read generated summary: {path}")
        rows.append(index_row(written, path))

    current: dict[str, str | None] = {}
    for period_type in ("monthly", "quarterly", "annual"):
        candidates = sorted(
            (row for row in rows if row["period_type"] == period_type),
            key=lambda row: (row["period_end"], row["period_id"]),
        )
        current[period_type] = candidates[-1]["period_id"] if candidates else None

    index_generated_at = max(
        (str(row.get("generated_at") or "") for row in rows),
        default=utc_now_iso(),
    )
    index = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "generated_at": index_generated_at,
        "current": current,
        "summaries": rows,
        "composition_rule": {
            "reality": "Deduplicate across weekly releases by effective event ID.",
            "attention": "Retain article volume, event appearances, rediscoveries and resurfacing.",
            "baseline": "Overlapping launch baselines are excluded from standardized period totals.",
        },
    }
    index["content_sha256"] = stable_hash({key: value for key, value in index.items() if key != "content_sha256"})
    write_json(PERIOD_INDEX, index)
    return index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", help="Optional period ID (YYYY-MM, YYYY-Qn or YYYY).")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    index = build_all(list_weekly_releases())
    output = (
        [row for row in index["summaries"] if row["period_id"] == args.period]
        if args.period else index["summaries"]
    )
    if args.period and not output:
        raise PeriodSummaryError(f"Period {args.period!r} was not generated.")
    print(json.dumps({"generated": output, "index": str(PERIOD_INDEX.relative_to(ROOT))}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PeriodSummaryError as exc:
        print(f"Period summary build failed: {exc}")
        raise SystemExit(1)
