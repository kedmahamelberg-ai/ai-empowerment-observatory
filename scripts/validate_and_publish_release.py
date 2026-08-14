#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

LENSES = ROOT / "data" / "lenses" / "latest.json"
EVENTS = ROOT / "data" / "events" / "latest.json"
CLASSIFICATION = ROOT / "review" / "classification" / "latest.json"
METHODOLOGY = ROOT / "data" / "methodology" / "latest.json"
STATUS = ROOT / "data" / "status" / "latest.json"


class ReleaseError(RuntimeError):
    pass


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ReleaseError(f"Required release artifact is missing: {path}")

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReleaseError(f"Invalid JSON: {path}") from exc


def check_index(value: Any, name: str) -> float:
    if value is None:
        raise ReleaseError(f"{name} is null.")

    value = float(value)

    if not -100 <= value <= 100:
        raise ReleaseError(f"{name} is out of range: {value}")

    return value


def main() -> int:
    lenses = load(LENSES)
    events = load(EVENTS)
    classification = load(CLASSIFICATION)

    global_data = lenses.get("global") or {}
    coverage = global_data.get("coverage") or {}
    event = global_data.get("event") or {}
    amplification = global_data.get("amplification") or {}

    coverage_index = check_index(
        coverage.get("empowerment_index"),
        "Coverage Empowerment Index",
    )
    event_index = check_index(
        event.get("empowerment_index"),
        "Event Empowerment Index",
    )

    gap = amplification.get("directional_amplification_gap")

    if gap is None:
        raise ReleaseError("Directional Amplification Gap is null.")

    coverage_n = int(coverage.get("unit_count_total") or 0)
    event_n = int(event.get("unit_count_total") or 0)

    if coverage_n <= 0 or event_n <= 0:
        raise ReleaseError(
            f"Invalid lens unit counts: coverage={coverage_n}, event={event_n}"
        )

    event_meta = events.get("meta") or {}
    active_events = int(event_meta.get("active_event_count") or 0)

    if active_events <= 0:
        raise ReleaseError("No active events in Event Lens output.")

    lens_meta = lenses.get("meta") or {}
    release_status = str(
        lens_meta.get("release_status")
        or "provisional_automated"
    )

    audited = release_status.startswith("human_audited")

    status = {
        "generated_at": (
            datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        ),
        "system_status": "operational",
        "release_status": release_status,
        "human_audited": audited,
        "structural_gate": "passed",
        "latest": {
            "coverage_units": coverage_n,
            "event_units": event_n,
            "active_events": active_events,
            "coverage_empowerment_index": coverage_index,
            "event_empowerment_index": event_index,
            "directional_amplification_gap": float(gap),
            "review_queue_count": int(
                (classification.get("meta") or {}).get(
                    "review_queue_count",
                    len(classification.get("review_queue") or []),
                )
                or 0
            ),
        },
        "governance": {
            "weekly_publication_blocks_on": [
                "missing or invalid artifacts",
                "unassigned coverage units",
                "null or out-of-range indices",
                "workflow or storage failure"
            ],
            "weekly_publication_does_not_block_on": [
                "pending stratified classification audit",
                "ambiguous event merge review",
                "ordinary model-confidence uncertainty"
            ],
            "human_role": (
                "asynchronous governance, periodic stratified audit, "
                "high-risk review and methodology version approval"
            ),
        },
    }

    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(status, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseError as exc:
        print(f"Release gate failed: {exc}")
        raise SystemExit(1)
