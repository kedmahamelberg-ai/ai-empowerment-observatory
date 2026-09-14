"""Bounded database recovery for the event resolver, not its AI calls.

SELECTs are rebuilt and retried, with small ordered pages. Keyed writes retain
one payload. After an ambiguous response, confirm the row before retrying.
No extraction, model, or Supabase dependency is imported by this module.
"""
from __future__ import annotations
import copy
import json
import random
import time
import uuid
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from classification_database_reads import ReadBudget, error_code, execute_read, transient_read_error

PRIMARY_KEYS = {
    "collection_runs": "run_id", "article_observations": "observation_id",
    "articles": "article_id", "article_translations": "translation_id",
    "model_versions": "model_version_id", "event_resolution_runs": "resolution_run_id",
    "events": "event_id", "event_articles": "event_article_id",
    "event_assignment_decisions": "assignment_decision_id",
}
UPSERT_KEYS = {
    "model_versions": ("provider", "model_name", "model_revision", "task"),
    "events": ("canonical_event_key",), "event_articles": ("event_id", "article_id"),
    "event_assignment_decisions": ("resolution_run_id", "article_id"),
}
UPDATE_KEYS = {"events": "event_id", "event_resolution_runs": "resolution_run_id"}
METHODS = frozenset({"select", "eq", "in_", "order", "limit", "range", "insert", "upsert", "update"})
WRITES = frozenset({"insert", "upsert", "update"})

class ResolverDatabaseError(RuntimeError):
    """A failed read or uncertain write is not permission to publish."""

class ResolverWriteConflict(ResolverDatabaseError):
    """Refuse to overwrite an independently changed row during recovery."""


def equivalent(a, b, field=""):
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if a == b:
        return True
    if isinstance(a, (float, int, Decimal)) and isinstance(b, (float, int, Decimal)):
        return Decimal(str(a)) == Decimal(str(b))
    if field.endswith("_at") and isinstance(a, str) and isinstance(b, str):
        try:
            return datetime.fromisoformat(a.replace("Z", "+00:00")) == datetime.fromisoformat(b.replace("Z", "+00:00"))
        except ValueError:
            pass
    return False


def matches(row, expected):
    return all(k in row and equivalent(row[k], v, k) for k, v in expected.items())


def rows_of(response):
    rows = getattr(response, "data", None)
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ResolverDatabaseError("The resolver received an invalid database row list")
    return rows


class ResolverDatabase:
    def __init__(self, client, *, attempts=5, seconds=300, page_size=50, batch_size=25, sleep=None):
        if not 1 <= attempts <= 5 or seconds <= 0 or min(page_size, batch_size) < 1:
            raise ValueError("Invalid event-resolver database limits")
        self.client, self.attempts, self.seconds = client, attempts, seconds
        self.page_size, self.batch_size, self.sleep = page_size, batch_size, sleep or time.sleep

    def table(self, table):
        if table not in PRIMARY_KEYS:
            raise ResolverDatabaseError("Unreviewed resolver database table: " + str(table))
        return ResolverQuery(self, table)

    def read(self, operation, table, budget):
        return execute_read(operation, label="event resolver " + table, attempts=self.attempts, budget=budget)


