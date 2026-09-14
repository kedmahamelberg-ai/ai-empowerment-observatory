"""Confirmed, bounded writes for the relationship classifier.

Only these two existing tables are writable here. INSERT never becomes an
upsert. A result receives one UUID before retries; its existing natural unique
key also prevents a second row if a response is lost. Run updates use compare-
and-set filters. Human corrections and model outputs are never overwritten.

Standard library only; safe to test in the lightweight classifier environment.
"""
from __future__ import annotations

import copy
import json
import random
import time
import uuid
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from classification_database_reads import (
    ReadBudget, error_code, execute_read, transient_read_error,
)

RESULTS = "symbiosis_classifications"
RUNS = "symbiosis_classification_runs"
KEYS = {
    RESULTS: ("symbiosis_run_id", "lens", "unit_key"),
    RUNS: ("run_key",),
}
PRIMARY_KEYS = {RESULTS: "symbiosis_classification_id", RUNS: "symbiosis_run_id"}
COUNTS = (
    "coverage_unit_count", "event_unit_count", "complete_configuration_count",
    "partial_signal_count", "no_clear_signal_count", "insufficient_evidence_count",
    "review_required_count",
)
RUN_GUARDS = ("status", "completed_at") + COUNTS
MAX_ATTEMPTS = 5
MAX_SECONDS = 180


class SymbiosisWriteError(RuntimeError):
    """Do not count a result as saved or publish after an unconfirmed write."""


class SymbiosisWriteConflict(SymbiosisWriteError):
    """Another saved value must not be overwritten to make a retry succeed."""


def _equivalent(left, right, key=""):
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(
            _equivalent(left[k], right[k], k) for k in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _equivalent(a, b) for a, b in zip(left, right)
        )
    if key == "model_confidence" and isinstance(left, (float, int, Decimal)) and isinstance(right, (float, int, Decimal)):
        # Verified storage type is numeric(5,4); PostgreSQL rounds half away
        # from zero. Preserve the original value in raw_output, compare its
        # stored numeric representation rather than inventing a loose tolerance.
        quantum = Decimal("0.0001")
        return Decimal(str(left)).quantize(quantum, rounding=ROUND_HALF_UP) == Decimal(str(right)).quantize(quantum, rounding=ROUND_HALF_UP)
    if left == right:
        return True
    if key.endswith("_at") and isinstance(left, str) and isinstance(right, str):
        try:
            return datetime.fromisoformat(left.replace("Z", "+00:00")) == datetime.fromisoformat(right.replace("Z", "+00:00"))
        except ValueError:
            return False
    return False


def _matches(row, values):
    return all(key in row and _equivalent(row[key], value, key) for key, value in values.items())


def _immutable_payload(table, payload):
    # A review can be accepted/corrected between the INSERT and confirmation.
    # Verify all model/evidence fields, but return (never reset) the saved review.
    excluded = {PRIMARY_KEYS[table], "created_at", "updated_at"}
    if table == RESULTS:
        excluded.update({"review_status", "reviewer_name", "reviewed_at"})
    return {
        key: value for key, value in payload.items()
        if key not in excluded and not (table == RESULTS and key.startswith("final_"))
    }


def _one(response, label):
    rows = getattr(response, "data", None)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows) or len(rows) > 1:
        raise SymbiosisWriteError("Invalid database response while " + label)
    return rows[0] if rows else None


def _lookup(client, table, identity, budget):
    def operation():
        query = client.table(table).select("*")
        for key, value in identity.items():
            query = query.eq(key, value)
        return query.limit(2).execute()
    response = execute_read(
        operation, label="confirm " + table, attempts=MAX_ATTEMPTS, budget=budget,
    )
    return _one(response, "confirming " + table)


def _wait(attempt, budget, table):
    if attempt >= MAX_ATTEMPTS:
        raise SymbiosisWriteError(
            "Database write remains unconfirmed after %s attempts: %s. "
            "Saved rows are retained; publication must remain blocked."
            % (MAX_ATTEMPTS, table)
        )
    delay = min(8, 2 ** (attempt - 1)) + random.uniform(0, 0.25)
    budget.check()
    if time.monotonic() + delay >= budget.deadline:
        raise SymbiosisWriteError("Database write retry would exceed its time budget")
    print("DB WRITE RETRY %s/%s: %s; waiting %.2fs" % (
        attempt, MAX_ATTEMPTS - 1, table, delay
    ), flush=True)
    time.sleep(delay)


def _response(row):
    return SimpleNamespace(data=[row], count=None)


