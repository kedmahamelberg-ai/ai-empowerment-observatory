#!/usr/bin/env python3
"""Export a reproducible owner-QC file for the current or a named weekly release.

This is intentionally manual/on-demand. It does not run in the weekly pipeline.
The public relationship signal is event based, so the default export is the
Event Lens (one resolved development per row). A whole-week export can be used
for an occasional audit; stratified_random is intended for lighter future QC.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SYMBIOSIS_DIR = ROOT / "data" / "symbiosis"
RELEASES_DIR = ROOT / "data" / "releases"
DEFAULT_OUT_DIR = ROOT / "review" / "symbiosis" / "qc"

CORE = {
    "mutualism",
    "ai_benefiting_parasitism",
    "human_benefiting_parasitism",
    "competition",
}
PARTIAL = {
    "human_enabling_only",
    "human_constraining_only",
    "ai_enabling_only",
    "ai_constraining_only",
}
BOUNDARY = {
    "no_clear_relational_signal",
    "ambiguous_relational_signal",
    "insufficient_evidence",
}

FIELDS = [
    "review_order",
    "release_id",
    "event_id",
    "unit_key",
    "event_date",
    "development_title",
    "source_count",
    "source_publishers",
    "source_headlines",
    "source_urls",
    "novelty_status",
    "qc_stratum",
    "qa_priority",
    "qa_note",
    "model_configuration",
    "model_plain_label",
    "model_human_experience_type",
    "model_ai_expressive_role",
    "model_human_direction",
    "model_ai_direction",
    "model_evidence_status",
    "model_evidence_summary",
    "model_reasoning",
    "model_review_status",
    "model_reviewed",
    "HUMAN_human_experience_type",
    "HUMAN_ai_expressive_role",
    "HUMAN_evidence_status",
    "HUMAN_reasoning",
    "HUMAN_include_in_gold",
    "HUMAN_reviewer_name",
    "HUMAN_reviewed_at",
]


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected JSON object: {path}")
    return payload


def release_path(release_id: str) -> Path:
    if not release_id:
        return RELEASES_DIR / "current.json"
    candidate = RELEASES_DIR / "weekly" / f"{release_id}.json"
    if candidate.exists():
        return candidate
    current = read_json(RELEASES_DIR / "current.json")
    if str(current.get("release_id") or "") == release_id:
        return RELEASES_DIR / "current.json"
    raise SystemExit(f"Could not find weekly release {release_id}")


def symbiosis_path(release_id: str) -> Path:
    if not release_id:
        return SYMBIOSIS_DIR / "current.json"
    candidate = SYMBIOSIS_DIR / "weekly" / f"{release_id}.json"
    if candidate.exists():
        return candidate
    current = read_json(SYMBIOSIS_DIR / "current.json")
    if str(current.get("release_id") or "") == release_id:
        return SYMBIOSIS_DIR / "current.json"
    raise SystemExit(f"Could not find relationship artifact for {release_id}")


def norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def qc_stratum(configuration: str) -> str:
    if configuration in CORE:
        return "two_sided"
    if configuration in PARTIAL:
        return "one_sided"
    return "boundary_or_insufficient"


EXPLICIT_CONSTRAINT = re.compile(
    r"\b(ban(?:s|ned)?|halt(?:s|ed)?|block(?:s|ed)?|restrict(?:s|ed|ion)?|"
    r"regulat(?:e|es|ed|ing)|sanction(?:s|ed)?|withdraw(?:n|al)?|"
    r"fine[ -]?tun(?:e|ing)|fail(?:s|ed|ure)?|can't|cannot|reject(?:s|ed)?|"
    r"resistance|warning|threat|bias(?:es)?|harm(?:s|ed)?|risk(?:s)?)\b",
    re.I,
)
EXPLICIT_ENABLE = re.compile(
    r"\b(powered|uses?|using|adopt(?:s|ed|ion)?|deploy(?:s|ed|ment)?|"
    r"launch(?:es|ed)?|develop(?:s|ed)|creates?|generates?|writes?|"
    r"accelerat(?:e|es|ed|ing)|productivity|gain(?:s|ed)?|empower(?:s|ed|ing)?|"
    r"integrat(?:e|es|ed|ion)|application|implemented?|real[- ]time)\b",
    re.I,
)
NON_RELATIONAL = re.compile(
    r"\b(stock|shares?|shareholders?|ipo|valuation|investment|investors?|buy|"
    r"market cap|conference|seminar|forum|town hall|top 100|history of|"
    r"opinion|commentary|report meeting|olympiad|recruitment|acquires?|acquisition)\b",
    re.I,
)

# Hand-audited boundary cases from W35 that deserve attention even if generic
# lexical rules would miss them. These are flags, not replacement labels.
SPECIAL_FLAGS = {
    "teenagers will have to grow up with artificial intelligence":
        "Current mutualism label appears stronger than the headline evidence; growing up alongside AI does not itself establish gains on both sides.",
    "university of alberta study finds 87% of canadian musicians view generative ai ‘negatively’":
        "Negative attitudes do not by themselves establish that both people and AI are constrained; verify the competition label.",
    "school ib psychology class: use of artificial intelligence for mental health splits opinion":
        "Split opinion does not itself establish human constraint plus AI-side gain; verify the parasitism label.",
    "fda digital health leader promises generative ai regulatory guidance is coming":
        "A promise of guidance is an institutional action; verify that it really represents AI-side enabling rather than no clear relational signal.",
    "our country has successfully developed nearly 200 key ai standards.":
        "Standards are governance infrastructure; verify that the story supports AI-side gain rather than a neutral institutional development.",
    "henan hosts an artificial intelligence session of “longzi lake science and innovation roadshow”.":
        "Holding an AI-themed session does not itself establish AI-side gain; verify the one-sided enabling label.",
    "douglas county, wis., adopts ai policy for officials, staff":
        "A policy can enable, constrain, or simply govern AI. The title alone does not establish direction; verify the one-sided enabling label.",
    "from war booty to war learning: the legal status of captured military artificial intelligence":
        "Legal analysis of captured AI does not itself show AI-side gain; verify the one-sided enabling label.",
}


def audit_note(row: dict[str, Any]) -> tuple[str, str]:
    title = norm(row.get("event_title"))
    lower = title.casefold()
    config = str(row.get("configuration") or "")
    for key, note in SPECIAL_FLAGS.items():
        if lower == key.casefold():
            return "HIGH", note

    if config == "insufficient_evidence":
        if EXPLICIT_CONSTRAINT.search(title):
            return "HIGH", "Headline contains an explicit constraint/risk/failure cue. Check whether a one-sided constraint or another directional pattern is supportable instead of insufficient evidence."
        if EXPLICIT_ENABLE.search(title):
            return "HIGH", "Headline contains an explicit use/deployment/gain cue. Check whether a one-sided or two-sided directional pattern is supportable instead of insufficient evidence."
        if NON_RELATIONAL.search(title):
            return "MEDIUM", "The headline appears to clearly describe a business, finance, event, or institutional item. Check whether this is a sufficient neutral/no-clear case rather than insufficient evidence."
        return "HIGH", "Model marked this development insufficient. Review the source title/link to decide whether evidence is truly too thin or whether a neutral/directional label is supportable."

    if config in CORE and ("opinion" in lower or "view" in lower or "challenge" in lower or "grow up" in lower):
        return "HIGH", "Two-sided pattern may infer outcomes from attitudes/context rather than explicit effects. Verify both sides independently."

    if config in PARTIAL and NON_RELATIONAL.search(title):
        return "MEDIUM", "One-sided directional label may be inferred from an announcement/institutional event. Verify that the title establishes an actual directional AI or human signal."

    return "NORMAL", "Independent QC: verify both sides and evidence status against the codebook; do not infer a gain or constraint from topic salience alone."


def rows_for_release(sym: dict[str, Any]) -> list[dict[str, Any]]:
    release_id = str(sym.get("release_id") or "")
    rows: list[dict[str, Any]] = []
    for item in sym.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        event_id = str(item.get("event_id") or "")
        sources = [source for source in (item.get("sources") or []) if isinstance(source, dict)]
        priority, note = audit_note(item)
        row = {
            "release_id": release_id,
            "event_id": event_id,
            "unit_key": f"event:{release_id}:{event_id}",
            "event_date": norm(item.get("event_date")),
            "development_title": norm(item.get("event_title")),
            "source_count": len(sources),
            "source_publishers": " | ".join(norm(source.get("publisher")) for source in sources),
            "source_headlines": " | ".join(norm(source.get("headline")) for source in sources),
            "source_urls": " | ".join(norm(source.get("url")) for source in sources),
            "novelty_status": norm(item.get("novelty_status")),
            "qc_stratum": qc_stratum(str(item.get("configuration") or "")),
            "qa_priority": priority,
            "qa_note": note,
            "model_configuration": norm(item.get("configuration")),
            "model_plain_label": norm(item.get("plain_label")),
            "model_human_experience_type": norm(item.get("human_experience_type")),
            "model_ai_expressive_role": norm(item.get("ai_expressive_role")),
            "model_human_direction": norm(item.get("human_direction")),
            "model_ai_direction": norm(item.get("ai_direction")),
            "model_evidence_status": norm(item.get("evidence_status")),
            "model_evidence_summary": norm(item.get("evidence_summary")),
            "model_reasoning": norm(item.get("reasoning")),
            "model_review_status": norm(item.get("review_status")),
            "model_reviewed": bool(item.get("reviewed")),
            "HUMAN_human_experience_type": "",
            "HUMAN_ai_expressive_role": "",
            "HUMAN_evidence_status": "",
            "HUMAN_reasoning": "",
            "HUMAN_include_in_gold": "yes",
            "HUMAN_reviewer_name": "",
            "HUMAN_reviewed_at": "",
        }
        rows.append(row)
    return rows


def deterministic_seed(release_id: str, supplied: str) -> int:
    if supplied:
        try:
            return int(supplied)
        except ValueError:
            pass
        material = supplied
    else:
        material = f"aieo-qc:{release_id}"
    return int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:16], 16)


def sample_stratified(rows: list[dict[str, Any]], sample_size: int, seed: int) -> list[dict[str, Any]]:
    if sample_size <= 0 or sample_size >= len(rows):
        return list(rows)
    rng = random.Random(seed)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row["qc_stratum"])].append(row)
    for group in groups.values():
        rng.shuffle(group)

    order = ["boundary_or_insufficient", "two_sided", "one_sided"]
    selected: list[dict[str, Any]] = []
    # Equal allocation across decision-boundary strata, then fill any unused slots.
    base = max(1, sample_size // len(order))
    for key in order:
        selected.extend(groups.get(key, [])[:base])
    if len(selected) < sample_size:
        selected_ids = {row["event_id"] for row in selected}
        remaining = [row for row in rows if row["event_id"] not in selected_ids]
        rng.shuffle(remaining)
        selected.extend(remaining[: sample_size - len(selected)])
    rng.shuffle(selected)
    return selected[:sample_size]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", default="", help="Weekly release ID; blank means current")
    parser.add_argument("--mode", choices=["all", "stratified_random"], default="stratified_random")
    parser.add_argument("--sample-size", type=int, default=24)
    parser.add_argument("--seed", default="")
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sym_path = symbiosis_path(args.release_id)
    sym = read_json(sym_path)
    release_id = str(sym.get("release_id") or "")
    release = read_json(release_path(release_id))
    expected = int((release.get("counts") or {}).get("ai_relevant_event_records") or 0)
    rows = rows_for_release(sym)
    if expected and len(rows) != expected:
        raise SystemExit(f"QC export mismatch: symbiosis evidence has {len(rows)} rows, weekly release has {expected} developments")

    seed = deterministic_seed(release_id, args.seed)
    selected = list(rows) if args.mode == "all" else sample_stratified(rows, args.sample_size, seed)
    if args.mode == "all":
        # Insufficient/boundary cases first, then high-priority flags, then the rest.
        rank = {"HIGH": 0, "MEDIUM": 1, "NORMAL": 2}
        stratum_rank = {"boundary_or_insufficient": 0, "two_sided": 1, "one_sided": 2}
        selected.sort(key=lambda row: (stratum_rank.get(row["qc_stratum"], 9), rank.get(row["qa_priority"], 9), row["development_title"].casefold()))
    for index, row in enumerate(selected, start=1):
        row["review_order"] = index

    out_dir = DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.output:
        csv_path = Path(args.output)
        if not csv_path.is_absolute():
            csv_path = ROOT / csv_path
    else:
        suffix = "all" if args.mode == "all" else f"sample-{len(selected)}"
        csv_path = out_dir / f"{release_id}-event-qc-{suffix}.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(selected)

    meta = {
        "schema_version": "aieo_symbiosis_qc_export_v1",
        "release_id": release_id,
        "mode": args.mode,
        "seed": seed,
        "weekly_developments": expected,
        "exported_rows": len(selected),
        "model_counts": (sym.get("event") or {}).get("display_configuration_counts") or {},
        "instructions": {
            "blind_review": "Code HUMAN_human_experience_type, HUMAN_ai_expressive_role, and HUMAN_evidence_status from the source evidence before comparing with the model columns.",
            "gold": "Keep HUMAN_include_in_gold=yes only for rows you personally adjudicated.",
        },
        "csv": str(csv_path.relative_to(ROOT) if csv_path.is_relative_to(ROOT) else csv_path),
    }
    meta_path = csv_path.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
