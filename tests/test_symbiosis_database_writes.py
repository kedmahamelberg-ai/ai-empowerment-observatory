"""Offline write-fault and real-classifier wiring tests; no model/DB imports."""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import symbiosis_database_writes as db
import classification_database_reads as reads

RID = "8645da3c-a541-4164-9bd8-11209beff68d"
RESULT_ID = "80bd5cf1-de58-43fa-9c45-5ad385d01983"


class APIError(RuntimeError):
    def __init__(self, code=504):
        self.code = code
        super().__init__({"message": "JSON could not be generated", "code": code,
                          "hint": "Refer to full message for details",
                          "details": 'b\'{"message":"Gateway Timeout"}\''})


def result_payload(**changes):
    payload = {"symbiosis_run_id": RID, "lens": "coverage", "unit_key": "coverage:2026-W37:article-a",
               "codebook_version": "v1", "release_id": "2026-W37", "model_confidence": 0.857891,
               "raw_output": {"axes": {"human": "gain"}, "input_fully_covered": True},
               "model_summary": "Stored source-bound model answer", "review_status": "pending",
               "updated_at": "2026-09-14T09:00:00Z"}
    payload.update(changes)
    return payload


def run_row(**changes):
    row = {"symbiosis_run_id": RID, "run_key": "symbiosis-test", "status": "running",
           "completed_at": None, "started_at": "2026-09-14T09:00:00Z",
           **{key: 0 for key in db.COUNTS}}
    row.update(changes)
    return row


class FakeClient:
    def __init__(self):
        self.rows = {db.RESULTS: [], db.RUNS: []}
        self.effects = {}
        self.calls = []
        self.serial = 0
        self.before_update = None
        self.late_insert = None

    def table(self, name):
        self.serial += 1
        return Query(self, name, self.serial)

    def queue(self, method, table, *effects):
        self.effects.setdefault((method, table), []).extend(effects)

    def count(self, method, table):
        return sum(c[0] == method and c[1] == table for c in self.calls)


