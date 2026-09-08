"""Exercise real PostgREST requests with a deterministic HTTP/database fixture."""
from __future__ import annotations

import argparse
import copy
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import httpx
from postgrest import SyncPostgrestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import publish_symbiosis_release as publisher
import symbiosis_publication_reads as reads
from symbiosis_common import CLASSIFIER_VERSION, CODEBOOK_VERSION, final_payload_from_classification


def classification(number, unit="event-a", run="run-good", **overrides):
    return {
        "symbiosis_classification_id": f"{number:08d}-0000-0000-0000-000000000000",
        "symbiosis_run_id": run, "article_id": None, "event_id": unit,
        "release_id": "2026-W36", "lens": "event", "codebook_version": CODEBOOK_VERSION,
        "created_at": f"2026-09-08T{number // 3600:02}:{number // 60 % 60:02}:{number % 60:02}Z",
        "review_status": "pending", "content_basis": "full_text",
        "raw_output": {"input_evidence": {"source_count": 1, "full_text_sources": 1},
                       "classification_audit": {"source_fingerprints": {"source": "b" * 64}}},
        "model_configuration": "no_clear_relational_signal",
        **overrides,
    }


def run_row(identifier="run-good", status="success", version=CLASSIFIER_VERSION):
    return {"symbiosis_run_id": identifier, "status": status, "classifier_version": version}


class DatabaseHTTP:
    def __init__(self, rows=(), runs=None, *, cap=1000):
        self.tables = {reads.CLASSIFICATION_TABLE: list(rows),
                       reads.RUN_TABLE: [run_row()] if runs is None else list(runs)}
        self.cap, self.calls, self.fault = cap, [], None

    def handle(self, request):
        # Sending a mutation, an embedded join, or unbounded history payload is
        # a regression even if the final fixture counts happen to be correct.
        assert request.method == "GET", "Publication must only read the database"
        table = request.url.path.rsplit("/", 1)[-1]
        params = dict(request.url.params)
        self.calls.append((table, params))
        assert "!inner" not in params["select"]
        assert "order" in params and int(params["limit"]) > 0
        if params["select"] == "*":
            assert reads.CLASSIFICATION_PK in params, "Fetch full rows only by selected primary keys"
        if self.fault:
            response = self.fault(table, params)
            if response is not None:
                return response
        rows = copy.deepcopy(self.tables[table])
        for key, value in params.items():
            if key in {"select", "order", "limit"}:
                continue
            operator, value = value.split(".", 1)
            if operator == "eq":
                rows = [r for r in rows if str(r.get(key)) == value]
            elif operator == "gt":
                rows = [r for r in rows if str(r.get(key)) > value]
            elif operator == "in":
                values = [v.strip('"') for v in value.strip("()").split(",")]
                rows = [r for r in rows if str(r.get(key)) in values]
            else:
                raise AssertionError(f"Unsupported fixture operator {operator}")
        column, order = params["order"].split(".")[:2]
        rows.sort(key=lambda r: r[column], reverse=order == "desc")
        rows = rows[:min(int(params["limit"]), self.cap)]
        if params["select"] != "*":
            columns = params["select"].split(",")
            rows = [{k: r.get(k) for k in columns} for r in rows]
        return httpx.Response(200, json=rows)


def error_response(code="57014", status=500):
    return httpx.Response(status, json={"code": code, "message": "statement timeout",
                                        "details": None, "hint": None})


class PublicationReadTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        redirect = redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        sleeping = patch.object(reads.time, "sleep")
        self.sleep = sleeping.start()
        self.addCleanup(sleeping.stop)

    def client(self, fixture):
        http = httpx.Client(transport=httpx.MockTransport(fixture.handle))
        client = SyncPostgrestClient("https://database.invalid/rest/v1", http_client=http)
        self.addCleanup(http.close)
        return client

    def latest(self, fixture, ids=("event-a",), **options):
        client = self.client(fixture)
        reader = reads.PublicationReader(client, **options)
        return publisher.latest_rows(client, release_id="2026-W36", lens="event",
                                     ids=list(ids), reader=reader)

    def test_latest_success_beyond_api_cap_and_first_thousand_history_rows(self):
        rows = [classification(i) for i in range(1, 1011)]
        fixture = DatabaseHTTP(rows, cap=17)
        result = self.latest(fixture)
        self.assertEqual(result["event-a"], rows[-1])
        full = [q for table, q in fixture.calls if q["select"] == "*"]
        self.assertTrue(full)
        self.assertTrue(all(rows[-1][reads.CLASSIFICATION_PK] in q[reads.CLASSIFICATION_PK] for q in full))
        self.assertNotIn("raw_output", fixture.calls[0][1]["select"])

    def test_filters_failed_running_other_models_releases_lenses_and_codebooks(self):
        winner = classification(1)
        fixture = DatabaseHTTP([
            winner, classification(2, run="failed"), classification(3, run="running"),
            classification(4, run="old-model"), classification(5, release_id="2026-W35"),
            classification(6, lens="coverage"), classification(7, codebook_version="obsolete"),
        ], [run_row(), run_row("failed", "failed"), run_row("running", "running"),
            run_row("old-model", version="old")])
        self.assertEqual(self.latest(fixture), {"event-a": winner})

    def test_statement_timeout_splits_batches_without_omitting_units(self):
        rows = [classification(i, unit=f"event-{i}") for i in range(1, 28)]
        fixture = DatabaseHTTP(rows, cap=3)
        def fault(table, params):
            for value in params.values():
                if value.startswith("in.(") and len(value.split(",")) > 4:
                    return error_response()
        fixture.fault = fault
        result = self.latest(fixture, ids=[r["event_id"] for r in rows])
        self.assertEqual(result, {r["event_id"]: r for r in rows})
        self.assertIn("splitting", self.output.getvalue())

    def test_timeout_after_a_page_resumes_from_cursor(self):
        rows = [classification(i, unit=f"event-{i % 2}") for i in range(1, 16)]
        fixture = DatabaseHTTP(rows, cap=3)
        fired = []
        def fault(table, params):
            if not fired and table == reads.CLASSIFICATION_TABLE and params.get(reads.CLASSIFICATION_PK, "").startswith("gt."):
                fired.append(True)
                return error_response()
        fixture.fault = fault
        result = self.latest(fixture, ids=["event-0", "event-1"])
        self.assertEqual(result["event-0"], rows[-2])
        self.assertEqual(result["event-1"], rows[-1])
        self.assertTrue(fired)

    def test_single_id_timeout_retries_then_preserves_review_and_fingerprints(self):
        row = classification(1, review_status="corrected", final_configuration="mutualism",
                             final_human_direction="enabling", final_ai_direction="enabling",
                             final_evidence_status="sufficient", final_evidence_summary="Owner correction")
        fixture = DatabaseHTTP([row])
        attempts = []
        def fault(table, params):
            if not attempts:
                attempts.append(True)
                return error_response()
        fixture.fault = fault
        result = self.latest(fixture)["event-a"]
        self.assertEqual(result, row)
        self.assertEqual(final_payload_from_classification(result), final_payload_from_classification(row))
        self.assertEqual(self.sleep.call_count, 1)

    def test_persistent_timeout_is_bounded_and_cannot_overwrite_publication(self):
        fixture = DatabaseHTTP([classification(1)])
        fixture.fault = lambda table, params: error_response()
        client = self.client(fixture)
        release = {"release_id": "2026-W36", "evidence": [
            {"event_id": "event-a", "member_article_ids": [], "sources": []}]}
        args = argparse.Namespace(release_id="", offline_corrections=False, require_complete=False)
        with patch.object(publisher, "parse_args", return_value=args), \
             patch.object(publisher, "read_json", return_value=release), \
             patch.object(publisher, "release_corrections", return_value={}), \
             patch.object(publisher, "required_env", return_value="fixture"), \
             patch.object(publisher, "create_client", return_value=client), \
             patch.object(publisher, "persist_release_payload") as persist, \
             patch.object(publisher, "write_json") as write:
            with self.assertRaisesRegex(reads.PublicationReadError, "three attempts"):
                publisher.main()
            persist.assert_not_called()
            write.assert_not_called()
        self.assertEqual(len(fixture.calls), 3)

    def test_permission_error_is_not_retried_or_treated_as_empty(self):
        fixture = DatabaseHTTP([classification(1)])
        fixture.fault = lambda table, params: error_response("42501", 403)
        with self.assertRaisesRegex(reads.PublicationReadError, "42501"):
            self.latest(fixture)
        self.assertEqual(len(fixture.calls), 1)
        self.sleep.assert_not_called()

    def test_transient_transport_failure_retries(self):
        fixture = DatabaseHTTP([classification(1)])
        failures = []
        def fault(table, params):
            if not failures:
                failures.append(True)
                raise httpx.ReadTimeout("fixture timeout")
        fixture.fault = fault
        self.assertEqual(len(self.latest(fixture)), 1)

    def test_missing_selected_payload_fails_instead_of_publishing_subset(self):
        fixture = DatabaseHTTP([classification(1)])
        fixture.fault = lambda table, p: httpx.Response(200, json=[]) if p["select"] == "*" else None
        with self.assertRaisesRegex(reads.PublicationReadError, "could not all be read"):
            self.latest(fixture)

    def test_changed_metadata_between_selection_and_full_row_fails(self):
        fixture = DatabaseHTTP([classification(1)])
        def fault(table, params):
            if params["select"] == "*":
                fixture.tables[table][0]["symbiosis_run_id"] = "different-run"
        fixture.fault = fault
        with self.assertRaisesRegex(reads.PublicationReadError, "metadata changed"):
            self.latest(fixture)

    def test_equal_timestamps_choose_stable_primary_key(self):
        rows = [classification(i, created_at="2026-09-08T12:00:00Z") for i in (3, 1, 2)]
        self.assertEqual(self.latest(DatabaseHTTP(rows, cap=1))["event-a"], rows[0])

    def test_missing_run_metadata_cannot_silently_fall_back(self):
        fixture = DatabaseHTTP([classification(1)], runs=[])
        with self.assertRaisesRegex(reads.PublicationReadError, "run metadata is missing"):
            self.latest(fixture)

    def test_budget_stops_splitting_and_retrying(self):
        fixture = DatabaseHTTP([classification(1)])
        clock = [0.0]
        def fault(table, params):
            clock[0] = 6.0
            return error_response()
        fixture.fault = fault
        with patch.object(reads.time, "monotonic", side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(reads.PublicationReadError, "budget was reached"):
                self.latest(fixture, budget_seconds=5)
        self.assertEqual(len(fixture.calls), 1)

    def test_review_corrections_keep_earlier_versions_and_latest_review_time(self):
        older_review = classification(1, review_status="corrected", codebook_version="old",
                                      updated_at="2026-09-08T15:00:00Z")
        newer_row = classification(2, review_status="accepted", updated_at="2026-09-08T14:00:00Z")
        fixture = DatabaseHTTP([older_review, newer_row, classification(3)], cap=1)
        reader = reads.PublicationReader(self.client(fixture))
        result = reader.reviewed_events(release_id="2026-W36", ids=["event-a"])
        self.assertEqual(result, {"event-a": older_review})

    def test_empty_scope_makes_no_database_request(self):
        fixture = DatabaseHTTP()
        self.assertEqual(self.latest(fixture, ids=[]), {})
        self.assertEqual(fixture.calls, [])


if __name__ == "__main__":
    unittest.main()