class ResolverQuery:
    def __init__(self, db, table, steps=()):
        self.db, self.table_name, self.steps = db, table, steps

    def __getattr__(self, name):
        if name not in METHODS:
            raise AttributeError("Unsupported resolver database operation: " + name)
        def add(*args, **kwargs):
            args, kwargs = copy.deepcopy(args), copy.deepcopy(kwargs)
            if name in WRITES:
                if any(s[0] in WRITES for s in self.steps) or not args or not isinstance(args[0], dict):
                    raise ResolverDatabaseError("Only one single-row write is supported per query")
                if name == "insert":
                    if self.table_name != "event_resolution_runs":
                        raise ResolverDatabaseError("Unkeyed resolver insert is not supported")
                    args[0].setdefault("resolution_run_id", str(uuid.uuid4()))
            return ResolverQuery(self.db, self.table_name, self.steps + ((name, args, kwargs),))
        return add

    def raw(self, steps=None):
        query = self.db.client.table(self.table_name)
        for method, args, kwargs in self.steps if steps is None else steps:
            query = getattr(query, method)(*copy.deepcopy(args), **copy.deepcopy(kwargs))
        return query

    def execute(self):
        budget = ReadBudget(seconds=self.db.seconds, max_requests=512)
        writes = [s for s in self.steps if s[0] in WRITES]
        if writes:
            return self.write(writes[0], budget)
        if not self.steps or self.steps[0][0] != "select":
            raise ResolverDatabaseError("The resolver requires a SELECT or a keyed write")
        return self.read(self.steps, budget)

    def read(self, steps, budget):
        if any(s[0] in {"limit", "range"} for s in steps):
            response = self.db.read(lambda: self.raw(steps).execute(), self.table_name, budget)
            rows_of(response)
            return response
        # Keep every row even if the API's response limit is smaller than ours.
        for i, (method, args, kwargs) in enumerate(steps):
            if method == "in_" and len(args[1]) > self.db.batch_size:
                values = list(dict.fromkeys(args[1]))
                rows, counts = [], []
                for start in range(0, len(values), self.db.batch_size):
                    step = (method, (args[0], values[start:start + self.db.batch_size]), kwargs)
                    response = self.read(steps[:i] + (step,) + steps[i + 1:], budget)
                    rows.extend(rows_of(response)); counts.append(getattr(response, "count", None))
                return SimpleNamespace(data=rows, count=sum(counts) if all(isinstance(n, int) for n in counts) else None)
        pk = PRIMARY_KEYS[self.table_name]
        method, args, kwargs = steps[0]
        columns = args[0] if args else "*"
        if columns != "*" and pk not in columns.split(","):
            steps = ((method, (pk + "," + columns,) + args[1:], kwargs),) + steps[1:]
        if not any(s[0] == "order" and s[1][0] == pk for s in steps):
            steps += (("order", (pk,), {}),)
        output, offset, seen, count = [], 0, set(), None
        while True:
            paged = steps + (("range", (offset, offset + self.db.page_size - 1), {}),)
            response = self.db.read(lambda: self.raw(paged).execute(), self.table_name, budget)
            rows = rows_of(response)
            if offset == 0:
                count = getattr(response, "count", None)
            if not rows:
                return SimpleNamespace(data=output, count=count)
            ids = [str(r.get(pk) or "") for r in rows]
            if any(not key or key in seen for key in ids) or len(ids) != len(set(ids)):
                raise ResolverDatabaseError("Repeated/missing row identity in resolver pagination: " + self.table_name)
            seen.update(ids); output.extend(rows); offset += len(rows)

    def identity(self, write):
        method, args, kwargs = write
        payload = args[0]
        if method == "upsert":
            keys = UPSERT_KEYS.get(self.table_name)
            if not keys or tuple(str(kwargs.get("on_conflict", "")).split(",")) != keys or kwargs.get("ignore_duplicates"):
                raise ResolverDatabaseError("Unreviewed resolver upsert constraint")
            if any(payload.get(k) is None for k in keys):
                raise ResolverDatabaseError("Missing resolver upsert identity")
            return {k: payload[k] for k in keys}
        if method == "insert":
            return {"resolution_run_id": payload["resolution_run_id"]}
        pk = UPDATE_KEYS.get(self.table_name)
        filters = [s[1] for s in self.steps if s[0] == "eq"]
        if not pk or len(filters) != 1 or filters[0][0] != pk or filters[0][1] is None:
            raise ResolverDatabaseError("A resolver update must target one primary key")
        if any(s[0] not in {"update", "eq", "select"} for s in self.steps):
            raise ResolverDatabaseError("Unreviewed filter on resolver update")
        return {pk: filters[0][1]}

    def lookup(self, identity, budget):
        def operation():
            query = self.db.client.table(self.table_name).select("*")
            for key, value in identity.items():
                query = query.eq(key, value)
            return query.limit(2).execute()
        rows = rows_of(self.db.read(operation, self.table_name, budget))
        if len(rows) > 1:
            raise ResolverWriteConflict("The resolver's unique key returned multiple rows")
        return rows[0] if rows else None

    def write(self, write, budget):
        method, args, _ = write
        payload = args[0]; identity = self.identity(write)
        baseline = self.lookup(identity, budget)
        if method == "update" and baseline is None:
            raise ResolverDatabaseError("Resolver update target does not exist")
        if baseline is not None and matches(baseline, payload):
            return SimpleNamespace(data=[baseline], count=None)
        if method == "insert" and baseline is not None:
            raise ResolverWriteConflict("Resolver insert identity already exists with different data")
        uncertain = False
        for attempt in range(1, self.db.attempts + 1):
            budget.consume()
            try:
                response = self.raw().execute()
            except Exception as exc:
                duplicate = uncertain and method == "insert" and error_code(exc) == "23505"
                if not transient_read_error(exc) and not duplicate:
                    raise
                uncertain = True
                # Failed confirmation must raise, never mean 'not committed'.
                observed = self.lookup(identity, budget)
                if observed is not None and matches(observed, payload):
                    print("DB WRITE CONFIRMED: " + self.table_name + "; saved row reused", flush=True)
                    return SimpleNamespace(data=[observed], count=None)
                unchanged = (observed is None and baseline is None) or (
                    observed is not None and baseline is not None and set(observed) == set(baseline) and matches(observed, baseline))
                if not unchanged or duplicate:
                    raise ResolverWriteConflict("Resolver row changed during uncertain write: " + self.table_name) from exc
                if attempt == self.db.attempts:
                    raise ResolverDatabaseError("Resolver write still unavailable after bounded retries: " + self.table_name) from exc
                delay = min(16, 2 ** attempt) + random.uniform(0, .5)
                budget.check()
                if time.monotonic() + delay >= budget.deadline:
                    raise ResolverDatabaseError("Resolver write retry would exceed its time budget") from exc
                print("DB WRITE RETRY %s/%s: %s (code %s)" % (attempt, self.db.attempts - 1, self.table_name, error_code(exc) or "transport"), flush=True)
                self.db.sleep(delay)
                continue
            rows = rows_of(response)
            if len(rows) == 1:
                return response
            if not rows:
                observed = self.lookup(identity, budget)
                if observed is not None and matches(observed, payload):
                    return SimpleNamespace(data=[observed], count=None)
            raise ResolverDatabaseError("Resolver write could not be confirmed: " + self.table_name)
        raise AssertionError("Unreachable resolver retry state")


