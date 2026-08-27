#!/usr/bin/env python3
"""Build a self-contained HTML workbench for reviewing all symbiosis labels."""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supabase import Client, create_client

from symbiosis_common import (
    CODEBOOK_VERSION,
    final_payload_from_classification,
    release_identifier,
    release_review_scope,
)

ROOT = Path(__file__).resolve().parents[1]
RELEASES_DIR = ROOT / "data" / "releases"
TEMPLATE_PATH = ROOT / "review" / "symbiosis" / "index.html"
CSS_PATH = ROOT / "review" / "symbiosis" / "workbench.css"
JS_PATH = ROOT / "review" / "symbiosis" / "workbench.js"
OUTPUT_PATH = ROOT / "review" / "symbiosis" / "workbench.html"
JSON_PATH = ROOT / "review" / "symbiosis" / "workbench-data.json"
TRANSLATION_PROFILE = "validated_language_routing_v3"


class WorkbenchError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise WorkbenchError(f"Missing JSON file: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise WorkbenchError(f"Expected a JSON object: {path}")
    return payload


def historical_release_catalog() -> tuple[list[str], list[dict[str, Any]]]:
    roots = [RELEASES_DIR / "baselines", RELEASES_DIR / "weekly"]
    reviewable_ids: set[str] = set()
    excluded: list[dict[str, Any]] = []
    for root in roots:
        for path in root.glob("*.json"):
            if not path.is_file():
                continue
            payload = read_json(path)
            source_path = str(path.relative_to(ROOT))
            release_id = release_identifier(payload, path)
            scope = release_review_scope(payload, source_path)
            if scope["reviewable"]:
                reviewable_ids.add(release_id)
            else:
                excluded.append(scope)
    return sorted(reviewable_ids), sorted(excluded, key=lambda row: str(row.get("release_id") or ""))


def historical_release_ids() -> list[str]:
    return historical_release_catalog()[0]


def excluded_references_for_scope(scope: str) -> list[dict[str, Any]]:
    if scope == "latest":
        return []
    return historical_release_catalog()[1]


def current_release_id() -> str:
    release_id = str(read_json(RELEASES_DIR / "current.json").get("release_id") or "").strip()
    if not release_id:
        raise WorkbenchError("Current weekly release lacks release_id.")
    return release_id


def release_ids_for_scope(scope: str) -> list[str]:
    all_ids = historical_release_ids()
    current_id = current_release_id()
    if scope == "latest":
        return [current_id]
    if scope == "history":
        return [release_id for release_id in all_ids if release_id != current_id]
    return all_ids


def load_release(release_id: str) -> dict[str, Any]:
    candidates = [
        RELEASES_DIR / "weekly" / f"{release_id}.json",
        RELEASES_DIR / "baselines" / f"{release_id}.json",
    ]
    for path in candidates:
        if path.exists():
            return read_json(path)
    current = read_json(RELEASES_DIR / "current.json")
    if current.get("release_id") == release_id:
        return current
    raise WorkbenchError(f"Could not locate release JSON for {release_id}.")


def release_event_index(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for event in release.get("evidence") or []:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("effective_event_id") or event.get("event_id") or "").strip()
        if event_id:
            result[event_id] = event
    return result


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise WorkbenchError(f"{name} is missing.")
    return value


def paged(client: Client, table: str, select: str, apply=None, page_size: int = 1000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        query = client.table(table).select(select)
        if apply is not None:
            query = apply(query)
        response = query.range(start, start + page_size - 1).execute()
        batch = getattr(response, "data", None) or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def latest_rows(client: Client, *, scope: str) -> list[dict[str, Any]]:
    release_ids = release_ids_for_scope(scope)
    if not release_ids:
        return []

    rows: list[dict[str, Any]] = []
    for start in range(0, len(release_ids), 100):
        rows.extend(
            paged(
                client,
                "symbiosis_classifications",
                "*,symbiosis_classification_runs!inner(status)",
                apply=lambda q, batch=release_ids[start:start + 100]: (
                    q.eq("codebook_version", CODEBOOK_VERSION)
                    .eq("symbiosis_classification_runs.status", "success")
                    .in_("release_id", batch)
                ),
            )
        )

    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        unit_key = str(row.get("unit_key") or "")
        if unit_key:
            latest.setdefault(unit_key, row)
    return sorted(
        latest.values(),
        key=lambda row: (str(row.get("release_id") or ""), str(row["lens"]), str(row["unit_key"])),
    )


def load_articles(client: Client, ids: list[str]) -> dict[str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for start in range(0, len(ids), 150):
        response = (
            client.table("articles")
            .select("article_id,canonical_url,headline,publisher,published_at,first_seen_at,source_metadata")
            .in_("article_id", ids[start:start + 150])
            .execute()
        )
        rows.extend(getattr(response, "data", None) or [])
    translations: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), 150):
        response = (
            client.table("article_translations")
            .select("article_id,translated_headline,source_language_iso2,created_at")
            .eq("translation_profile", TRANSLATION_PROFILE)
            .in_("article_id", ids[start:start + 150])
            .order("created_at", desc=True)
            .execute()
        )
        for row in getattr(response, "data", None) or []:
            translations.setdefault(str(row["article_id"]), row)
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        aid = str(row["article_id"])
        metadata = row.get("source_metadata") if isinstance(row.get("source_metadata"), dict) else {}
        evidence = ""
        for key in ("human_evidence_summary", "article_summary", "summary", "snippet", "description", "source_snippet"):
            if metadata.get(key) and str(metadata[key]).strip():
                evidence = str(metadata[key]).strip()
                break
        result[aid] = {
            **row,
            "headline_english": str((translations.get(aid) or {}).get("translated_headline") or row.get("headline") or ""),
            "evidence": evidence,
        }
    return result


def load_events_and_sources(
    client: Client,
    event_ids: list[str],
    article_map: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    events: dict[str, dict[str, Any]] = {}
    for start in range(0, len(event_ids), 100):
        response = (
            client.table("events")
            .select("event_id,event_title,event_summary,event_date,first_seen_at,last_seen_at")
            .in_("event_id", event_ids[start:start + 100])
            .execute()
        )
        for row in getattr(response, "data", None) or []:
            events[str(row["event_id"])] = row
    links: list[dict[str, Any]] = []
    for start in range(0, len(event_ids), 100):
        response = (
            client.table("event_articles")
            .select("event_id,article_id,is_canonical_source")
            .in_("event_id", event_ids[start:start + 100])
            .execute()
        )
        links.extend(getattr(response, "data", None) or [])
    sources: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in links:
        eid = str(link["event_id"])
        article = article_map.get(str(link["article_id"]))
        if article:
            sources[eid].append(article)
    return events, sources


def latest_empowerment_rows(
    client: Client,
    coverage_ids: list[str],
    event_ids: list[str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    fields = (
        "lens_classification_id,lens,article_id,event_id,empowerment_status,"
        "empowerment_degree,narrative_frame,distribution_breadth,dominant_dimension,"
        "topic,geographic_scope,country_iso3s,content_basis,confidence,reasoning,created_at"
    )
    for ids, lens, column in ((coverage_ids, "coverage", "article_id"), (event_ids, "event", "event_id")):
        for start in range(0, len(ids), 120):
            query = (
                client.table("lens_classifications")
                .select(fields)
                .eq("lens", lens)
                .in_(column, ids[start:start + 120])
            )
            response = query.execute()
            rows = getattr(response, "data", None) or []
            rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
            for row in rows:
                unit_id = str(row.get(column) or "")
                if unit_id:
                    result.setdefault(f"{lens}:{unit_id}", row)
    return result


def evidence_text_for_coverage(article: dict[str, Any]) -> str:
    lines = [
        f"Publisher: {article.get('publisher') or 'Unknown source'}",
        f"Headline: {article.get('headline_english') or article.get('headline') or ''}",
    ]
    if article.get("evidence"):
        lines.append(f"Stored summary or snippet: {article['evidence']}")
    else:
        lines.append("No stored summary or snippet. Open the original source before accepting a strong relationship label.")
    return "\n".join(lines)


def evidence_text_for_event(event: dict[str, Any], sources: list[dict[str, Any]]) -> str:
    lines = [f"Development: {event.get('event_title') or ''}"]
    if event.get("event_summary"):
        lines.append(f"Stored event summary: {event['event_summary']}")
    lines.append("Sources:")
    for source in sources:
        line = f"- {source.get('publisher') or 'Unknown source'}: {source.get('headline_english') or source.get('headline') or ''}"
        if source.get("evidence"):
            line += f" | {source['evidence']}"
        lines.append(line)
    return "\n".join(lines)


def build_payload(client: Client, *, scope: str) -> dict[str, Any]:
    rows = latest_rows(client, scope=scope)
    release_ids = sorted({str(row.get("release_id") or "") for row in rows if row.get("release_id")})
    releases = {release_id: load_release(release_id) for release_id in release_ids}
    release_events = {release_id: release_event_index(release) for release_id, release in releases.items()}

    coverage_ids = sorted({str(row["article_id"]) for row in rows if row.get("article_id")})
    event_ids = sorted({str(row["event_id"]) for row in rows if row.get("event_id")})

    all_article_ids = set(coverage_ids)
    for release_id, events in release_events.items():
        wanted_event_ids = {
            str(row.get("event_id"))
            for row in rows
            if str(row.get("release_id") or "") == release_id and row.get("event_id")
        }
        for event_id in wanted_event_ids:
            event = events.get(event_id, {})
            all_article_ids.update(str(value) for value in (event.get("member_article_ids") or []) if value)
            all_article_ids.update(
                str(source.get("article_id"))
                for source in (event.get("sources") or [])
                if isinstance(source, dict) and source.get("article_id")
            )

    article_map = load_articles(client, sorted(all_article_ids))
    fallback_event_map, fallback_sources = load_events_and_sources(client, event_ids, article_map)
    empowerment = latest_empowerment_rows(client, coverage_ids, event_ids) if scope == "latest" else {}

    items: list[dict[str, Any]] = []
    for row in rows:
        lens = str(row["lens"])
        unit_id = str(row.get("article_id") or row.get("event_id"))
        unit_key = str(row["unit_key"])
        release_id = str(row.get("release_id") or "")
        period_start = str(row.get("period_start") or "")
        period_end = str(row.get("period_end") or "")

        if lens == "coverage":
            article = article_map.get(unit_id, {})
            sources = [
                {
                    "publisher": article.get("publisher") or "Open source",
                    "url": article.get("canonical_url") or "",
                }
            ]
            title = article.get("headline_english") or article.get("headline") or unit_id
            date = article.get("published_at") or article.get("first_seen_at")
            evidence = evidence_text_for_coverage(article)
        else:
            event = release_events.get(release_id, {}).get(unit_id)
            if not event:
                event = fallback_event_map.get(unit_id, {})
                srcs = fallback_sources.get(unit_id, [])
            else:
                srcs = []
                source_rows = [source for source in (event.get("sources") or []) if isinstance(source, dict)]
                source_ids = [str(source.get("article_id")) for source in source_rows if source.get("article_id")]
                for source in source_rows:
                    article_id = str(source.get("article_id") or "")
                    article = article_map.get(article_id)
                    if article:
                        srcs.append(article)
                    else:
                        srcs.append(
                            {
                                "article_id": article_id,
                                "publisher": source.get("publisher") or "Open source",
                                "canonical_url": source.get("url") or "",
                                "headline": source.get("headline") or "",
                                "headline_english": source.get("headline") or "",
                                "published_at": source.get("published_date") or "",
                                "first_seen_at": source.get("published_date") or "",
                                "evidence": "",
                            }
                        )
                if not srcs:
                    for article_id in event.get("member_article_ids") or []:
                        article = article_map.get(str(article_id))
                        if article:
                            srcs.append(article)
            sources = [
                {
                    "publisher": source.get("publisher") or "Open source",
                    "url": source.get("canonical_url") or source.get("url") or "",
                }
                for source in srcs
            ]
            title = event.get("event_title") or unit_id
            date = event.get("event_date") or event.get("first_seen_at")
            evidence = evidence_text_for_event(event, srcs)

        existing_review = None
        if row.get("review_status") in {"accepted", "corrected", "insufficient_evidence", "rejected"}:
            final = final_payload_from_classification(row)
            safe_key = unit_key.replace(":", "-")
            existing_review = {
                "decision_id": f"symbiosis-{safe_key}-v1",
                "release_id": release_id,
                "lens": lens,
                "unit_key": unit_key,
                "article_id": row.get("article_id"),
                "event_id": row.get("event_id"),
                "review_status": row.get("review_status"),
                "reviewer_name": row.get("reviewer_name") or "Kedma Hamelberg",
                "notes": "Loaded from Supabase",
                "source_urls": [source["url"] for source in sources if source.get("url")],
                "final": {
                    "human_experience_type": final["human_experience_type"],
                    "ai_expressive_role": final["ai_expressive_role"],
                    "evidence_status": final["evidence_status"],
                    "story_country_iso3s": final["story_country_iso3s"],
                    "evidence_summary": final["evidence_summary"],
                    "reasoning": final["reasoning"],
                    "empowerment_status": final["empowerment_status"] or "unclear",
                    "empowerment_degree": final["empowerment_degree"] or 0,
                    "empowerment_reasoning": final["empowerment_reasoning"] or "",
                },
            }

        items.append(
            {
                "symbiosis_classification_id": row["symbiosis_classification_id"],
                "release_id": release_id,
                "period_start": period_start,
                "period_end": period_end,
                "lens": lens,
                "unit_id": unit_id,
                "unit_key": unit_key,
                "article_id": row.get("article_id"),
                "event_id": row.get("event_id"),
                "title": str(title),
                "date": str(date or ""),
                "sources": sources,
                "content_basis": row.get("content_basis"),
                "evidence": evidence,
                "model": {
                    "human_experience_type": row.get("model_human_experience_type"),
                    "ai_expressive_role": row.get("model_ai_expressive_role"),
                    "human_direction": row.get("model_human_direction"),
                    "ai_direction": row.get("model_ai_direction"),
                    "configuration": row.get("model_configuration"),
                    "plain_label": row.get("model_plain_label"),
                    "evidence_status": row.get("evidence_status"),
                    "human_reasoning": row.get("model_human_reasoning"),
                    "ai_reasoning": row.get("model_ai_reasoning"),
                    "summary": row.get("model_summary"),
                    "confidence": row.get("model_confidence"),
                    "country_iso3s": row.get("country_iso3s") or [],
                },
                "empowerment_model": empowerment.get(f"{lens}:{unit_id}"),
                "review_status": row.get("review_status"),
                "existing_review": existing_review,
            }
        )

    return {
        "schema_version": "aieo_symbiosis_workbench_v1.1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scope": scope,
        "release_ids": release_ids,
        "excluded_aggregate_references": excluded_references_for_scope(scope),
        "codebook_version": CODEBOOK_VERSION,
        "default_reviewer": "Kedma Hamelberg",
        "item_count": len(items),
        "items": items,
    }


def build_html(payload: dict[str, Any]) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")
    js = JS_PATH.read_text(encoding="utf-8")
    serialized = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    template = template.replace("/* populated in the self-contained artifact */", css, 1)
    template = template.replace(
        'window.AIEO_SYMBIOSIS_PAYLOAD = {"codebook_version":"loading","items":[]};',
        f"window.AIEO_SYMBIOSIS_PAYLOAD = {serialized};",
    )
    template = template.replace("/* populated in the self-contained artifact */", js, 1)
    return template


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=["latest", "history", "all"], default="all")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client: Client = create_client(required_env("SUPABASE_URL"), required_env("SUPABASE_SECRET_KEY"))
    payload = build_payload(client, scope=args.scope)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(build_html(payload), encoding="utf-8")
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"scope": args.scope, "item_count": payload["item_count"], "output": str(OUTPUT_PATH)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
