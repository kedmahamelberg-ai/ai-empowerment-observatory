"""Database resilience tests for the resumable article-body workflow."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import brief_backfill_article_content as base
import brief_backfill_article_content_resumable as runner


class ReadClient:
    def __init__(self, failures, error):
        self.failures = failures
        self.error = error
        self.calls = 0

    def table(self, _name):
        client = self

        class Query:
            def select(self, *_args): return self
            def in_(self, *_args): return self
            def eq(self, *_args): return self
            def order(self, *_args, **_kwargs): return self
            def range(self, *_args): return self
            def execute(self):
                client.calls += 1
                if client.calls <= client.failures:
                    raise client.error
                return SimpleNamespace(data=[{"article_id": "a"}])

        return Query()


class AttemptClient:
    def __init__(self):
        self.rows = []

    def table(self, _name):
        client = self

        class Query:
            def __init__(self):
                self.filters = []
            def select(self, *_args): return self
            def eq(self, key, value):
                self.filters.append((key, value))
                return self
            def range(self, *_args): return self
            def execute(self):
                rows = [
                    row for row in client.rows
                    if all(row.get(key) == value for key, value in self.filters)
                ]
                return SimpleNamespace(data=rows[:1])

        return Query()


class DatabaseRecoveryTests(unittest.TestCase):
    def test_transient_504_read_is_retried(self):
        client = ReadClient(1, RuntimeError({"code": 504, "message": "Gateway Timeout"}))
        with patch.object(runner.time, "sleep") as sleep:
            rows = list(runner.scoped_rows(
                client, "example", "article_id", {"a"}, page_size=10
            ))
        self.assertEqual(rows, [{"article_id": "a"}])
        self.assertEqual(client.calls, 2)
        sleep.assert_called_once()

    def test_permission_error_is_not_retried(self):
        client = ReadClient(10, RuntimeError({"code": 403, "message": "Forbidden"}))
        with patch.object(runner.time, "sleep") as sleep:
            with self.assertRaises(RuntimeError):
                list(runner.scoped_rows(
                    client, "example", "article_id", {"a"}, page_size=10
                ))
        self.assertEqual(client.calls, 1)
        sleep.assert_not_called()

    def test_committed_attempt_is_confirmed_without_duplicate_retry(self):
        client = AttemptClient()
        calls = []

        def ambiguous_insert(_client, article_id, _url, _result, workflow_run_id):
            calls.append(article_id)
            client.rows.append({
                "article_id": article_id,
                "workflow_run_id": workflow_run_id,
            })
            raise RuntimeError({"code": 504, "message": "Gateway Timeout"})

        with patch.object(base, "insert_attempt", side_effect=ambiguous_insert), \
             patch.object(runner.time, "sleep") as sleep:
            runner.persist_attempt(
                client, "a", "https://example.org/a",
                {"outcome": "blocked_robots"}, "run-7"
            )
        self.assertEqual(calls, ["a"])
        self.assertEqual(len(client.rows), 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
