"""Offline regression tests. These import no model or extraction dependencies.

Load the real reader functions from each classifier's AST so the lightweight
and extraction CI profiles both exercise these checks, without a live database.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import io
import json
import sys
import unittest
from collections import defaultdict
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import classification_database_reads as dbread


class APIError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__({"message": "JSON could not be generated", "code": code,
                          "details": 'b\'{"message":"Gateway Timeout"}\''})


class MemoryClient:
    def __init__(self, tables=None, cap=1000, fail=None):
        self.tables = copy.deepcopy(tables or {})
        self.cap = cap
        self.fail = fail
        self.calls = []

    def table(self, name):
        return Query(self, name)


class Query:
    def __init__(self, client, name):
        self.client, self.name, self.filters = client, name, []
        self.start, self.end, self.ordering = 0, 999, []
        self.width = 0

    def select(self, *_args, **_kwargs): return self
    def in_(self, key, values):
        self.width = max(self.width, len(values))
        self.filters.append(lambda row: str(row.get(key)) in values)
        return self
    def eq(self, key, value):
        self.filters.append(lambda row: row.get(key) == value)
        return self
    def order(self, key, desc=False):
        self.ordering.append((key, desc))
        return self
    def range(self, start, end):
        self.start, self.end = start, end
        return self
    def limit(self, n): return self.range(0, n - 1)
    def execute(self):
        self.client.calls.append((self.name, self.width, self.start, self.end))
        if self.client.fail:
            self.client.fail(self)
        rows = [row for row in self.client.tables.get(self.name, []) if all(f(row) for f in self.filters)]
        for key, reverse in reversed(self.ordering):
            rows.sort(key=lambda row: str(row.get(key) or ""), reverse=reverse)
        rows = rows[self.start:min(self.end+1, self.start+self.client.cap)]
        return SimpleNamespace(data=copy.deepcopy(rows))


def selected_functions(filename, names):
    source = (ROOT / "scripts" / filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in nodes} != set(names):
        raise AssertionError("Expected real classifier reader function is missing")
    # Only database transport is exercised here; clinical/news/model validation
    # remains covered by its existing independent suites, which are unchanged.
    namespace = {
        "database_read": dbread.execute_read,
        "read_id_rows": dbread.read_id_rows, "read_rows": dbread.read_rows,
        "SUPPORTED_TRANSLATION_PROFILES": ("stable",),
        "preferred_translation_rows": lambda rows: {row["article_id"]: row for row in reversed(rows)},
        "defaultdict": defaultdict,
    }
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations", asname=None)], level=0)] + nodes, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(ROOT / "scripts" / filename), "exec"), namespace)
    return namespace


class ClassificationReadTests(unittest.TestCase):
    def setUp(self):
        self.sleep = patch.object(dbread.time, "sleep").start()
        self.addCleanup(patch.stopall)
        self.output = io.StringIO()
        self.capture = redirect_stdout(self.output)
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)

    def test_exact_integer_504_response_retries(self):
        calls = []
        def read():
            calls.append(1)
            if len(calls) == 1: raise APIError(504)
            return SimpleNamespace(data=[{"article_id": "a"}])
        response = dbread.execute_read(read, label="article_translations")
        self.assertEqual(response.data, [{"article_id": "a"}])
        self.assertEqual(len(calls), 2)
        self.sleep.assert_called_once_with(1)

    def test_string_and_payload_error_codes_are_recognized(self):
        for exc in (APIError("504"), RuntimeError({"code": 504}), APIError("PGRST003"), APIError("57014")):
            self.assertTrue(dbread.transient_read_error(exc))

    def test_permission_schema_and_data_errors_are_not_retried(self):
        for code in ("401", "403", "400", "42P01", "42703", "23505"):
            calls = []
            def read():
                calls.append(1)
                raise APIError(code)
            with self.assertRaises(APIError): dbread.execute_read(read)
            self.assertEqual(len(calls), 1)
        self.sleep.assert_not_called()

    def test_arbitrary_validation_error_is_not_a_proxy_timeout(self):
        error = ValueError("JSON could not be generated: invalid classification")
        self.assertFalse(dbread.transient_read_error(error))

    def test_network_read_timeout_is_retryable(self):
        self.assertTrue(dbread.transient_read_error(TimeoutError("connection")))
        self.assertTrue(dbread.transient_read_error(ConnectionError("reset")))
        error = type("ReadTimeout", (Exception,), {"__module__": "httpx"})("no response")
        self.assertTrue(dbread.transient_read_error(error))

    def test_persistent_timeout_has_a_finite_attempt_limit(self):
        calls = []
        def read():
            calls.append(1)
            raise APIError(504)
        with self.assertRaises(dbread.DatabaseReadError): dbread.execute_read(read)
        self.assertEqual(len(calls), 5)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [1, 2, 4, 8])

    def test_read_budget_prevents_unbounded_requests(self):
        def read(): raise APIError(504)
        budget = dbread.ReadBudget(max_requests=2)
        with self.assertRaises(dbread.DatabaseReadError): dbread.execute_read(read, budget=budget)
        self.assertEqual(budget.requests, 2)

    def test_empty_id_scope_makes_no_request(self):
        client = MemoryClient()
        self.assertEqual(dbread.read_id_rows(client, "articles", "article_id", []), [])
        self.assertEqual(client.calls, [])

    def test_all_rows_survive_a_server_cap_lower_than_requested(self):
        rows = [{"article_id": "a%04d" % i} for i in range(1013)]
        client = MemoryClient({"articles": rows}, cap=7)
        found = dbread.read_id_rows(client, "articles", "article_id", [r["article_id"] for r in rows])
        self.assertEqual(found, rows)
        self.assertLessEqual(max(c[1] for c in client.calls), 25)

    def test_duplicate_input_ids_do_not_duplicate_rows(self):
        client = MemoryClient({"articles": [{"article_id": "a"}]})
        self.assertEqual(dbread.read_id_rows(client, "articles", "article_id", ["a", "a"]), [{"article_id": "a"}])

    def test_persistent_large_batch_timeout_splits_without_missing_ids(self):
        rows = [{"article_id": "a%02d" % i} for i in range(30)]
        def fail(query):
            if query.width > 3: raise APIError(504)
        client = MemoryClient({"articles": rows}, cap=2, fail=fail)
        found = dbread.read_id_rows(client, "articles", "article_id", [r["article_id"] for r in rows])
        self.assertEqual(found, rows)
        self.assertIn("DB READ SPLIT", self.output.getvalue())

    def test_timeout_after_first_page_does_not_duplicate_partial_parent_rows(self):
        rows = [{"article_id": "a%02d" % i} for i in range(8)]
        def fail(query):
            if query.width > 4 and query.start > 0: raise APIError(504)
        client = MemoryClient({"articles": rows}, cap=2, fail=fail)
        found = dbread.read_id_rows(client, "articles", "article_id", [r["article_id"] for r in rows])
        self.assertEqual(found, rows)

    def test_failed_subgroup_never_becomes_partial_evidence(self):
        rows = [{"article_id": "a"}, {"article_id": "b"}]
        def fail(query):
            if query.width > 1 or any(f({"article_id": "b"}) for f in query.filters): raise APIError(504)
        client = MemoryClient({"articles": rows}, fail=fail)
        with self.assertRaises(dbread.DatabaseReadError):
            dbread.read_id_rows(client, "articles", "article_id", ["a", "b"])
        self.assertEqual(client.tables["articles"], rows)

    def test_invalid_database_response_is_not_empty(self):
        client = MemoryClient()
        with patch.object(Query, "execute", return_value=SimpleNamespace(data=None)):
            with self.assertRaises(dbread.DatabaseReadError): dbread.read_id_rows(client, "articles", "article_id", ["a"])

    def test_nonadvancing_page_is_rejected(self):
        client = MemoryClient()
        with patch.object(Query, "execute", return_value=SimpleNamespace(data=[{"article_id": "a"}])):
            with self.assertRaises(dbread.DatabaseReadError): dbread.read_id_rows(client, "articles", "article_id", ["a"])
        self.assertEqual(client.tables, {})

    def test_no_credentials_or_source_text_are_logged(self):
        error = APIError(504)
        error.args = ({"code": 504, "details": "SECRET-KEY private-source-text"},)
        def read(): raise error
        with self.assertRaises(dbread.DatabaseReadError): dbread.execute_read(read, label="articles", attempts=2)
        self.assertNotIn("SECRET-KEY", self.output.getvalue())
        self.assertNotIn("private-source-text", self.output.getvalue())

    def test_both_real_translation_loaders_recover_from_the_reported_failure(self):
        for file, name in (("classify_symbiosis.py", "load_translation_map"), ("classify_dual_lens.py", "load_translations")):
            client = MemoryClient({"article_translations": [
                {"translation_id": "t1", "article_id": "a", "translation_profile": "stable", "created_at": "2026-09-05", "translated_headline": "Older"},
                {"translation_id": "t2", "article_id": "a", "translation_profile": "stable", "created_at": "2026-09-06", "translated_headline": "Newer"},
            ]}, cap=1)
            def fail(query):
                if len(query.client.calls) == 1: raise APIError(504)
            client.fail = fail
            functions = selected_functions(file, [name])
            result = functions[name](client, ["a"])
            self.assertEqual(result["a"]["translated_headline"], "Newer")

    def test_real_observation_loader_keeps_all_five_markets(self):
        rows = [{"observation_id": str(i), "article_id": "a", "search_country_iso3": country,
                 "search_language": lang, "search_rank": i+1} for i, (country, lang) in enumerate([
                     ("CHN", "zh"), ("USA", "en"), ("GBR", "en"), ("FRA", "fr"), ("CAN", "fr")])]
        client = MemoryClient({"article_observations": rows}, cap=1)
        funcs = selected_functions("classify_symbiosis.py", ["load_observation_meta"])
        result = funcs["load_observation_meta"](client, ["a"])["a"]
        self.assertEqual(result["search_markets"], {"CHN", "USA", "GBR", "FRA", "CAN"})
        self.assertEqual(result["search_languages"], {"zh", "en", "fr"})

    def test_real_saved_row_reader_preserves_reviews_across_many_pages(self):
        for release in ("2026-W36", "2027-W02"):
            rows = [{"symbiosis_classification_id": "%04d" % i, "release_id": release,
                     "raw_output": {"body_sha256": "hash-"+str(i)}, "review_status": "accepted"}
                    for i in range(1013)]
            client = MemoryClient({"symbiosis_classifications": rows}, cap=7)
            funcs = selected_functions("classify_symbiosis.py", ["paged_table"])
            found = funcs["paged_table"](client, "symbiosis_classifications", "*",
                                          apply=lambda q: q.eq("release_id", release))
            self.assertEqual(found, rows)
            self.assertEqual(client.tables["symbiosis_classifications"], rows)

    def test_body_bytes_and_fingerprints_survive_reads(self):
        text = "研究人员可以访问数据。 Les chercheurs ont accès aux données. " * 1000
        digest = hashlib.sha256(text.encode()).hexdigest()
        row = {"snapshot_id": "s1", "article_id": "a", "body_text": text, "text_sha256": digest, "is_current": True}
        client = MemoryClient({"brief_article_content_snapshots": [row]})
        found = dbread.read_id_rows(client, "brief_article_content_snapshots", "*", ["a"], apply=lambda q: q.eq("is_current", True))
        self.assertEqual(found, [row])

    def test_all_standalone_classifier_selects_are_protected(self):
        for filename in ("classify_symbiosis.py", "classify_dual_lens.py"):
            tree = ast.parse((ROOT / "scripts" / filename).read_text())
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute"):
                    continue
                attrs = {n.func.attr for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
                if attrs & {"insert", "update", "upsert", "delete", "rpc"}: continue
                ancestor, protected = node, False
                while ancestor in parents:
                    ancestor = parents[ancestor]
                    if isinstance(ancestor, ast.Call) and isinstance(ancestor.func, ast.Name) and ancestor.func.id == "database_read":
                        protected = True
                        break
                self.assertTrue(protected, "%s:%s has an unprotected SELECT" % (filename, node.lineno))


    def test_final_recovery_has_more_output_room_without_raising_dollar_cap(self):
        text = (ROOT / '.github/workflows/classify-current-symbiosis.yml').read_text()
        final = text.split('  pass_3:', 1)[1].split('  ensure_complete:', 1)[0]
        self.assertIn("AIEO_AI_MAX_COMPLETION_TOKENS: ${{ vars.AIEO_AI_MAX_COMPLETION_TOKENS || '16384' }}", final)
        self.assertIn("AIEO_AI_MAX_JOB_USD: ${{ vars.AIEO_AI_MAX_JOB_USD || '0.50' }}", final)
        self.assertNotIn('AIEO_AI_MAX_COMPLETION_TOKENS:', text.split('  pass_3:', 1)[0])
        self.assertIn('needs.pass_1.outputs.complete', final)
        self.assertIn('needs.pass_2.outputs.complete', final)


if __name__ == "__main__": unittest.main()