def insert_row(client, table, payload):
    """Retry the SAME row, not the model call, confirming ambiguous commits.

    The verified database has unique (run,lens,unit) results, unique run_key
    run records, and UUID primary keys for both tables. No migration is needed.
    An existing result with different model/evidence values is a hard conflict.
    """
    if table not in KEYS or not isinstance(payload, dict):
        raise SymbiosisWriteError("Unreviewed table or non-dictionary insert")
    data = json.loads(json.dumps(copy.deepcopy(payload), allow_nan=False))
    identity = {key: data.get(key) for key in KEYS[table]}
    if any(value is None or value == "" for value in identity.values()):
        raise SymbiosisWriteError("Missing unique insert identity for " + table)
    pk = PRIMARY_KEYS[table]
    data.setdefault(pk, str(uuid.uuid4()))
    try:
        data[pk] = str(uuid.UUID(data[pk]))
    except (ValueError, TypeError, AttributeError) as exc:
        raise SymbiosisWriteError("Invalid insert primary key for " + table) from exc
    expected = _immutable_payload(table, data)
    budget = ReadBudget(seconds=MAX_SECONDS, max_requests=40)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        budget.consume()
        duplicate = False
        try:
            # New builder, unchanged payload and primary key on every attempt.
            response = client.table(table).insert(copy.deepcopy(data)).select("*").execute()
        except Exception as exc:
            duplicate = error_code(exc) == "23505"
            if not duplicate and not transient_read_error(exc):
                raise
        else:
            row = _one(response, "inserting " + table)
            if row is not None:
                if str(row.get(pk, "")) != data[pk] or not _matches(row, expected):
                    raise SymbiosisWriteConflict("Inserted row did not match its payload: " + table)
                return _response(row)
            # Empty successful response still requires proof of persistence.
        row = _lookup(client, table, identity, budget)
        if row is not None:
            if not row.get(pk) or not _matches(row, expected):
                raise SymbiosisWriteConflict("Existing row differs; no overwrite performed: " + table)
            print("DB WRITE CONFIRMED: %s; saved row reused" % table, flush=True)
            return _response(row)
        if duplicate:
            raise SymbiosisWriteConflict("Unique-key error without a matching row: " + table)
        # Confirmation read succeeded and saw no row. The fixed UUID and unique
        # key still protect a late commit between this read and the next INSERT.
        _wait(attempt, budget, table)
    raise AssertionError("Unreachable insert state")


def update_run(client, run_id, payload):
    """Safely resume/checkpoint/finish one run with optimistic concurrency."""
    if not isinstance(payload, dict) or not payload or set(payload) - set(RUN_GUARDS):
        raise SymbiosisWriteError("Unreviewed run-update fields")
    data = json.loads(json.dumps(copy.deepcopy(payload), allow_nan=False))
    if data.get("status") not in {"running", "success", "partial", "failed"}:
        raise SymbiosisWriteError("A valid run status is required")
    for key in COUNTS:
        if key in data and (type(data[key]) is not int or data[key] < 0):
            raise SymbiosisWriteError("Run counts must be non-negative integers")
    budget = ReadBudget(seconds=MAX_SECONDS, max_requests=40)
    identity = {"symbiosis_run_id": str(run_id)}
    baseline = _lookup(client, RUNS, identity, budget)
    if baseline is None or any(key not in baseline for key in RUN_GUARDS):
        raise SymbiosisWriteError("Run missing or incomplete during update confirmation")
    if _matches(baseline, data):
        return _response(baseline)
    if baseline["status"] == "success":
        raise SymbiosisWriteConflict("Refusing to reopen or downgrade a successful run")
    guards = {key: baseline[key] for key in RUN_GUARDS}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        budget.consume()
        try:
            query = client.table(RUNS).update(copy.deepcopy(data)).eq("symbiosis_run_id", str(run_id))
            for key, value in guards.items():
                query = query.is_(key, "null") if value is None else query.eq(key, value)
            response = query.select("*").execute()
        except Exception as exc:
            if not transient_read_error(exc):
                raise
        else:
            row = _one(response, "updating relationship run")
            if row is not None:
                if str(row.get("symbiosis_run_id", "")) != str(run_id) or not _matches(row, data):
                    raise SymbiosisWriteConflict("Run update response did not match")
                return _response(row)
        observed = _lookup(client, RUNS, identity, budget)
        if observed is not None and _matches(observed, data):
            print("DB WRITE CONFIRMED: relationship run status saved", flush=True)
            return _response(observed)
        if observed is None or not _matches(observed, guards):
            raise SymbiosisWriteConflict("Run changed during recovery; no overwrite performed")
        _wait(attempt, budget, RUNS)
    raise AssertionError("Unreachable run-update state")
