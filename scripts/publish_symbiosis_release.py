#!/usr/bin/env python3
"""Build the public, versioned relationship-pattern artifact for a weekly release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supabase import Client, create_client

from symbiosis_common import (
    CLASSIFIER_VERSION,
    CODEBOOK_VERSION,
    CORE_FOUR,
    PARTIALS,
    PLAIN_LABELS,
    PUBLIC_SIGNAL_SCHEMA_VERSION,
    RELATIONSHIP_PATTERN_KEYS,
    TECHNICAL_LABELS,
    derive_configuration,
    classification_input_evidence,
    evidence_basis_strength,
    final_payload_from_classification,
    normalize_ai_role,
    normalize_distribution_signal,
    normalize_evidence_status,
    normalize_human_type,
    normalize_relationship_patterns,
    public_signals_from_patterns,
    release_full_text_requirements,
)

ROOT = Path(__file__).resolve().parents[1]
RELEASES_DIR = ROOT / "data" / "releases"
OUTPUT_DIR = ROOT / "data" / "symbiosis"
CURRENT_PATH = OUTPUT_DIR / "current.json"
INDEX_PATH = OUTPUT_DIR / "index.json"
OWNER_GOLD_PATH = ROOT / "validation" / "symbiosis-owner-gold.json"
SOURCE_BODY_QC_DIR = ROOT / "validation" / "qc"


class PublishError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise PublishError(f"{name} is missing.")
    return value


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise PublishError(f"Missing JSON file: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PublishError(f"Expected a JSON object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def release_path(release_id: str | None) -> Path:
    if release_id:
        return RELEASES_DIR / "weekly" / f"{release_id}.json"
    return RELEASES_DIR / "current.json"


def unit_ids(release: dict[str, Any]) -> tuple[list[str], list[str], dict[str, dict[str, Any]]]:
    articles: set[str] = set()
    events: set[str] = set()
    evidence_by_event: dict[str, dict[str, Any]] = {}

    # Coverage expectations come from the weekly release Coverage Lens, not only
    # from event-source membership. This preserves an exact human-review gate.
    coverage_rows = release.get("units", {}).get("coverage_articles", []) or []
    for row in coverage_rows:
        if not isinstance(row, dict) or not row.get("article_id"):
            continue
        if row.get("classification", {}).get("ai_relevant") is False:
            continue
        articles.add(str(row["article_id"]))

    for event in release.get("evidence") or []:
        if not isinstance(event, dict):
            continue
        if event.get("classification", {}).get("ai_relevant") is False:
            continue
        event_id = str(event.get("effective_event_id") or event.get("event_id") or "").strip()
        if event_id:
            events.add(event_id)
            evidence_by_event[event_id] = event
        if not coverage_rows:
            for article_id in event.get("member_article_ids") or []:
                if article_id:
                    articles.add(str(article_id))
            for source in event.get("sources") or []:
                if isinstance(source, dict) and source.get("article_id"):
                    articles.add(str(source["article_id"]))
    return sorted(articles), sorted(events), evidence_by_event


def latest_rows(
    client: Client,
    *,
    release_id: str,
    lens: str,
    ids: list[str],
) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    column = "article_id" if lens == "coverage" else "event_id"
    rows: list[dict[str, Any]] = []
    for start in range(0, len(ids), 100):
        response = (
            client.table("symbiosis_classifications")
            .select("*,symbiosis_classification_runs!inner(status,classifier_version)")
            .eq("codebook_version", CODEBOOK_VERSION)
            .eq("symbiosis_classification_runs.status", "success")
            .eq("symbiosis_classification_runs.classifier_version", CLASSIFIER_VERSION)
            .eq("release_id", release_id)
            .eq("lens", lens)
            .in_(column, ids[start:start + 100])
            .execute()
        )
        rows.extend(getattr(response, "data", None) or [])
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get(column) or "")
        if key:
            latest.setdefault(key, row)
    return latest


def full_text_sources_used(row: dict[str, Any] | None) -> int:
    if not row:
        return 0
    content_basis, summary = classification_input_evidence(row)
    return evidence_basis_strength(content_basis, summary)[0]


def require_current_full_text_lineage(
    release: dict[str, Any],
    coverage_rows: dict[str, dict[str, Any]],
    event_rows: dict[str, dict[str, Any]],
    source_body_corrections: dict[str, dict[str, Any]],
) -> None:
    """Refuse to republish headline rows after full bodies became available."""
    coverage_required, event_required = release_full_text_requirements(release)
    stale_coverage = [
        article_id
        for article_id, required in coverage_required.items()
        if full_text_sources_used(coverage_rows.get(article_id)) < required
    ]
    stale_events = []
    for event_id, required in event_required.items():
        row = event_rows.get(event_id)
        used = full_text_sources_used(row)
        correction = source_body_corrections.get(event_id)
        if correction:
            used = max(used, full_text_sources_used(correction))
        if used < required:
            stale_events.append(event_id)
    if stale_coverage or stale_events:
        raise PublishError(
            "Saved successful relationship rows are stale relative to the current full-body release: "
            f"{len(stale_coverage)}/{len(coverage_required)} required full-body coverage rows and "
            f"{len(stale_events)}/{len(event_required)} required full-body event rows were still "
            "classified from weaker evidence. Resume the interrupted replacement run; do not publish "
            "or restart body collection."
        )


def configuration_summary(
    rows: dict[str, dict[str, Any]],
    expected_ids: list[str],
    display_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return final review counts and the release's current evidence reading.

    The public Observatory updates every week.  Completed review decisions are
    merged into the current source-linked reading for that release; a complete
    review is recorded separately when every expected unit has been reviewed.
    """
    available = [
        (display_overrides or {}).get(unit_id, final_payload_from_classification(rows[unit_id]))
        for unit_id in expected_ids
        if unit_id in rows
    ]
    reviewed = [
        final_payload_from_classification(rows[unit_id])
        for unit_id in expected_ids
        if unit_id in rows and final_payload_from_classification(rows[unit_id])["reviewed"]
    ]

    def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
        configurations = [
            str(item.get("configuration"))
            for item in items
            if item.get("configuration") in PLAIN_LABELS
        ]
        counts = Counter(configurations)
        complete_total = sum(counts[value] for value in CORE_FOUR)
        partial_total = sum(counts[value] for value in PARTIALS)
        return {
            "configuration_counts": {key: counts.get(key, 0) for key in PLAIN_LABELS},
            "complete_configuration_count": complete_total,
            "partial_signal_count": partial_total,
            "no_clear_relational_signal_count": counts.get("no_clear_relational_signal", 0),
            "ambiguous_relational_signal_count": counts.get("ambiguous_relational_signal", 0),
            "insufficient_evidence_count": counts.get("insufficient_evidence", 0),
            "core_four_distribution": {
                value: round(counts[value] / complete_total, 6) if complete_total else 0.0
                for value in sorted(CORE_FOUR)
            },
        }

    reviewed_summary = summarize(reviewed)
    display_summary = summarize(available)
    classified_units = len(available)
    reviewed_units = len(reviewed)
    expected_units = len(expected_ids)
    complete_review = expected_units > 0 and reviewed_units == expected_units
    display_basis = (
        "human_reviewed"
        if complete_review
        else "current_evidence_reading_with_reviewed_corrections"
        if classified_units
        else "classification_in_progress"
    )

    return {
        "expected_units": expected_units,
        "classified_units": classified_units,
        "reviewed_units": reviewed_units,
        "unreviewed_units": expected_units - reviewed_units,
        **reviewed_summary,
        "display_basis": display_basis,
        "display_classified_units": classified_units,
        "display_configuration_counts": display_summary["configuration_counts"],
        "display_complete_configuration_count": display_summary["complete_configuration_count"],
        "display_partial_signal_count": display_summary["partial_signal_count"],
        "display_no_clear_relational_signal_count": display_summary["no_clear_relational_signal_count"],
        "display_ambiguous_relational_signal_count": display_summary["ambiguous_relational_signal_count"],
        "display_insufficient_evidence_count": display_summary["insufficient_evidence_count"],
        "display_core_four_distribution": display_summary["core_four_distribution"],
        "denominator_note": (
            "The weekly display reports the source evidence available for the current release. "
            "A development is kept separate when the available source evidence is not enough to show a clear people outcome."
        ),
    }


