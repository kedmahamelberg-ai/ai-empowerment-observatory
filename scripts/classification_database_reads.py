"""Bounded, read-only Supabase recovery shared by both classifiers.

No extraction, model or Supabase dependency is imported here. Never retry a
write through this module, or turn a failed read into an empty evidence set.
"""
from __future__ import annotations

import json
import time

TRANSIENT_CODES = frozenset({
    "408", "425", "429", "500", "502", "503", "504", "520", "522", "524",
    "57014", "53300", "57P01", "57P02", "57P03", "40001", "40P01",
    "PGRST000", "PGRST001", "PGRST002", "PGRST003",
})
NETWORK_ERRORS = frozenset({
    "TimeoutException", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
    "PoolTimeout", "ConnectError", "ReadError", "RemoteProtocolError",
    "ConnectionError", "Timeout",
})
PRIMARY_KEYS = {
    "articles": "article_id",
    "article_translations": "translation_id",
    "article_observations": "observation_id",
    "brief_article_content_snapshots": "snapshot_id",
    "symbiosis_classifications": "symbiosis_classification_id",
    "symbiosis_classification_runs": "symbiosis_run_id",
}


class DatabaseReadError(RuntimeError):
    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def error_code(exc):
    """Accept both integer and string codes, including PostgREST proxy errors."""
    for value in (getattr(exc, "code", None), getattr(exc, "status_code", None),
                  getattr(getattr(exc, "response", None), "status_code", None)):
        if value is not None:
            return str(value).strip().upper()
    for value in getattr(exc, "args", ()):
        if isinstance(value, dict):
            code = value.get("code", value.get("status_code"))
            if code is not None:
                return str(code).strip().upper()
    return ""


def transient_read_error(exc):
    code = error_code(exc)
    if code:
        # A structured permission/schema code takes priority over error prose.
        return code in TRANSIENT_CODES or code.startswith("08")
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    for cls in type(exc).__mro__:
        if cls.__module__.split(".")[0] in {"httpx", "httpcore", "requests", "urllib3"}:
            if cls.__name__ in NETWORK_ERRORS:
                return True
    if type(exc).__name__ == "APIError":
        # Some SDK versions omit a numeric proxy code. Do not catch arbitrary
        # ValueError/Pydantic/model failures simply because their text says JSON.
        text = str(exc).lower()
        return any(term in text for term in (
            "gateway timeout", "bad gateway", "service unavailable",
            "connection reset", "timed out acquiring connection",
        ))
    return False


class ReadBudget:
    def __init__(self, seconds=300, max_requests=512):
        if seconds <= 0 or max_requests < 1:
            raise ValueError("Database read limits must be positive")
        self.deadline = time.monotonic() + seconds
        self.max_requests = max_requests
        self.requests = 0

    def check(self):
        if time.monotonic() >= self.deadline or self.requests >= self.max_requests:
            raise DatabaseReadError(
                "Database read budget exhausted. Saved data remain unchanged; "
                "classification/publication must not use a partial input set."
            )

    def consume(self):
        self.check()
        self.requests += 1


def execute_read(operation, *, label="classification input", attempts=5, budget=None):
    """Retry a SELECT operation, not the classification loop or model call."""
    if attempts < 1:
        raise ValueError("At least one read attempt is required")
    budget = budget or ReadBudget()
    for attempt in range(1, attempts + 1):
        budget.consume()
        try:
            response = operation()
        except Exception as exc:
            if not transient_read_error(exc):
                raise
            if attempt == attempts:
                raise DatabaseReadError(
                    "Database read still unavailable after %s attempts: %s. "
                    "No empty-evidence fallback was used." % (attempts, label),
                    retryable=True,
                ) from exc
            delay = min(8, 2 ** (attempt - 1))
            budget.check()
            if time.monotonic() + delay >= budget.deadline:
                raise DatabaseReadError("Database read retry would exceed its time budget") from exc
            # Never log source text, credentials, URL query strings or raw DB errors.
            print("DB READ RETRY %s/%s: %s (code %s); waiting %ss" % (
                attempt, attempts - 1, label, error_code(exc) or "transport", delay
            ), flush=True)
            time.sleep(delay)
            continue
        if time.monotonic() >= budget.deadline:
            raise DatabaseReadError("Database response arrived after the read time budget")
        return response
    raise AssertionError("Unreachable read retry state")


def read_rows(client, table, columns, *, apply=None, pk=None, page_size=50,
              budget=None, attempts=5):
    """Read all pages in stable primary-key order, even below the API row cap."""
    if page_size < 1:
        raise ValueError("page_size must be positive")
    pk = pk or PRIMARY_KEYS[table]
    budget = budget or ReadBudget()
    selected = columns if columns == "*" or pk in columns.split(",") else pk + "," + columns
    result, start, pages = [], 0, set()
    while True:
        def operation():
            query = client.table(table).select(selected)
            if apply is not None:
                query = apply(query)
            return query.order(pk).range(start, start + page_size - 1).execute()
        response = execute_read(operation, label="read " + table, attempts=attempts, budget=budget)
        rows = getattr(response, "data", None)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise DatabaseReadError("Invalid row list returned while reading " + table)
        if not rows:
            return result
        fingerprint = json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str)
        if fingerprint in pages:
            raise DatabaseReadError("Non-advancing database page while reading " + table)
        pages.add(fingerprint)
        result.extend(rows)
        # A short page is not necessarily the last page: server row caps can
        # be lower than requested. Finish only after an actual empty response.
        start += len(rows)


def read_id_rows(client, table, columns, values, *, key="article_id", apply=None,
                 pk=None, batch_size=25, page_size=50, budget=None):
    """Small ID batches; split persistent timeout batches without omitting rows."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    ids = sorted(set(str(value) for value in values))
    if not ids:
        return []
    budget = budget or ReadBudget()

    def group(batch):
        def filtered(query):
            query = query.in_(key, batch)
            return apply(query) if apply is not None else query
        try:
            return read_rows(client, table, columns, apply=filtered, pk=pk,
                             page_size=page_size, budget=budget,
                             attempts=2 if len(batch) > 1 else 5)
        except DatabaseReadError as exc:
            if not exc.retryable or len(batch) < 2:
                raise
            budget.check()
            middle = len(batch) // 2
            print("DB READ SPLIT: %s, %s IDs into %s + %s" % (
                table, len(batch), middle, len(batch) - middle
            ), flush=True)
            # Failed parent pages were only buffered locally. Discard them and
            # re-read both disjoint subsets, so neither duplicates nor gaps escape.
            return group(batch[:middle]) + group(batch[middle:])

    result = []
    for start in range(0, len(ids), batch_size):
        result.extend(group(ids[start:start + batch_size]))
    return result
