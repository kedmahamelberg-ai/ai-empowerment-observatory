"""Read publication inputs without joining or transferring classification history.

All operations are reads. Inspect small metadata pages first, check run status
and classifier version, then fetch the complete selected rows by primary key.
Never replace a failed query with an empty result or a cached public snapshot.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Iterator

import httpx


class PublicationReadError(RuntimeError):
    pass


CLASSIFICATION_TABLE = "symbiosis_classifications"
CLASSIFICATION_PK = "symbiosis_classification_id"
RUN_TABLE = "symbiosis_classification_runs"
RUN_PK = "symbiosis_run_id"
METADATA_COLUMNS = (
    "symbiosis_classification_id,symbiosis_run_id,article_id,event_id,"
    "created_at,release_id,lens,codebook_version"
)
REVIEW_STATUSES = ("accepted", "corrected", "insufficient_evidence")
TRANSIENT_CODES = {
    "57014", "53300", "53400", "57P01", "57P02", "57P03", "40001", "40P01",
    "PGRST000", "PGRST001", "PGRST002", "PGRST003", "429", "502", "503", "504",
}


def transient_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 502, 503, 504}
    return str(getattr(exc, "code", "")) in TRANSIENT_CODES


class PublicationReader:
    """Bound pages, retries and total time for a single publication invocation."""

    def __init__(self, client: Any, *, budget_seconds: float = 480,
                 batch_size: int = 25, page_size: int = 50):
        if budget_seconds <= 0 or batch_size < 1 or page_size < 1:
            raise ValueError("Publication read limits must be positive.")
        self.client = client
        self.deadline = time.monotonic() + budget_seconds
        self.batch_size = batch_size
        self.page_size = page_size
        self.run_states: dict[str, dict[str, Any]] = {}

    def check_budget(self) -> None:
        if time.monotonic() >= self.deadline:
            raise PublicationReadError(
                "The publication database-read budget was reached. Saved classifications "
                "remain unchanged; retry Publish Observatory Release after database recovery."
            )

    def _group(self, *, table: str, columns: str, pk: str, key: str,
               values: list[str], apply: Callable[[Any], Any],
               after: str = "", page_size: int | None = None) -> Iterator[dict[str, Any]]:
        size = page_size or self.page_size
        retries = 0
        while True:
            self.check_budget()
            # A primary-key lookup already supplies the finite ID set. Remove
            # read IDs from that set instead of applying two filters to one key.
            pending = [value for value in values if key != pk or value > after]
            if not pending:
                return
            query = apply(self.client.table(table).select(columns).in_(key, pending))
            query = query.order(pk)
            if after and key != pk:
                query = query.gt(pk, after)
            query = query.limit(size)
            try:
                response = query.execute()
            except Exception as exc:
                if not transient_error(exc):
                    raise PublicationReadError(
                        f"Reading {table} failed with {type(exc).__name__} "
                        f"(code {getattr(exc, 'code', 'not supplied')}). "
                        "Check database access/schema; publication has stopped."
                    ) from exc
                self.check_budget()
                if len(values) > 1:
                    middle = len(values) // 2
                    print(f"Database read interrupted for {table}; splitting "
                          f"{len(values)} IDs into smaller requests.", flush=True)
                    for subset in (values[:middle], values[middle:]):
                        yield from self._group(table=table, columns=columns, pk=pk,
                                               key=key, values=subset, apply=apply,
                                               after=after, page_size=max(1, size // 2))
                    return
                if retries >= 2:
                    raise PublicationReadError(
                        f"Reading {table} still fails for a single ID after three attempts. "
                        "Saved classifications remain unchanged. Check Supabase query "
                        "performance, then retry Publish Observatory Release."
                    ) from exc
                retries += 1
                size = max(1, size // 2)
                print(f"Database read interrupted for {table}; retry {retries}/2 "
                      f"with at most {size} rows.", flush=True)
                time.sleep(retries)
                continue
            self.check_budget()
            rows = getattr(response, "data", None)
            if not isinstance(rows, list):
                raise PublicationReadError(f"Reading {table} returned an invalid row list.")
            if not rows:
                return
            # Continue until an empty page, even when the API enforces a lower
            # row cap than requested. The unique-key cursor prevents omissions.
            for row in rows:
                row_id = str(row.get(pk) or "") if isinstance(row, dict) else ""
                if not row_id or row_id <= after:
                    raise PublicationReadError(f"Reading {table} returned a non-advancing page.")
                after = row_id
                yield row
            retries = 0

    def rows(self, *, table: str, columns: str, pk: str, key: str,
             values: list[str], apply: Callable[[Any], Any] = lambda q: q
             ) -> list[dict[str, Any]]:
        result = []
        unique = sorted(set(values))
        for start in range(0, len(unique), self.batch_size):
            result.extend(self._group(table=table, columns=columns, pk=pk, key=key,
                                      values=unique[start:start + self.batch_size], apply=apply))
        return result

    def _runs(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        missing = sorted(set(ids) - self.run_states.keys())
        fetched = self.rows(table=RUN_TABLE, columns="symbiosis_run_id,status,classifier_version",
                            pk=RUN_PK, key=RUN_PK, values=missing)
        self.run_states.update({str(row[RUN_PK]): row for row in fetched})
        if set(ids) - self.run_states.keys():
            raise PublicationReadError("Classification run metadata is missing; publication has stopped.")
        return self.run_states

    def _details(self, selected: dict[str, dict[str, Any]], column: str
                 ) -> dict[str, dict[str, Any]]:
        ids = [str(row[CLASSIFICATION_PK]) for row in selected.values()]
        rows = self.rows(table=CLASSIFICATION_TABLE, columns="*", pk=CLASSIFICATION_PK,
                         key=CLASSIFICATION_PK, values=ids)
        by_id = {str(row[CLASSIFICATION_PK]): row for row in rows}
        if set(by_id) != set(ids):
            raise PublicationReadError("Selected saved classifications could not all be read; publication has stopped.")
        result = {}
        for unit_id, metadata in selected.items():
            row = by_id[str(metadata[CLASSIFICATION_PK])]
            # A concurrent change or mismatched response must not silently bind
            # another release, run, review or source to the chosen result.
            if any(row.get(key) != value for key, value in metadata.items()):
                raise PublicationReadError("Saved classification metadata changed during publication; retry publication.")
            if str(row.get(column) or "") != unit_id:
                raise PublicationReadError("Saved classification unit does not match its selected result.")
            result[unit_id] = row
        return result

    def latest(self, *, release_id: str, lens: str, ids: list[str],
               codebook_version: str, classifier_version: str) -> dict[str, dict[str, Any]]:
        if lens not in {"coverage", "event"}:
            raise ValueError("Unknown relationship lens.")
        if not ids:
            return {}
        column = "article_id" if lens == "coverage" else "event_id"
        print(f"Reading {lens} classification metadata for {len(set(ids))} units.", flush=True)
        metadata = self.rows(
            table=CLASSIFICATION_TABLE, columns=METADATA_COLUMNS, pk=CLASSIFICATION_PK,
            key=column, values=ids,
            apply=lambda q: q.eq("release_id", release_id).eq("lens", lens)
                            .eq("codebook_version", codebook_version),
        )
        states = self._runs([str(row.get(RUN_PK) or "") for row in metadata])
        selected: dict[str, dict[str, Any]] = {}
        for row in sorted(metadata, key=lambda r: (str(r.get("created_at") or ""),
                                                    str(r[CLASSIFICATION_PK])), reverse=True):
            run = states[str(row[RUN_PK])]
            if run.get("status") == "success" and run.get("classifier_version") == classifier_version:
                selected.setdefault(str(row[column]), row)
        result = self._details(selected, column)
        print(f"Loaded {len(result)}/{len(set(ids))} saved successful {lens} classifications.", flush=True)
        return result

    def reviewed_events(self, *, release_id: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        """Preserve the existing correction path, including earlier model versions."""
        metadata = self.rows(
            table=CLASSIFICATION_TABLE,
            columns=METADATA_COLUMNS + ",updated_at,review_status", pk=CLASSIFICATION_PK,
            key="event_id", values=ids,
            apply=lambda q: q.eq("release_id", release_id).eq("lens", "event")
                            .in_("review_status", list(REVIEW_STATUSES)),
        )
        selected: dict[str, dict[str, Any]] = {}
        for row in sorted(metadata,
                          key=lambda r: (str(r.get("updated_at") or r.get("created_at") or ""),
                                         str(r[CLASSIFICATION_PK])), reverse=True):
            selected.setdefault(str(row["event_id"]), row)
        return self._details(selected, "event_id")