def owner_gold_for_release(release_id: str) -> dict[str, dict[str, Any]]:
    if not OWNER_GOLD_PATH.exists():
        return {}
    payload = read_json(OWNER_GOLD_PATH)
    result: dict[str, dict[str, Any]] = {}
    for record in payload.get("records") or []:
        if not isinstance(record, dict) or str(record.get("release_id") or "") != release_id:
            continue
        event_id = str(record.get("event_id") or "").strip()
        if event_id:
            result[event_id] = record
    return result


def source_body_corrections_for_release(release_id: str) -> dict[str, dict[str, Any]]:
    path = SOURCE_BODY_QC_DIR / f"{release_id}-source-body-audit.json"
    if not path.exists():
        return {}
    payload = read_json(path)
    return {
        str(row.get("event_id")): row
        for row in payload.get("records") or []
        if isinstance(row, dict) and row.get("event_id")
    }


def apply_source_body_correction(
    final: dict[str, Any],
    record: dict[str, Any] | None,
) -> dict[str, Any]:
    if not record:
        return final
    human_type = normalize_human_type(record.get("human_experience_type"))
    ai_role = normalize_ai_role(record.get("ai_expressive_role"))
    evidence_status = normalize_evidence_status(record.get("evidence_status"))
    configuration, human_direction, ai_direction, plain_label = derive_configuration(
        human_type, ai_role, evidence_status
    )
    patterns, _ = normalize_relationship_patterns(
        record.get("relationship_patterns"),
        fallback_configuration=configuration,
    )
    distribution, distribution_explicit = normalize_distribution_signal(
        record.get("distribution_signal")
    )
    public_signals = public_signals_from_patterns(
        patterns,
        configuration=configuration,
        human_direction=human_direction,
        evidence_status=evidence_status,
        distribution_signal=distribution,
    )
    takeaway = str(record.get("public_takeaway") or "").strip()
    return {
        **final,
        "reviewed": False,
        "review_status": "source_body_qc_ai_assisted",
        "configuration": configuration,
        "plain_label": plain_label,
        "technical_label": TECHNICAL_LABELS[configuration],
        "human_experience_type": human_type,
        "ai_expressive_role": ai_role,
        "human_direction": human_direction,
        "ai_direction": ai_direction,
        "evidence_status": evidence_status,
        "content_basis": str(record.get("content_basis") or "full_text_supplied_by_owner"),
        "evidence_basis_summary": {
            "source_count": 1,
            "full_text_sources": 1,
            "article_summary_sources": 0,
            "snippet_sources": 0,
            "headline_only_sources": 0,
            "body_coverage": "owner_supplied_full_body",
        },
        "evidence_summary": takeaway,
        "reasoning": str(record.get("reasoning") or takeaway).strip(),
        "relationship_patterns": patterns,
        "public_signals": public_signals,
        "distribution_signal": distribution,
        "public_takeaway": takeaway,
        "multi_label_available": True,
        "distribution_coded": distribution_explicit,
        "signal_provenance": "owner_requested_ai_assisted_source_body_audit",
    }


