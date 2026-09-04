#!/usr/bin/env python3
"""Shared helpers for immutable AIEO weekly and monthly releases.

The public release layer intentionally sits *after* collection, translation,
event resolution and Stage 7C classification. It does not alter model outputs.
It scopes them to a declared period, reconciles all counts, and writes
immutable release files that every public surface can reuse.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
AMSTERDAM = ZoneInfo("Europe/Amsterdam")
EVENT_METHOD = "article_to_event_v1"

WEEKLY_DIR = ROOT / "data" / "releases" / "weekly"
MONTHLY_DIR = ROOT / "data" / "releases" / "monthly"
QUARTERLY_DIR = ROOT / "data" / "releases" / "quarterly"
ANNUAL_DIR = ROOT / "data" / "releases" / "annual"
PERIOD_INDEX = ROOT / "data" / "releases" / "period-index.json"
RECONCILIATION_LATEST = ROOT / "data" / "releases" / "reconciliation" / "latest.json"
RELEASE_INDEX = ROOT / "data" / "releases" / "index.json"
CURRENT_RELEASE = ROOT / "data" / "releases" / "current.json"
REPORT_INDEX = ROOT / "data" / "reports" / "index.json"
SCHEDULE_PATH = ROOT / "data" / "reports" / "schedule.json"
SOURCE_STRATA_PATH = ROOT / "config" / "source-strata.json"
GOVERNANCE_PATH = ROOT / "validation" / "release-governance.json"

VALID_STATUS = [
    "expanding",
    "contracting",
    "mixed",
    "non_empowerment",
    "unclear",
]
VALID_FRAME = [
    "opportunity",
    "threat",
    "contested",
    "descriptive_neutral",
    "unclear",
]
VALID_DIMENSIONS = ["operational", "creative", "agentic", "normative"]
VALID_TOPIC = [
    "work_employment",
    "business_productivity",
    "consumer_services",
    "creativity_ip",
    "education_research",
    "healthcare",
    "government_regulation",
    "privacy_security",
    "infrastructure_investment",
    "other",
]


class ReleaseError(RuntimeError):
    """Public release construction or validation failed."""


@dataclass(frozen=True)
class Period:
    start: date
    end: date

    def contains(self, value: date) -> bool:
        return self.start <= value <= self.end

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def iso(self) -> dict[str, str]:
        return {
            "period_start": self.start.isoformat(),
            "period_end": self.end.isoformat(),
        }


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise ReleaseError(f"{name} is missing.")
    return value


def supabase_admin() -> Any:
    url = required_env("SUPABASE_URL")
    key = str(
        os.environ.get("SUPABASE_SECRET_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or ""
    ).strip()
    if not key:
        raise ReleaseError(
            "SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY is missing."
        )
    try:
        from supabase import create_client
    except (ImportError, AttributeError) as exc:
        raise ReleaseError(
            "The Supabase Python client is required for database-backed release operations."
        ) from exc
    return create_client(url, key)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_date(value: Any) -> date | None:
    parsed = parse_datetime(value)
    if parsed is not None:
        return parsed.astimezone(AMSTERDAM).date()
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text[:10]) if text else None
    except ValueError:
        return None


def date_for_article(row: dict[str, Any]) -> date | None:
    return (
        parse_date(row.get("published_at"))
        or parse_date(row.get("first_seen_at"))
        or parse_date(row.get("last_seen_at"))
    )


def previous_complete_week(as_of: date | None = None) -> Period:
    """Return the most recent *fully completed* Monday-Sunday week.

    The end date is always a Sunday strictly before ``as_of``. This avoids
    accidentally publishing a partial week when the workflow is run manually
    on a Sunday. The intended schedule is Monday morning Amsterdam time.
    """

    current = as_of or datetime.now(AMSTERDAM).date()
    days_back = current.weekday() + 1  # Monday=1 ... Sunday=7
    end = current - timedelta(days=days_back)
    return Period(start=end - timedelta(days=6), end=end)


def calendar_month(month_text: str, *, launch_start: date | None = None) -> Period:
    match = re.fullmatch(r"(\d{4})-(\d{2})", month_text.strip())
    if not match:
        raise ReleaseError("Month must use YYYY-MM format.")
    year, month = map(int, match.groups())
    start = date(year, month, 1)
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    end = next_month - timedelta(days=1)
    if launch_start and start <= launch_start <= end:
        start = launch_start
    return Period(start=start, end=end)


def iso_week_id(period: Period) -> str:
    iso_year, iso_week, _ = period.end.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def first_row(response: Any, context: str) -> dict[str, Any]:
    data = getattr(response, "data", None)
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict) and data:
        return data
    raise ReleaseError(f"No Supabase row while {context}.")


def chunks(values: Sequence[str], size: int = 100) -> Iterator[list[str]]:
    for start in range(0, len(values), size):
        yield list(values[start : start + size])


def unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value)))


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def share(values: Iterable[str], allowed: Sequence[str]) -> dict[str, float]:
    items = list(values)
    counts = Counter(items)
    total = len(items)
    return {
        key: round(counts.get(key, 0) / total, 6) if total else 0.0
        for key in allowed
    }


def percentage(value: float | int | None) -> float | None:
    return round(float(value) * 100.0, 2) if value is not None else None


def calculate_index(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    ai_rows = [row for row in rows if bool(row.get("ai_relevant"))]
    scored = [row for row in ai_rows if row.get("unit_score") is not None]
    excluded = [row for row in ai_rows if row.get("unit_score") is None]
    not_ai = [row for row in rows if not bool(row.get("ai_relevant"))]

    index_value = None
    if scored:
        index_value = round(
            sum(float(row.get("unit_score") or 0.0) for row in scored)
            / len(scored)
            * 100.0,
            4,
        )

    dimension_distribution = {}
    for dimension in VALID_DIMENSIONS:
        dimension_distribution[dimension] = round(
            sum(
                1
                for row in ai_rows
                if bool((row.get("dimensions") or {}).get(dimension, {}).get("present"))
            )
            / len(ai_rows),
            6,
        ) if ai_rows else 0.0

    return {
        "unit_count_total": total,
        "unit_count_ai_relevant": len(ai_rows),
        "unit_count_scored": len(scored),
        "unit_count_excluded_unclear": len(excluded),
        "unit_count_not_ai_relevant": len(not_ai),
        "empowerment_index": index_value,
        "status_distribution": share(
            [str(row.get("empowerment_status") or "unclear") for row in ai_rows],
            VALID_STATUS,
        ),
        "narrative_distribution": share(
            [str(row.get("narrative_frame") or "unclear") for row in ai_rows],
            VALID_FRAME,
        ),
        "topic_distribution": share(
            [str(row.get("topic") or "other") for row in ai_rows],
            VALID_TOPIC,
        ),
        "dimension_distribution": dimension_distribution,
        "review_required_count": sum(bool(row.get("requires_review")) for row in rows),
        "audit_selected_count": sum(bool(row.get("audit_selected")) for row in rows),
    }


def clean_domain(url: str | None) -> str:
    if not url:
        return ""
    try:
        domain = urlparse(url).netloc.lower().split(":", 1)[0]
    except ValueError:
        return ""
    return domain.removeprefix("www.")


def normalize_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def load_source_strata() -> dict[str, Any]:
    return load_json(SOURCE_STRATA_PATH, {"version": "unconfigured", "rules": []})


def source_stratum(
    publisher: str,
    url: str | None,
    config: dict[str, Any] | None = None,
) -> str:
    cfg = config or load_source_strata()
    name = normalize_name(publisher).casefold()
    domain = clean_domain(url)

    for rule in cfg.get("rules", []):
        stratum = str(rule.get("stratum") or "unclassified")
        publishers = [str(item).casefold() for item in rule.get("publisher_patterns", [])]
        domains = [str(item).casefold() for item in rule.get("domain_patterns", [])]
        suffixes = [str(item).casefold() for item in rule.get("domain_suffixes", [])]
        if any(pattern and pattern in name for pattern in publishers):
            return stratum
        if any(pattern and pattern in domain for pattern in domains):
            return stratum
        if any(domain.endswith(suffix) for suffix in suffixes):
            return stratum

    if domain.endswith(".gov") or ".gov." in domain:
        return "primary_official"
    if domain.endswith(".edu") or domain.endswith(".ac.uk"):
        return "primary_official"
    return "unclassified"


def source_summary(articles: Sequence[dict[str, Any]]) -> dict[str, Any]:
    config = load_source_strata()
    publishers: dict[str, dict[str, Any]] = {}
    strata = Counter()
    languages = Counter()
    markets = Counter()

    for article in articles:
        publisher = normalize_name(article.get("publisher")) or "Unknown source"
        item = publishers.setdefault(
            publisher,
            {
                "publisher": publisher,
                "article_ids": set(),
                "event_ids": set(),
                "domains": set(),
                "stratum": source_stratum(
                    publisher,
                    article.get("url"),
                    config,
                ),
            },
        )
        item["article_ids"].add(str(article.get("article_id")))
        if article.get("event_id"):
            item["event_ids"].add(str(article.get("event_id")))
        domain = clean_domain(article.get("url"))
        if domain:
            item["domains"].add(domain)
        strata[item["stratum"]] += 1
        language = str(article.get("source_language") or "unknown").lower()
        languages[language] += 1
        for market in article.get("search_markets") or []:
            markets[str(market)] += 1

    publication_rows = []
    for item in publishers.values():
        publication_rows.append(
            {
                "publisher": item["publisher"],
                "articles": len(item["article_ids"]),
                "event_records": len(item["event_ids"]),
                "domains": sorted(item["domains"]),
                "stratum": item["stratum"],
            }
        )
    publication_rows.sort(key=lambda row: (-row["articles"], row["publisher"].casefold()))

    total = len(articles)
    return {
        "unique_publications": len(publication_rows),
        "unique_domains": len(
            {
                domain
                for row in publication_rows
                for domain in row["domains"]
            }
        ),
        "publications": publication_rows,
        "strata": {
            key: {
                "articles": int(value),
                "share": round(value / total, 6) if total else 0.0,
            }
            for key, value in sorted(strata.items())
        },
        "languages": dict(sorted(languages.items())),
        "discovery_markets": dict(sorted(markets.items())),
        "unclassified_source_share": round(
            strata.get("unclassified", 0) / total,
            6,
        ) if total else 0.0,
        "source_strata_version": config.get("version"),
    }


def governance_for(classification_run_id: str) -> dict[str, Any]:
    data = load_json(GOVERNANCE_PATH, {}) or {}
    runs = data.get("classification_runs", {}) if isinstance(data, dict) else {}
    override = runs.get(classification_run_id, {}) if isinstance(runs, dict) else {}
    status = str(override.get("audit_status") or "pending")
    if status not in {"pending", "complete", "waived_with_disclosure"}:
        status = "pending"
    return {
        "audit_status": status,
        "audit_completed_count": int(override.get("audit_completed_count") or 0),
        "audit_completed_at": override.get("audit_completed_at"),
        "auditor": override.get("auditor"),
        "notes": override.get("notes"),
        "public_label": (
            "Human-audited public release"
            if status == "complete"
            else (
                "Human-governed release; audit exception disclosed"
                if status == "waived_with_disclosure"
                else "Evidence-based pilot release"
            )
        ),
    }


def release_summary(release: dict[str, Any]) -> dict[str, Any]:
    counts = release.get("counts", {})
    coverage = release.get("lenses", {}).get("coverage", {})
    event = release.get("lenses", {}).get("event", {})
    reliability = release.get("reliability", {})
    return {
        "release_id": release["release_id"],
        "release_type": release["release_type"],
        "period_start": release["period_start"],
        "period_end": release["period_end"],
        "generated_at": release["generated_at"],
        "articles": counts.get("ai_relevant_articles", 0),
        "event_records": counts.get("ai_relevant_event_records", 0),
        "revision": int(release.get("revision") or 1),
        "new_event_records": counts.get("new_event_records", 0),
        "first_time_event_records": counts.get("first_time_event_records", counts.get("new_event_records", 0)),
        "recurring_event_records": counts.get("recurring_event_records", 0),
        "resurfaced_event_records": counts.get("resurfaced_event_records", 0),
        "follow_on_event_records": counts.get("follow_on_event_records", 0),
        "possible_historical_match_event_records": counts.get(
            "possible_historical_match_event_records", 0
        ),
        "unclassified_novelty_event_records": counts.get(
            "unclassified_novelty_event_records", 0
        ),
        "rediscovered_article_records": counts.get("rediscovered_article_records", 0),
        "extra_coverage": counts.get("extra_coverage", 0),
        "coverage_index": coverage.get("empowerment_index"),
        "event_index": event.get("empowerment_index"),
        "amplification_gap": release.get("amplification", {}).get("directional_gap"),
        "audit_status": reliability.get("governance", {}).get("audit_status"),
        "validation_status": reliability.get("validation_status"),
    }


def atomic_copy_json(source: Path, destination: Path) -> None:
    value = load_json(source)
    if value is None:
        raise ReleaseError(f"Cannot copy missing JSON file: {source}")
    write_json(destination, value)


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
