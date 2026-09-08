"""One auditable complete-content cohort for analysis and the Brief.

Collection snapshots are never rewritten. Eligibility requires a completed
reading and at least one full source whose saved content hash matches the
classification input. Missing companion sources are not exported. Summaries
of audio/video need a recorded complete transcript before they qualify.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

POLICY = "complete_content_2026_09_v1"
SCHEMA = "aieo_complete_content_v1"
DIRECTIONS = ("gain", "loss", "mixed", "none", "unresolved")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
REASONS = {
    "reading_incomplete": "The source reading is incomplete.",
    "no_complete_source": "No complete source with matching input lineage is available.",
    "partial_media_source_used": "The reading used an audio or video summary without a complete transcript.",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def event_id(row):
    return str(row.get("effective_event_id") or row.get("event_id") or "")


def media_summary(url):
    """Recognise publisher audio/video entries, not articles discussing media."""
    parsed = urlparse(str(url or ""))
    return bool(re.search(r"/(?:audio|video|videos|podcasts?)/", parsed.path, re.I))


def source_check(source, basis):
    aid = str(source.get("article_id") or "")
    quality = (basis.get("source_quality") or {}).get(aid) or {}
    fingerprint = (basis.get("source_fingerprints") or {}).get(aid)
    reasons = []
    if quality.get("usable_complete_body") is not True or quality.get("flags"):
        reasons.append("complete_body_unavailable")
    if not isinstance(fingerprint, str) or not SHA256.fullmatch(fingerprint) or fingerprint != quality.get("body_sha256"):
        reasons.append("source_lineage_unverified")
    if media_summary(source.get("url")) and quality.get("scope") != "complete_transcript":
        reasons.append("complete_transcript_unavailable")
    return {"article_id": aid, "url": source.get("url", ""), "eligible": not reasons,
            "reason_codes": reasons, "body_sha256": fingerprint if not reasons else None}


def summarize(rows):
    return {"schema_version": "aieo_independent_directions_v2", "policy_version": "source_representations_independent_axes_2026_09",
            "total": len(rows), "evidence_complete": len(rows),
            **{side: {key: sum(row["axes"][side]["direction"] == key for row in rows) for key in DIRECTIONS} for side in ("human", "ai")}}


def build_cohort(release, relationship):
    for key in ("release_id", "period_start", "period_end"):
        if not release.get(key) or release[key] != relationship.get(key):
            raise ValueError("Cohort inputs differ: " + key)
    if not release.get("content_sha256") or relationship.get("source_release_sha256") != release["content_sha256"]:
        raise ValueError("Cohort relationship revision does not match its release")
    events = release.get("evidence", [])
    ids = [event_id(row) for row in events]
    readings = relationship.get("evidence", [])
    rids = [event_id(row) for row in readings]
    if "" in ids or len(set(ids)) != len(ids) or len(set(rids)) != len(rids) or set(ids) != set(rids):
        raise ValueError("Cohort needs one reading per collected development")
    if len(ids) != release["counts"]["ai_relevant_event_records"]:
        raise ValueError("Cohort collection count does not reconcile")
    eventmap = {event_id(row): row for row in events}
    records, eligible_rows, all_sources = [], [], set()
    for row in sorted(readings, key=event_id):
        eid = event_id(row)
        sources = eventmap[eid].get("sources") or []
        checks = [source_check(source, row.get("evidence_basis_summary") or {}) for source in sources]
        source_ids = [check["article_id"] for check in checks]
        if "" in source_ids or len(set(source_ids)) != len(source_ids):
            raise ValueError("Missing or duplicate source identity: " + eid)
        all_sources.update(source_ids)
        axes = row.get("axes") or {}
        reasons = []
        if axes.get("evidence_complete") is not True or any((axes.get(side) or {}).get("direction") not in DIRECTIONS[:-1] for side in ("human", "ai")):
            reasons.append("reading_incomplete")
        accepted = sorted(check["article_id"] for check in checks if check["eligible"])
        if not accepted:
            reasons.append("no_complete_source")
        # A reading that consumed a partial media summary needs refreshing even
        # if a different complete source is present. Do not silently relabel it.
        basis = row.get("evidence_basis_summary") or {}
        if any("complete_transcript_unavailable" in check["reason_codes"] and (basis.get("source_quality", {}).get(check["article_id"], {}).get("usable_complete_body") is True) for check in checks):
            reasons.append("partial_media_source_used")
        record = {"event_id": eid, "eligible": not reasons, "reason_codes": reasons,
                  "eligible_article_ids": accepted if not reasons else [], "sources": checks}
        records.append(record)
        if record["eligible"]:
            eligible_rows.append(row)
    if len(all_sources) != release["counts"]["ai_relevant_articles"]:
        raise ValueError("Cohort source inventory does not reconcile")
    accepted_sources = sorted({aid for row in records for aid in row["eligible_article_ids"]})
    n = len(eligible_rows)
    payload = {"schema_version": SCHEMA, "policy_version": POLICY,
               **{key: release[key] for key in ("release_id", "period_start", "period_end")},
               "source_release_sha256": release["content_sha256"],
               "source_relationship_sha256": relationship["content_sha256"],
               "denominator": {"unit": "distinct_development", "value": n},
               "counts": {"collected_developments": len(ids), "collected_sources": len(all_sources),
                          "eligible_developments": n, "eligible_sources": len(accepted_sources),
                          "excluded_developments": len(ids) - n, "excluded_sources": len(all_sources) - len(accepted_sources)},
               "directional_summary": summarize(eligible_rows), "records": records,
               "reason_definitions": REASONS,
               "scope_note": "Complete source content with matching classification input; counted once per development. No direction stated remains included. Availability is checked automatically and does not certify classification accuracy. Unavailable sources can bias the observed sample."}
    payload["content_sha256"] = digest(payload)
    return payload


def brief_export(release, relationship, cohort):
    """An atomic, filtered export. It contains no excluded story or source."""
    allowed = {row["event_id"]: set(row["eligible_article_ids"]) for row in cohort["records"] if row["eligible"]}
    aids = set().union(*allowed.values()) if allowed else set()
    events = []
    event_fields = ("event_id", "effective_event_id", "event_title", "event_summary", "event_date", "novelty_status", "possible_historical_match")
    for original in release["evidence"]:
        eid = event_id(original)
        if eid not in allowed:
            continue
        event = {key: deepcopy(original[key]) for key in event_fields if key in original}
        event["sources"] = [deepcopy(s) for s in original["sources"] if str(s["article_id"]) in allowed[eid]]
        event["member_article_ids"] = sorted(allowed[eid])
        event["member_article_count"] = len(allowed[eid])
        event["classification"] = {"ai_relevant": True, "topic": (original.get("classification") or {}).get("topic", "other")}
        events.append(event)
    articles = []
    for original in (release.get("units") or {}).get("coverage_articles", []):
        if str(original.get("article_id")) in aids:
            articles.append({key: deepcopy(original[key]) for key in ("article_id", "event_id", "effective_event_id", "publisher", "url", "headline_original", "headline_english", "published_date", "source_language", "search_markets", "search_languages") if key in original})
            articles[-1]["classification"] = {"ai_relevant": True}
    if {str(row["article_id"]) for row in articles} != aids:
        raise ValueError("Eligible source inventory is incomplete")
    novelty = Counter(row.get("novelty_status") for row in events)
    counts = {"ai_relevant_event_records": len(events), "ai_relevant_articles": len(aids), "extra_coverage": len(aids) - len(events),
              "new_event_records": novelty["first_time"] + novelty["follow_on_development"],
              "first_time_event_records": novelty["first_time"], "follow_on_event_records": novelty["follow_on_development"],
              "recurring_event_records": novelty["recurring"],
              "possible_historical_match_event_records": sum(row.get("novelty_status") not in ("first_time", "follow_on_development", "recurring") for row in events)}
    binding = {"policy_version": POLICY, "cohort_sha256": cohort["content_sha256"],
               "source_release_sha256": release["content_sha256"], "source_relationship_sha256": relationship["content_sha256"],
               "eligible_event_ids": sorted(allowed), "eligible_source_ids": sorted(aids),
               "denominator": deepcopy(cohort["denominator"]), "audit_path": "/data/analysis/current.json"}
    projected_release = {"schema_version": "aieo_complete_content_release_v1",
                         **{key: release[key] for key in ("release_id", "period_start", "period_end")},
                         "complete_content": binding, "counts": counts, "evidence": events,
                         "units": {"coverage_articles": articles}}
    projected_release["content_sha256"] = digest(projected_release)
    rows = []
    for original in relationship["evidence"]:
        eid = event_id(original)
        if eid not in allowed:
            continue
        row = {key: deepcopy(original[key]) for key in ("event_id", "event_title", "event_date", "novelty_status", "axes", "evidence_status", "public_signals", "public_takeaway", "evidence_summary", "relationship_patterns", "display_scope") if key in original}
        row["sources"] = [deepcopy(s) for s in original["sources"] if str(s["article_id"]) in allowed[eid]]
        basis = original["evidence_basis_summary"]
        row["evidence_basis_summary"] = {"input_policy": basis.get("input_policy"), "full_text_sources": len(allowed[eid]),
             "source_count": len(allowed[eid]), "body_coverage": "all_exported_sources",
             **{key: {aid: deepcopy(value) for aid, value in basis.get(key, {}).items() if aid in allowed[eid]} for key in ("source_quality", "source_fingerprints")}}
        rows.append(row)
    projected_relationship = {"schema_version": "aieo_complete_content_relationship_v1",
          **{key: release[key] for key in ("release_id", "period_start", "period_end")},
          "source_release_sha256": projected_release["content_sha256"], "complete_content": binding,
          "directional_summary": deepcopy(cohort["directional_summary"]), "evidence": rows}
    projected_relationship["content_sha256"] = digest(projected_relationship)
    return {"schema_version": "aieo_brief_export_v1", "release_id": release["release_id"],
            "release": projected_release, "relationship": projected_relationship}


def export_audit_csv(cohort, release, path):
    events = {event_id(row): row for row in release["evidence"]}
    def safe(value):
        value = str(value)
        return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
    with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
        fields = ("release_id", "event_id", "development", "included_in_analysis", "reason_codes", "eligible_article_ids", "source_urls")
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in cohort["records"]:
            writer.writerow(dict(zip(fields, map(safe, (cohort["release_id"], row["event_id"], events[row["event_id"]]["event_title"], str(row["eligible"]).lower(), ";".join(row["reason_codes"]), ";".join(row["eligible_article_ids"]), ";".join(s["url"] for s in row["sources"]))))))
