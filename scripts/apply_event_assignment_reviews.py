#!/usr/bin/env python3
"""Apply human decisions from validation/event_assignment_reviews.csv.

Expected CSV columns:
  assignment_decision_id
  final_decision   # merge_candidate | keep_separate | defer
  notes            # optional

This keeps the static GitHub review UI private/simple while retaining a
fully auditable Supabase review history.
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supabase import Client, create_client

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "validation" / "event_assignment_reviews.csv"

METHOD_NAME = "article_to_event_v1"


class ReviewApplyError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise ReviewApplyError(f"{name} is missing.")
    return value


def iso_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def first_row(response: Any, context: str) -> dict[str, Any]:
    data = getattr(response, "data", None)

    if isinstance(data, list) and data:
        return data[0]

    if isinstance(data, dict) and data:
        return data

    raise ReviewApplyError(f"No row while {context}.")


def load_decision(
    client: Client,
    decision_id: str,
) -> dict[str, Any]:
    response = (
        client.table("event_assignment_decisions")
        .select(
            "assignment_decision_id,article_id,decision,"
            "candidate_event_id,assigned_event_id"
        )
        .eq("assignment_decision_id", decision_id)
        .limit(1)
        .execute()
    )

    return first_row(
        response,
        "loading assignment decision",
    )


def main() -> int:
    if not CSV_PATH.exists():
        raise ReviewApplyError(
            f"Missing review CSV: {CSV_PATH}"
        )

    client: Client = create_client(
        required_env("SUPABASE_URL"),
        required_env("SUPABASE_SECRET_KEY"),
    )

    rows = list(
        csv.DictReader(
            CSV_PATH.open(
                "r",
                encoding="utf-8-sig",
                newline="",
            )
        )
    )

    if not rows:
        raise ReviewApplyError(
            "Review CSV is empty."
        )

    applied = 0
    deferred = 0

    for row in rows:
        decision_id = str(
            row.get("assignment_decision_id") or ""
        ).strip()

        final_decision = str(
            row.get("final_decision") or ""
        ).strip()

        notes = str(
            row.get("notes") or ""
        ).strip()

        if not decision_id or not final_decision:
            continue

        if final_decision not in {
            "merge_candidate",
            "keep_separate",
            "defer",
        }:
            raise ReviewApplyError(
                f"Invalid final_decision: {final_decision}"
            )

        decision = load_decision(
            client,
            decision_id,
        )

        article_id = str(
            decision["article_id"]
        )

        candidate_event_id = decision.get(
            "candidate_event_id"
        )

        provisional_event_id = decision.get(
            "assigned_event_id"
        )

        final_event_id = provisional_event_id

        if final_decision == "merge_candidate":
            if not candidate_event_id:
                raise ReviewApplyError(
                    f"{decision_id} has no candidate_event_id."
                )

            # Remove the article from its separate pending event.
            if provisional_event_id:
                (
                    client.table("event_articles")
                    .delete()
                    .eq("event_id", provisional_event_id)
                    .eq("article_id", article_id)
                    .execute()
                )

            # Link it to the accepted candidate event.
            (
                client.table("event_articles")
                .upsert(
                    {
                        "event_id": candidate_event_id,
                        "article_id": article_id,
                        "is_canonical_source": False,
                    },
                    on_conflict="event_id,article_id",
                )
                .execute()
            )

            if provisional_event_id:
                (
                    client.table("events")
                    .update(
                        {
                            "event_state": "retired",
                            "requires_cluster_review": False,
                            "cluster_review_reason": (
                                "retired after human-approved merge"
                            ),
                            "updated_at": iso_now(),
                        }
                    )
                    .eq("event_id", provisional_event_id)
                    .execute()
                )

            (
                client.table("events")
                .update(
                    {
                        "event_state": "active",
                        "requires_cluster_review": False,
                        "cluster_review_reason": None,
                        "updated_at": iso_now(),
                    }
                )
                .eq("event_id", candidate_event_id)
                .execute()
            )

            (
                client.table("event_assignment_decisions")
                .update(
                    {
                        "decision": "review_resolved_merge",
                        "assigned_event_id": candidate_event_id,
                        "requires_review": False,
                        "review_reason": None,
                        "updated_at": iso_now(),
                    }
                )
                .eq("assignment_decision_id", decision_id)
                .execute()
            )

            final_event_id = candidate_event_id
            applied += 1

        elif final_decision == "keep_separate":
            if not provisional_event_id:
                raise ReviewApplyError(
                    f"{decision_id} has no provisional event."
                )

            (
                client.table("events")
                .update(
                    {
                        "event_state": "active",
                        "requires_cluster_review": False,
                        "cluster_review_reason": None,
                        "updated_at": iso_now(),
                    }
                )
                .eq("event_id", provisional_event_id)
                .execute()
            )

            (
                client.table("event_assignment_decisions")
                .update(
                    {
                        "decision": "review_resolved_separate",
                        "requires_review": False,
                        "review_reason": None,
                        "updated_at": iso_now(),
                    }
                )
                .eq("assignment_decision_id", decision_id)
                .execute()
            )

            applied += 1

        else:
            deferred += 1

        (
            client.table("event_assignment_reviews")
            .insert(
                {
                    "assignment_decision_id": decision_id,
                    "reviewer_name": "Kedma Hamelberg",
                    "final_decision": final_decision,
                    "final_event_id": final_event_id,
                    "notes": notes or None,
                    "reviewed_at": iso_now(),
                }
            )
            .execute()
        )

    print(f"Applied reviews: {applied}")
    print(f"Deferred reviews: {deferred}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReviewApplyError as exc:
        print(
            f"Applying event reviews failed: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