def resolver_collection(client, env):
    """A weekly retry uses its own saved collection, never a different week."""
    columns = "run_id,run_key,started_at,completed_at,status"
    explicit = str(env.get("AIEO_COLLECTION_RUN_ID") or "").strip()
    workflow_id = str(env.get("GITHUB_RUN_ID") or "").strip()
    if explicit:
        try:
            explicit = str(uuid.UUID(explicit))
        except ValueError as exc:
            raise ResolverDatabaseError("Invalid explicit collection UUID") from exc
        rows = rows_of(client.table("collection_runs").select(columns).eq("run_id", explicit).in_("status", ["success", "partial"]).limit(1).execute())
        if not rows:
            raise ResolverDatabaseError("Requested collection not complete; no fallback to another week")
        return rows[0]
    if workflow_id:
        if not workflow_id.isdigit():
            raise ResolverDatabaseError("Invalid GitHub run ID")
        rows = rows_of(client.table("collection_runs").select(columns).eq("workflow_run_id", workflow_id).in_("status", ["success", "partial"]).order("started_at", desc=True).limit(1).execute())
        if rows:
            print("RESOLVER COLLECTION: saved collection for GitHub run " + workflow_id, flush=True)
            return rows[0]
        if env.get("GITHUB_WORKFLOW") == "Weekly Observatory Pipeline":
            raise ResolverDatabaseError("This workflow has no saved collection; refusing a different week's data")
    rows = rows_of(client.table("collection_runs").select(columns).in_("status", ["success", "partial"]).order("started_at", desc=True).limit(1).execute())
    if not rows:
        raise ResolverDatabaseError("No completed collection available")
    return rows[0]