class Query:
    def __init__(self, client, table, serial):
        self.client, self.name, self.serial = client, table, serial
        self.method, self.payload, self.filters, self.maximum = "select", None, [], None
    def select(self, *_args): return self
    def limit(self, count): self.maximum = count; return self
    def order(self, *_args, **_kwargs): return self
    def eq(self, key, value): self.filters.append((key, "eq", value)); return self
    def is_(self, key, value): self.filters.append((key, "is", value)); return self
    def in_(self, key, values): self.filters.append((key, "in", values)); return self
    def insert(self, payload): self.method, self.payload = "insert", copy.deepcopy(payload); return self
    def update(self, payload): self.method, self.payload = "update", copy.deepcopy(payload); return self
    def _matched(self, row):
        for key, method, value in self.filters:
            if method == "is" and (value != "null" or row.get(key) is not None): return False
            if method == "in" and row.get(key) not in value: return False
            if method == "eq" and not db._equivalent(row.get(key), value, key): return False
        return True
    def execute(self):
        c = self.client
        c.calls.append((self.method, self.name, copy.deepcopy(self.payload), copy.deepcopy(self.filters), self.serial))
        queue = c.effects.get((self.method, self.name), [])
        effect = queue.pop(0) if queue else "normal"
        if isinstance(effect, Exception): raise effect
        if self.method == "select":
            if effect == "malformed": return SimpleNamespace(data=None)
            if effect == "two": return SimpleNamespace(data=[{}, {}])
            rows = [copy.deepcopy(r) for r in c.rows[self.name] if self._matched(r)]
            return SimpleNamespace(data=rows[:self.maximum] if self.maximum else rows)
        if self.method == "insert":
            if c.late_insert:
                c.rows[self.name].append(c.late_insert); c.late_insert = None
            pk, keys = db.PRIMARY_KEYS[self.name], db.KEYS[self.name]
            for row in c.rows[self.name]:
                if row.get(pk) == self.payload[pk] or all(row.get(k) == self.payload.get(k) for k in keys):
                    raise APIError("23505")
            if effect == "late":
                c.late_insert = copy.deepcopy(self.payload)
                raise APIError()
            row = copy.deepcopy(self.payload)
            if self.name == db.RUNS:
                row = {**run_row(), **row}
            if "model_confidence" in row:
                row["model_confidence"] = float(db.Decimal(str(row["model_confidence"])).quantize(db.Decimal("0.0001"), rounding=db.ROUND_HALF_UP))
            row.setdefault("created_at", "2026-09-14T09:00:00+00:00")
            c.rows[self.name].append(row)
            if effect == "review_timeout":
                row.update({"review_status": "corrected", "reviewer_name": "Reviewer", "final_configuration": "mutualism"})
                raise APIError()
            if effect == "commit_timeout": raise APIError()
            if effect == "commit_empty": return SimpleNamespace(data=[])
            if effect == "wrong_response": return SimpleNamespace(data=[{**row, "model_summary": "wrong"}])
            return SimpleNamespace(data=[copy.deepcopy(row)])
        if c.before_update:
            fn = c.before_update; c.before_update = None; fn(c)
        found = []
        for row in c.rows[self.name]:
            if self._matched(row):
                row.update(copy.deepcopy(self.payload)); found.append(copy.deepcopy(row))
        if effect == "commit_timeout": raise APIError()
        if effect == "commit_empty": return SimpleNamespace(data=[])
        return SimpleNamespace(data=found)


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.sleep = patch.object(db.time, "sleep").start()
        patch.object(db.random, "uniform", return_value=0).start()
        self.addCleanup(patch.stopall)
        self.client = FakeClient()
    def insert(self, **changes):
        return db.insert_row(self.client, db.RESULTS, result_payload(**changes))
    def test_success_is_one_request_and_same_payload(self):
        payload = result_payload(); before = copy.deepcopy(payload)
        response = db.insert_row(self.client, db.RESULTS, payload)
        self.assertEqual(payload, before)
        self.assertEqual(self.client.count("insert", db.RESULTS), 1)
        self.assertEqual(self.client.count("select", db.RESULTS), 0)
        self.assertEqual(response.data[0]["model_confidence"], 0.8579)
    def test_integer_504_before_commit_is_retried(self):
        self.client.queue("insert", db.RESULTS, APIError(504))
        self.insert()
        self.assertEqual(len(self.client.rows[db.RESULTS]), 1)
        inserts = [c for c in self.client.calls if c[0] == "insert"]
        self.assertEqual(len(inserts), 2)
        self.assertEqual(inserts[0][2], inserts[1][2])
        self.assertNotEqual(inserts[0][4], inserts[1][4])
    def test_string_504_is_retried(self):
        self.client.queue("insert", db.RESULTS, APIError("504")); self.insert()
        self.assertEqual(self.client.count("insert", db.RESULTS), 2)
    def test_lost_success_response_is_confirmed_not_reinserted(self):
        self.client.queue("insert", db.RESULTS, "commit_timeout"); self.insert()
        self.assertEqual(self.client.count("insert", db.RESULTS), 1)
        self.assertEqual(len(self.client.rows[db.RESULTS]), 1)
    def test_late_commit_cannot_create_duplicate(self):
        self.client.queue("insert", db.RESULTS, "late"); self.insert()
        self.assertEqual(len(self.client.rows[db.RESULTS]), 1)
        self.assertEqual(self.client.count("insert", db.RESULTS), 2)
    def test_database_rounding_is_supported(self):
        self.client.queue("insert", db.RESULTS, "commit_timeout")
        self.assertEqual(self.insert(model_confidence=0.12345).data[0]["model_confidence"], 0.1235)
    def test_review_during_confirmation_is_preserved(self):
        self.client.queue("insert", db.RESULTS, "review_timeout")
        row = self.insert().data[0]
        self.assertEqual(row["review_status"], "corrected")
        self.assertEqual(row["final_configuration"], "mutualism")
        self.assertEqual(self.client.count("update", db.RESULTS), 0)
    def test_existing_same_result_is_reused(self):
        first = self.insert().data[0]; second = self.insert().data[0]
        self.assertEqual(first["symbiosis_classification_id"], second["symbiosis_classification_id"])
        self.assertEqual(len(self.client.rows[db.RESULTS]), 1)
    def test_different_model_output_is_not_overwritten(self):
        self.insert()
        with self.assertRaises(db.SymbiosisWriteConflict): self.insert(model_summary="different")
        self.assertEqual(self.client.count("update", db.RESULTS), 0)
        self.assertEqual(self.client.rows[db.RESULTS][0]["model_summary"], "Stored source-bound model answer")
    def test_different_evidence_is_not_overwritten(self):
        self.insert()
        with self.assertRaises(db.SymbiosisWriteConflict): self.insert(raw_output={"input_fully_covered": False})
    def test_empty_return_is_confirmed(self):
        self.client.queue("insert", db.RESULTS, "commit_empty")
        self.assertEqual(len(self.insert().data), 1)
        self.assertEqual(self.client.count("insert", db.RESULTS), 1)
    def test_incorrect_success_response_is_rejected(self):
        self.client.queue("insert", db.RESULTS, "wrong_response")
        with self.assertRaises(db.SymbiosisWriteConflict): self.insert()
    def test_confirmation_timeout_uses_read_retries(self):
        self.client.queue("insert", db.RESULTS, "commit_timeout")
        self.client.queue("select", db.RESULTS, APIError(504)); self.insert()
        self.assertEqual(self.client.count("insert", db.RESULTS), 1)
        self.assertEqual(self.client.count("select", db.RESULTS), 2)
    def test_failed_confirmation_does_not_resend(self):
        self.client.queue("insert", db.RESULTS, "commit_timeout")
        self.client.queue("select", db.RESULTS, *[APIError(504) for _ in range(5)])
        with self.assertRaises(reads.DatabaseReadError): self.insert()
        self.assertEqual(self.client.count("insert", db.RESULTS), 1)
    def test_malformed_confirmation_is_not_absence(self):
        self.client.queue("insert", db.RESULTS, APIError(504))
        self.client.queue("select", db.RESULTS, "malformed")
        with self.assertRaises(db.SymbiosisWriteError): self.insert()
        self.assertEqual(self.client.count("insert", db.RESULTS), 1)
    def test_ambiguous_duplicate_rows_stop(self):
        self.client.queue("insert", db.RESULTS, APIError(504))
        self.client.queue("select", db.RESULTS, "two")
        with self.assertRaises(db.SymbiosisWriteError): self.insert()
    def test_persistent_uncommitted_timeout_is_bounded(self):
        self.client.queue("insert", db.RESULTS, *[APIError(504) for _ in range(7)])
        with self.assertRaises(db.SymbiosisWriteError): self.insert()
        self.assertEqual(self.client.count("insert", db.RESULTS), 5)
        self.assertEqual(len(self.client.rows[db.RESULTS]), 0)
    def test_permission_schema_and_foreign_key_errors_not_retried(self):
        for code in (401, 403, "22P02", "23503", "23514", "PGRST204"):
            with self.subTest(code=code):
                self.client = FakeClient(); self.client.queue("insert", db.RESULTS, APIError(code))
                with self.assertRaises(APIError): self.insert()
                self.assertEqual(self.client.count("insert", db.RESULTS), 1)
                self.assertEqual(self.client.count("select", db.RESULTS), 0)
    def test_unknown_exception_not_retried(self):
        self.client.queue("insert", db.RESULTS, ValueError("Gateway Timeout"))
        with self.assertRaises(ValueError): self.insert()
        self.assertEqual(self.client.count("insert", db.RESULTS), 1)
    def test_transport_timeout_is_retried(self):
        self.client.queue("insert", db.RESULTS, TimeoutError()); self.insert()
        self.assertEqual(self.client.count("insert", db.RESULTS), 2)
    def test_unreviewed_tables_and_missing_keys_are_rejected(self):
        with self.assertRaises(db.SymbiosisWriteError): db.insert_row(self.client, "articles", {})
        with self.assertRaises(db.SymbiosisWriteError): db.insert_row(self.client, db.RESULTS, {"lens": "coverage"})
        self.assertEqual(self.client.calls, [])
    def test_run_start_uses_fixed_uuid_after_timeout(self):
        self.client.queue("insert", db.RUNS, "commit_timeout")
        payload = {"run_key": "start-test", "scope": "latest_release", "status": "running", "started_at": "2026-09-14T09:00:00Z"}
        row = db.insert_row(self.client, db.RUNS, payload).data[0]
        uuid.UUID(row["symbiosis_run_id"])
        self.assertEqual(self.client.count("insert", db.RUNS), 1)
    def test_checkpoint_lost_response_is_confirmed(self):
        self.client.rows[db.RUNS].append(run_row())
        self.client.queue("update", db.RUNS, "commit_timeout")
        row = db.update_run(self.client, RID, {"status": "running", "coverage_unit_count": 13}).data[0]
        self.assertEqual(row["coverage_unit_count"], 13)
        self.assertEqual(self.client.count("update", db.RUNS), 1)
    def test_checkpoint_unsaved_write_retries(self):
        self.client.rows[db.RUNS].append(run_row())
        self.client.queue("update", db.RUNS, APIError())
        db.update_run(self.client, RID, {"status": "failed", "coverage_unit_count": 13})
        self.assertEqual(self.client.count("update", db.RUNS), 2)
    def test_checkpoint_permission_error_not_retried(self):
        self.client.rows[db.RUNS].append(run_row()); self.client.queue("update", db.RUNS, APIError(403))
        with self.assertRaises(APIError): db.update_run(self.client, RID, {"status": "failed"})
        self.assertEqual(self.client.count("update", db.RUNS), 1)
    def test_checkpoint_missing_row_not_created(self):
        with self.assertRaises(db.SymbiosisWriteError): db.update_run(self.client, RID, {"status": "failed"})
        self.assertEqual(self.client.count("update", db.RUNS), 0)
    def test_checkpoint_same_values_are_no_op(self):
        self.client.rows[db.RUNS].append(run_row())
        db.update_run(self.client, RID, {"status": "running", "completed_at": None})
        self.assertEqual(self.client.count("update", db.RUNS), 0)
    def test_checkpoint_concurrent_progress_is_not_overwritten(self):
        self.client.rows[db.RUNS].append(run_row())
        self.client.before_update = lambda c: c.rows[db.RUNS][0].update({"coverage_unit_count": 20})
        with self.assertRaises(db.SymbiosisWriteConflict):
            db.update_run(self.client, RID, {"status": "running", "coverage_unit_count": 13})
        self.assertEqual(self.client.rows[db.RUNS][0]["coverage_unit_count"], 20)
        self.assertEqual(self.client.count("update", db.RUNS), 1)
    def test_success_cannot_be_downgraded(self):
        self.client.rows[db.RUNS].append(run_row(status="success", completed_at="2026-09-14T10:00:00Z"))
        with self.assertRaises(db.SymbiosisWriteConflict): db.update_run(self.client, RID, {"status": "failed"})
        self.assertEqual(self.client.count("update", db.RUNS), 0)
    def test_guard_fields_protect_success_after_baseline_read(self):
        self.client.rows[db.RUNS].append(run_row())
        self.client.before_update = lambda c: c.rows[db.RUNS][0].update({"status": "success"})
        with self.assertRaises(db.SymbiosisWriteConflict): db.update_run(self.client, RID, {"status": "failed"})
        self.assertEqual(self.client.rows[db.RUNS][0]["status"], "success")
    def test_invalid_status_counts_and_fields_rejected(self):
        for payload in ({"status": "green"}, {"status": "failed", "reviewer_name": "X"}, {"status": "running", "coverage_unit_count": -1}, {"status": "running", "coverage_unit_count": True}):
            with self.subTest(payload=payload), self.assertRaises(db.SymbiosisWriteError): db.update_run(self.client, RID, payload)
        self.assertEqual(self.client.calls, [])
    def test_timestamp_formats_equivalent_and_booleans_are_not_numbers(self):
        self.assertTrue(db._equivalent("2026-09-14T09:00:00Z", "2026-09-14T09:00:00+00:00", "started_at"))
        self.assertFalse(db._equivalent({"flag": True}, {"flag": 1}))
    def test_confirmation_failure_on_update_does_not_repeat_write(self):
        self.client.rows[db.RUNS].append(run_row())
        self.client.queue("select", db.RUNS, "normal", *[APIError() for _ in range(5)])
        self.client.queue("update", db.RUNS, "commit_timeout")
        with self.assertRaises(reads.DatabaseReadError): db.update_run(self.client, RID, {"status": "failed"})
        self.assertEqual(self.client.count("update", db.RUNS), 1)


class RealSourceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "scripts/classify_symbiosis.py").read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)
        self.functions = {n.name: n for n in self.tree.body if isinstance(n, ast.FunctionDef)}
        patch.object(db.time, "sleep").start()
        patch.object(db.random, "uniform", return_value=0).start()
        self.addCleanup(patch.stopall)
    def compiled(self, *names):
        code = "from __future__ import annotations\n" + "\n\n".join(ast.get_source_segment(self.source, self.functions[name]) for name in names)
        env = {"symbiosis_insert": db.insert_row, "symbiosis_update_run": db.update_run,
               "first_row": lambda response, _ctx: response.data[0], "CODEBOOK_VERSION": "v1",
               "CLASSIFIER_VERSION": "classifier", "EVIDENCE_POLICY_VERSION": "full-v1",
               "FULL_BODY_REQUIRED_POLICY": "full-only", "QWEN_REPO": "unused",
               "content_basis_for_storage": lambda value: value,
               "classification_audit": lambda unit, result: {"input": unit["unit_key"]},
               "utc_now": lambda: datetime(2026, 9, 14, 9, tzinfo=timezone.utc),
               "iso_z": lambda value: value.isoformat().replace("+00:00", "Z"),
               "ai_runtime": SimpleNamespace(uses_openai=lambda: True, identity=lambda: {"model": "gpt-5-nano"}),
               "database_read": reads.execute_read,
               "SymbiosisClassificationError": RuntimeError}
        exec(compile(code, "real_classify_symbiosis_functions", "exec"), env)
        return env
    def test_no_raw_database_write_remains_in_classifier(self):
        found = []
        for n in ast.walk(self.tree):
            if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute) or n.func.attr not in {"insert", "update", "upsert", "delete"}:
                continue
            root = n
            while isinstance(root, ast.Call) and isinstance(root.func, ast.Attribute):
                root = root.func.value
            if isinstance(root, ast.Name) and root.id == "client":
                found.append(n.func.attr)
        self.assertEqual(found, [])
    def test_all_seven_write_sites_use_the_safe_boundary(self):
        calls = [n.func.id for n in ast.walk(self.tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in {"symbiosis_insert", "symbiosis_update_run"}]
        self.assertEqual(calls.count("symbiosis_insert"), 3)
        self.assertEqual(calls.count("symbiosis_update_run"), 4)
    def test_real_result_insert_handles_lost_commit(self):
        env = self.compiled("insert_result"); client = FakeClient(); client.queue("insert", db.RESULTS, "commit_timeout")
        unit = {"content_basis": "full_text", "unit_key": "coverage:2026-W37:x", "release_id": "2026-W37", "period_start": "2026-09-07", "period_end": "2026-09-13", "article_id": "x"}
        result = {key: "test" for key in ("evidence_status", "relational_signal", "human_experience_type", "ai_expressive_role", "human_direction", "ai_direction", "configuration", "plain_label", "human_reasoning", "ai_reasoning", "summary", "topic", "geographic_scope")}
        result.update({"ai_relevant": True, "confidence": 0.85, "country_iso3s": [], "axes": {"human": "gain"}, "raw_output": {"input_fully_covered": True}})
        row = env["insert_result"](client, run_id=RID, lens="coverage", unit=unit, result=result)
        self.assertEqual(row["unit_key"], unit["unit_key"])
        self.assertEqual(client.count("insert", db.RESULTS), 1)
    def test_real_run_start_is_recoverable(self):
        env = self.compiled("start_run"); client = FakeClient(); client.queue("insert", db.RUNS, "commit_timeout")
        run_id, _ = env["start_run"](client, scope="latest_release", target_release_id="2026-W37", collection_run_id=None, empowerment_run_id=None, model_revision="model1")
        self.assertEqual(client.rows[db.RUNS][0]["symbiosis_run_id"], run_id)
        self.assertEqual(client.count("insert", db.RUNS), 1)
    def test_real_checkpoint_and_finish_preserve_counts(self):
        env = self.compiled("checkpoint_run", "finish_run", "run_progress_payload")
        client = FakeClient(); client.rows[db.RUNS].append(run_row()); client.queue("update", db.RUNS, "commit_timeout", "commit_timeout")
        rows = [{"lens": "coverage", "model_configuration": "mutualism"}, {"lens": "event", "model_configuration": "competition"}]
        env["checkpoint_run"](client, RID, rows=rows)
        self.assertEqual(client.rows[db.RUNS][0]["status"], "running")
        env["finish_run"](client, RID, status="success", rows=rows)
        self.assertEqual(client.rows[db.RUNS][0]["status"], "success")
        self.assertEqual(client.rows[db.RUNS][0]["coverage_unit_count"], 1)
        self.assertEqual(client.rows[db.RUNS][0]["event_unit_count"], 1)
    def test_real_resume_reuses_same_run_and_saved_progress(self):
        env = self.compiled("resume_or_start_run", "start_run")
        client = FakeClient()
        client.rows[db.RUNS].append(run_row(
            status="failed", scope="latest_release", classifier_version="classifier",
            model_name="gpt-5-nano", model_revision="rev1", codebook_version="v1",
            target_release_id="2026-W37", collection_run_id="collection1",
            coverage_unit_count=84,
        ))
        client.queue("update", db.RUNS, "commit_timeout")
        run_id, _, resumed = env["resume_or_start_run"](
            client, scope="latest_release", target_release_id="2026-W37",
            collection_run_id="collection1", empowerment_run_id=None,
            model_revision="rev1",
        )
        self.assertEqual(run_id, RID)
        self.assertTrue(resumed)
        self.assertEqual(client.rows[db.RUNS][0]["status"], "running")
        self.assertEqual(client.rows[db.RUNS][0]["coverage_unit_count"], 84)
        self.assertEqual(client.count("insert", db.RUNS), 0)
    def test_write_retry_time_limit_blocks_new_attempt(self):
        client = FakeClient(); client.queue("insert", db.RESULTS, APIError())
        with patch.object(db, "MAX_SECONDS", 0.01):
            with self.assertRaises(db.SymbiosisWriteError):
                db.insert_row(client, db.RESULTS, result_payload())
        self.assertEqual(client.count("insert", db.RESULTS), 1)
    def test_real_continuation_keeps_review_and_original_row(self):
        env = self.compiled("carry_forward_saved_rows"); client = FakeClient()
        old = result_payload(symbiosis_classification_id=RESULT_ID, review_status="corrected", final_configuration="mutualism", reviewer_name="Reviewer")
        before = copy.deepcopy(old); client.queue("insert", db.RESULTS, "commit_timeout")
        rows = env["carry_forward_saved_rows"](client, [old], str(uuid.uuid4()))
        self.assertEqual(old, before)
        self.assertEqual(rows[0]["review_status"], "corrected")
        self.assertEqual(rows[0]["final_configuration"], "mutualism")
        self.assertEqual(rows[0]["raw_output"]["continued_from_classification_id"], RESULT_ID)
    def test_each_pass_checks_write_recovery_before_models(self):
        workflow = (ROOT / ".github/workflows/classify-current-symbiosis.yml").read_text()
        command = "python -B -m unittest discover -s tests -p 'test_symbiosis_database_writes.py' -v"
        self.assertEqual(workflow.count(command), 3)
        for part in workflow.split("name: Classification pass ")[1:]:
            self.assertLess(part.index(command), part.index("Install Python dependencies"))
        self.assertIn("Require complete relationship classification", workflow)
        self.assertIn("Publication is blocked.", workflow)


if __name__ == "__main__":
    unittest.main()