def resolved_public_payload(
    row: dict[str, Any] | None,
    owner_record: dict[str, Any] | None = None,
    source_body_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    final = final_payload_from_classification(row) if row else {
        "reviewed": False,
        "review_status": "not_classified",
        "configuration": None,
        "plain_label": "Relationship review pending",
        "technical_label": "Relationship review pending",
        "human_experience_type": None,
        "ai_expressive_role": None,
        "human_direction": None,
        "ai_direction": None,
        "evidence_status": None,
        "content_basis": "not_available",
        "evidence_basis_summary": {},
        "story_country_iso3s": [],
        "evidence_summary": "",
        "reasoning": "",
        "empowerment_status": None,
        "empowerment_degree": None,
        "empowerment_reasoning": None,
        "schema_version": PUBLIC_SIGNAL_SCHEMA_VERSION,
        "relationship_patterns": {key: False for key in RELATIONSHIP_PATTERN_KEYS},
        "public_signals": {
            "people_gaining": False,
            "people_losing_ground": False,
            "mixed_picture": False,
            "not_everyone_benefits": False,
            "not_clear_yet": True,
        },
        "distribution_signal": "not_shown",
        "public_takeaway": "",
        "multi_label_available": False,
        "distribution_coded": False,
    }
    final = apply_source_body_correction(final, source_body_record)
    if not owner_record:
        return final

    owner_final = owner_record.get("final") if isinstance(owner_record.get("final"), dict) else {}
    configuration = str(owner_final.get("configuration") or final.get("configuration") or "")
    human_direction = str(owner_final.get("human_direction") or final.get("human_direction") or "")
    evidence_status = str(owner_final.get("evidence_status") or final.get("evidence_status") or "")
    patterns, explicit = normalize_relationship_patterns(
        owner_final.get("relationship_patterns"),
        fallback_configuration=configuration,
    )
    distribution, distribution_explicit = normalize_distribution_signal(
        owner_final.get("distribution_signal")
    )
    public_signals = public_signals_from_patterns(
        patterns,
        configuration=configuration,
        human_direction=human_direction,
        evidence_status=evidence_status,
        distribution_signal=distribution,
    )
    return {
        **final,
        "reviewed": True,
        "review_status": "owner_manual_qc",
        "configuration": configuration or final.get("configuration"),
        "human_direction": human_direction or final.get("human_direction"),
        "ai_direction": owner_final.get("ai_direction") or final.get("ai_direction"),
        "evidence_status": evidence_status or final.get("evidence_status"),
        "relationship_patterns": patterns,
        "public_signals": public_signals,
        "distribution_signal": distribution,
        "public_takeaway": str(
            owner_final.get("public_takeaway")
            or owner_record.get("review_reasoning")
            or final.get("public_takeaway")
            or ""
        ).strip(),
        "multi_label_available": explicit,
        "distribution_coded": distribution_explicit,
        "signal_provenance": "owner_manual_qc",
    }


def public_signal_summary(
    rows: dict[str, dict[str, Any]],
    expected_ids: list[str],
    owner_gold: dict[str, dict[str, Any]],
    source_body_corrections: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    pattern_counts = Counter()
    people_counts = Counter()
    explicit_multi_label_units = 0
    distribution_coded_units = 0
    classified_units = 0
    body_coverage_counts = Counter()
    not_clear_breakdown = Counter()
    for event_id in expected_ids:
        final = resolved_public_payload(
            rows.get(event_id),
            owner_gold.get(event_id),
            source_body_corrections.get(event_id),
        )
        if rows.get(event_id) is not None:
            classified_units += 1
        if final.get("multi_label_available"):
            explicit_multi_label_units += 1
        if final.get("distribution_coded"):
            distribution_coded_units += 1
        for key in RELATIONSHIP_PATTERN_KEYS:
            pattern_counts[key] += int(bool((final.get("relationship_patterns") or {}).get(key)))
        for key, value in (final.get("public_signals") or {}).items():
            people_counts[key] += int(bool(value))
        basis = final.get("evidence_basis_summary") or {}
        body_coverage_counts[str(basis.get("body_coverage") or "not_recorded")] += 1
        if (final.get("public_signals") or {}).get("not_clear_yet"):
            if str(final.get("evidence_status") or "") == "insufficient":
                not_clear_breakdown["not_enough_evidence"] += 1
            else:
                not_clear_breakdown["no_directional_people_change"] += 1

    expected = len(expected_ids)
    return {
        "schema_version": PUBLIC_SIGNAL_SCHEMA_VERSION,
        "expected_units": expected,
        "classified_units": classified_units,
        "relationship_pattern_counts": {
            key: int(pattern_counts[key]) for key in RELATIONSHIP_PATTERN_KEYS
        },
        "people_signal_counts": {
            key: int(people_counts[key])
            for key in (
                "people_gaining",
                "people_losing_ground",
                "mixed_picture",
                "not_everyone_benefits",
                "not_clear_yet",
            )
        },
        "availability": {
            "people_gaining": classified_units == expected and expected > 0,
            "people_losing_ground": classified_units == expected and expected > 0,
            "mixed_picture": explicit_multi_label_units == expected and expected > 0,
            "not_everyone_benefits": distribution_coded_units == expected and expected > 0,
            "not_clear_yet": classified_units == expected and expected > 0,
        },
        "explicit_multi_label_units": explicit_multi_label_units,
        "distribution_coded_units": distribution_coded_units,
        "body_coverage_counts": dict(sorted(body_coverage_counts.items())),
        "not_clear_breakdown": {
            "not_enough_evidence": int(not_clear_breakdown["not_enough_evidence"]),
            "no_directional_people_change": int(not_clear_breakdown["no_directional_people_change"]),
        },
        "overlap_note": "A development may contain more than one signal, so these counts do not have to add up to the weekly total.",
    }


def empowerment_secondary_summary(rows: dict[str, dict[str, Any]], expected_ids: list[str]) -> dict[str, Any]:
    finals = [
        final_payload_from_classification(rows[unit_id])
        for unit_id in expected_ids
        if unit_id in rows and final_payload_from_classification(rows[unit_id])["reviewed"]
    ]
    counts = Counter(str(item.get("empowerment_status") or "unclear") for item in finals)
    scores: list[float] = []
    for item in finals:
        status = str(item.get("empowerment_status") or "unclear")
        degree = int(item.get("empowerment_degree") or 0)
        if status == "expanding":
            scores.append(degree / 3.0)
        elif status == "contracting":
            scores.append(-degree / 3.0)
        elif status in {"mixed", "non_empowerment"}:
            scores.append(0.0)
    total = len(finals)
    return {
        "reviewed_units": total,
        "scored_units": len(scores),
        "excluded_unclear": counts.get("unclear", 0),
        "empowerment_index": round((sum(scores) / len(scores)) * 100, 4) if scores else None,
        "status_counts": {
            key: counts.get(key, 0)
            for key in ["expanding", "contracting", "mixed", "non_empowerment", "unclear"]
        },
        "status_distribution": {
            key: round(counts.get(key, 0) / total, 6) if total else 0.0
            for key in ["expanding", "contracting", "mixed", "non_empowerment", "unclear"]
        },
        "note": "Secondary empowerment values use the same human review decisions as the relationship lens. Unclear units are excluded from the index denominator.",
    }


def event_public_rows(
    event_rows: dict[str, dict[str, Any]],
    event_ids: list[str],
    evidence: dict[str, dict[str, Any]],
    owner_gold: dict[str, dict[str, Any]],
    source_body_corrections: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for event_id in event_ids:
        source = evidence.get(event_id, {})
        final = resolved_public_payload(
            event_rows.get(event_id),
            owner_gold.get(event_id),
            source_body_corrections.get(event_id),
        )
        result.append(
            {
                "event_id": event_id,
                "event_title": source.get("event_title") or "Untitled development",
                "event_date": source.get("event_date"),
                "novelty_status": source.get("novelty_status"),
                "sources": source.get("sources") or [],
                "reviewed": final["reviewed"],
                "review_status": final["review_status"],
                "configuration": final["configuration"],
                "plain_label": final["plain_label"],
                "human_experience_type": final["human_experience_type"],
                "ai_expressive_role": final["ai_expressive_role"],
                "human_direction": final["human_direction"],
                "ai_direction": final["ai_direction"],
                "evidence_status": final["evidence_status"],
                "content_basis": final.get("content_basis"),
                "evidence_basis_summary": final.get("evidence_basis_summary") or {},
                "classification_audit": final.get("classification_audit") or {},
                "story_country_iso3s": final["story_country_iso3s"],
                "evidence_summary": final["evidence_summary"],
                "reasoning": final["reasoning"],
                "relationship_patterns": final["relationship_patterns"],
                "public_signals": final["public_signals"],
                "distribution_signal": final["distribution_signal"],
                "public_takeaway": final["public_takeaway"],
                "multi_label_available": final["multi_label_available"],
                "distribution_coded": final["distribution_coded"],
                "signal_provenance": final.get("signal_provenance") or "model_classification",
                "empowerment_secondary": {
                    "status": final["empowerment_status"],
                    "degree": final["empowerment_degree"],
                    "reasoning": final["empowerment_reasoning"],
                },
            }
        )
    return result


def normalized_hash(payload: dict[str, Any]) -> str:
    copy = json.loads(json.dumps(payload))
    copy.pop("generated_at", None)
    copy.pop("content_sha256", None)
    copy.pop("revision", None)
    return hashlib.sha256(json.dumps(copy, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def archive_and_revision(target: Path, new_payload: dict[str, Any]) -> tuple[int, bool]:
    if not target.exists():
        return 1, True
    old = read_json(target)
    if normalized_hash(old) == normalized_hash(new_payload):
        return int(old.get("revision") or 1), False
    old_revision = int(old.get("revision") or 1)
    release_id = str(old.get("release_id") or new_payload["release_id"])
    archive = OUTPUT_DIR / "weekly" / "archive" / release_id / f"revision-{old_revision}.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, archive)
    return old_revision + 1, True


def canonical_current_release_id() -> str:
    current_release = read_json(RELEASES_DIR / "current.json")
    value = str(current_release.get("release_id") or "").strip()
    if not value:
        raise PublishError("Canonical current weekly release lacks release_id.")
    return value


def update_index(payload: dict[str, Any], *, current_release_id: str) -> None:
    index = read_json(INDEX_PATH) if INDEX_PATH.exists() else {
        "schema_version": "aieo_symbiosis_index_v1.0",
        "weekly": [],
    }
    rows = [row for row in index.get("weekly", []) if row.get("release_id") != payload["release_id"]]
    rows.append(
        {
            "release_id": payload["release_id"],
            "revision": payload["revision"],
            "period_start": payload["period_start"],
            "period_end": payload["period_end"],
            "public_status": payload["public_status"],
            "event_reviewed": payload["review"]["event_reviewed"],
            "event_total": payload["review"]["event_total"],
            "coverage_reviewed": payload["review"]["coverage_reviewed"],
            "coverage_total": payload["review"]["coverage_total"],
        }
    )
    rows.sort(key=lambda row: str(row.get("period_start") or ""))
    index.update(
        {
            "updated_at": now_iso(),
            "current_release_id": current_release_id,
            "weekly": rows,
        }
    )
    write_json(INDEX_PATH, index)


def persist_release_payload(
    *,
    target: Path,
    payload: dict[str, Any],
    release_id: str,
) -> tuple[int, bool, str, bool]:
    """Persist one weekly relationship artifact without rolling current backwards.

    A historical owner-QC correction updates its versioned weekly artifact and
    index row, but current.json remains tied to data/releases/current.json.
    """
    canonical_current = canonical_current_release_id()
    is_current_release = release_id == canonical_current
    revision, changed = archive_and_revision(target, payload)
    payload["revision"] = revision
    payload["content_sha256"] = normalized_hash(payload)
    if changed:
        write_json(target, payload)
        if is_current_release:
            write_json(CURRENT_PATH, payload)
        update_index(payload, current_release_id=canonical_current)
    elif is_current_release:
        current_payload = read_json(target)
        write_json(CURRENT_PATH, current_payload)
        update_index(current_payload, current_release_id=canonical_current)
    return revision, changed, canonical_current, is_current_release


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", default="")
    parser.add_argument("--require-complete", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    release = read_json(release_path(args.release_id or None))
    release_id = str(release.get("release_id") or "").strip()
    if not release_id:
        raise PublishError("Release JSON lacks release_id.")
    article_ids, event_ids, evidence = unit_ids(release)
    client: Client = create_client(required_env("SUPABASE_URL"), required_env("SUPABASE_SECRET_KEY"))
    coverage_rows = latest_rows(client, release_id=release_id, lens="coverage", ids=article_ids)
    event_rows = latest_rows(client, release_id=release_id, lens="event", ids=event_ids)
    owner_gold = owner_gold_for_release(release_id)
    source_body_corrections = source_body_corrections_for_release(release_id)
    require_current_full_text_lineage(
        release,
        coverage_rows,
        event_rows,
        source_body_corrections,
    )
    event_display_overrides = {
        event_id: resolved_public_payload(
            event_rows.get(event_id),
            owner_gold.get(event_id),
            source_body_corrections.get(event_id),
        )
        for event_id in event_ids
        if event_id in event_rows
    }
    coverage_summary = configuration_summary(coverage_rows, article_ids)
    event_summary = configuration_summary(event_rows, event_ids, event_display_overrides)
    people_signal_summary = public_signal_summary(
        event_rows, event_ids, owner_gold, source_body_corrections
    )
    coverage_empowerment = empowerment_secondary_summary(coverage_rows, article_ids)
    event_empowerment = empowerment_secondary_summary(event_rows, event_ids)
    event_complete = event_summary["reviewed_units"] == event_summary["expected_units"] and event_summary["expected_units"] > 0
    coverage_complete = coverage_summary["reviewed_units"] == coverage_summary["expected_units"] and coverage_summary["expected_units"] > 0
    complete = event_complete and coverage_complete
    if args.require_complete and not complete:
        raise PublishError(
            "Human review is incomplete: "
            f"events {event_summary['reviewed_units']}/{event_summary['expected_units']}; "
            f"coverage {coverage_summary['reviewed_units']}/{coverage_summary['expected_units']}."
        )

    payload: dict[str, Any] = {
        "schema_version": "aieo_symbiosis_public_v1.1",
        "release_id": release_id,
        "release_type": "weekly_relationship_lens",
        "revision": 1,
        "period_start": release.get("period_start"),
        "period_end": release.get("period_end"),
        "generated_at": now_iso(),
        "codebook_version": CODEBOOK_VERSION,
        "source_release_sha256": release.get("content_sha256"),
        "public_status": (
            "human_reviewed"
            if complete
            else "current_evidence_reading"
            if event_summary["classified_units"] and coverage_summary["classified_units"]
            else "classification_in_progress"
        ),
        "scope_note": (
            "This lens classifies how source evidence represents human-AI relations. "
            "Each people outcome is tied to the source evidence available for that development. "
            "It does not claim objective system performance, consciousness, intentions, or biological fitness."
        ),
        "review": {
            "complete": complete,
            "event_complete": event_complete,
            "coverage_complete": coverage_complete,
            "event_reviewed": event_summary["reviewed_units"],
            "event_total": event_summary["expected_units"],
            "coverage_reviewed": coverage_summary["reviewed_units"],
            "coverage_total": coverage_summary["expected_units"],
        },
        "definitions": {
            "mutualism": PLAIN_LABELS["mutualism"],
            "ai_benefiting_parasitism": PLAIN_LABELS["ai_benefiting_parasitism"],
            "human_benefiting_parasitism": PLAIN_LABELS["human_benefiting_parasitism"],
            "competition": PLAIN_LABELS["competition"],
            "partial_signals": "Only one side of the relationship is established by the source evidence.",
            "no_clear_relational_signal": PLAIN_LABELS["no_clear_relational_signal"],
            "insufficient_evidence": PLAIN_LABELS["insufficient_evidence"],
        },
        "event": event_summary,
        "people_signals": people_signal_summary,
        "source_body_qc": {
            "review_file": (
                f"validation/qc/{release_id}-source-body-audit.json"
                if source_body_corrections
                else None
            ),
            "reviewed_unit_count": len(source_body_corrections),
            "method": "owner-requested AI-assisted full-body audit",
            "owner_gold": False,
        },
        "coverage": coverage_summary,
        "secondary_empowerment": {
            "event": event_empowerment,
            "coverage": coverage_empowerment,
        },
        "evidence": event_public_rows(
            event_rows, event_ids, evidence, owner_gold, source_body_corrections
        ),
    }

    target = OUTPUT_DIR / "weekly" / f"{release_id}.json"
    revision, changed, canonical_current, is_current_release = persist_release_payload(
        target=target, payload=payload, release_id=release_id
    )

    print(
        json.dumps(
            {
                "release_id": release_id,
                "revision": revision,
                "changed": changed,
                "public_status": payload["public_status"],
                "event_review": f"{event_summary['reviewed_units']}/{event_summary['expected_units']}",
                "coverage_review": f"{coverage_summary['reviewed_units']}/{coverage_summary['expected_units']}",
                "canonical_current_release_id": canonical_current,
                "promoted_to_current": is_current_release,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublishError as exc:
        import sys
        print(f"Symbiosis publication failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
