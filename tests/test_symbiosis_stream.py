"""Regression coverage for the W36 event that timed out after 269 saved rows.

The HTTP integration uses a local fixture server. No model, credentials or
production data are needed, and fixture prose is never published.
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import sys
import threading
import time
import unittest
from contextlib import redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import symbiosis_stream as transport
import classify_symbiosis as classifier
import test_relationship_recovery as fixtures
from symbiosis_model_output import ModelOutputError, RESPONSE_SCHEMA, response_result


def sse(item):
    return ("data: " + json.dumps(item, ensure_ascii=False)).encode("utf-8")


def delta(content=None, reason=None):
    return {"choices": [{"index": 0, "delta": {"content": content}, "finish_reason": reason}]}


def valid_lines(payload=None):
    answer = json.dumps(payload or fixtures.model_payload(), ensure_ascii=False)
    return [b": ping", sse({"prompt_progress": {"processed": 20, "total": 20}}),
            sse(delta(answer[:80])), sse(delta(answer[80:])), sse(delta(reason="stop")),
            sse({"choices": [], "usage": {"completion_tokens": 400}}), b"data: [DONE]"]


class StreamReply:
    headers = {"Content-Type": "text/event-stream; charset=utf-8"}

    def __init__(self, lines):
        self.lines, self.closed, self.chunk_size = lines, False, None

    def raise_for_status(self):
        pass

    def iter_lines(self, chunk_size):
        self.chunk_size = chunk_size
        yield from self.lines

    def close(self):
        self.closed = True


class StreamTests(unittest.TestCase):
    def test_progress_pings_unicode_and_usage_preserve_only_the_final_answer(self):
        payload = fixtures.model_payload()
        payload["summary"] = "研究 · accès · café"
        lines = [b"event: completion", b"id: fixture", b"retry: 1000",
                 sse({"choices": [{"index": 0, "delta": {"reasoning_content": "PRIVATE TRACE"}}]})]
        reply = StreamReply(lines + valid_lines(payload))
        log = io.StringIO()
        with patch.object(transport.requests, "post", return_value=reply) as post, redirect_stdout(log):
            raw = transport.chat_completion("http://fixture", {"max_tokens": 1600})
        parsed, diagnostics = response_result(raw)
        self.assertEqual(parsed, payload)
        self.assertEqual(diagnostics["completion_tokens"], 400)
        self.assertNotIn("PRIVATE TRACE", json.dumps(raw) + log.getvalue())
        self.assertTrue(reply.closed)
        self.assertEqual(reply.chunk_size, 1)
        self.assertTrue(post.call_args.kwargs["stream"])
        request = post.call_args.kwargs["json"]
        self.assertTrue(request["stream"])
        self.assertTrue(request["return_progress"])
        self.assertEqual(request["sse_ping_interval"], 15)

    def test_healthy_progress_may_finish_after_the_old_420_second_timeout(self):
        clock = [0.0]

        def lines():
            for now in (60, 240, 421, 590):
                clock[0] = now
                yield b": ping"
            clock[0] = 600
            yield from valid_lines()

        reply = StreamReply(lines())
        log = io.StringIO()
        with patch.object(transport.requests, "post", return_value=reply), \
             patch.object(transport.time, "monotonic", side_effect=lambda: clock[0]), redirect_stdout(log):
            result = transport.chat_completion("http://fixture", {})
        self.assertEqual(response_result(result)[0], fixtures.model_payload())
        self.assertEqual(result["stream_diagnostics"]["elapsed_seconds"], 600)
        self.assertIn("590 seconds", log.getvalue())
        self.assertTrue(reply.closed)

    def test_pings_cannot_extend_a_request_indefinitely(self):
        clock = [0.0]

        def lines():
            clock[0] = 1201
            yield b": ping"

        reply = StreamReply(lines())
        with patch.object(transport.requests, "post", return_value=reply), \
             patch.object(transport.time, "monotonic", side_effect=lambda: clock[0]), \
             self.assertRaises(ModelOutputError) as failure:
            transport.chat_completion("http://fixture", {})
        self.assertEqual(failure.exception.diagnostics[0]["error_type"], "ModelRuntimeLimit")
        self.assertTrue(reply.closed)

    def test_idle_timeout_closes_the_stream_and_records_a_safe_reason(self):
        def lines():
            yield b": ping"
            raise transport.requests.ReadTimeout("Private source detail must not be logged")

        reply = StreamReply(lines())
        with patch.object(transport.requests, "post", return_value=reply), self.assertRaises(ModelOutputError) as failure:
            transport.chat_completion("http://fixture", {})
        self.assertEqual(failure.exception.diagnostics[0]["error_type"], "ReadTimeout")
        self.assertNotIn("Private source", str(failure.exception) + json.dumps(failure.exception.diagnostics))
        self.assertTrue(reply.closed)

    def test_incomplete_invalid_empty_and_reasoning_only_streams_do_not_become_findings(self):
        answer = json.dumps(fixtures.model_payload())
        cases = {
            "no completion marker": [sse(delta(answer)), b"data: [DONE]"],
            "invalid JSON": [b"data: {not JSON"],
            "server error": [sse({"error": {"message": "private details"}})],
            "invalid finish": [sse(delta(answer, reason={}))],
            "continued after stop": [sse(delta(answer, "stop")), sse(delta("extra"))],
            "truncated": [sse(delta(answer, "length"))],
            "empty": [sse(delta(reason="stop"))],
            "reasoning only": [sse({"choices": [{"delta": {"reasoning_content": answer}, "finish_reason": "stop"}]})],
        }
        for name, lines in cases.items():
            reply = StreamReply(lines)
            with self.subTest(name=name), patch.object(transport.requests, "post", return_value=reply), \
                 self.assertRaises(ModelOutputError):
                response_result(transport.chat_completion("http://fixture", {}))
            self.assertTrue(reply.closed)

    def test_real_http_pings_keep_a_slow_local_response_alive(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for _ in range(12):
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    time.sleep(0.025)
                for line in valid_lines():
                    self.wfile.write(line + b"\n\n")
                    self.wfile.flush()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = transport.chat_completion(f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                                               {}, read_timeout=0.2, max_seconds=10)
            self.assertEqual(response_result(result)[0], fixtures.model_payload())
            self.assertTrue(received[0]["stream"])
            self.assertGreater(result["stream_diagnostics"]["elapsed_seconds"], 0.2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class RetryTests(unittest.TestCase):
    def test_timeout_does_not_request_an_even_larger_answer_on_retry(self):
        error = ModelOutputError("Connection stopped", [{"error_type": "ReadTimeout", "finish_reason": None}])
        with patch.object(classifier, "chat_completion", side_effect=[error, fixtures.completion(json.dumps(fixtures.model_payload()))]) as call, \
             patch.object(classifier.time, "sleep"), redirect_stderr(io.StringIO()):
            result = classifier._call_classifier_chunk(lens="event", evidence="Fixture evidence", content_basis="full_text")
        self.assertEqual([c.kwargs["payload"]["max_tokens"] for c in call.call_args_list], [1600, 1600])
        self.assertEqual([c.kwargs["payload"]["seed"] for c in call.call_args_list], [42, 43])
        self.assertEqual(result["raw_output"]["recovered_attempts"][0]["error_type"], "ReadTimeout")

    def test_a_verified_token_limit_allows_a_larger_complete_answer(self):
        complete = fixtures.completion(json.dumps(fixtures.model_payload()))
        limited = fixtures.completion(json.dumps(fixtures.model_payload()), reason="length")
        with patch.object(classifier, "chat_completion", side_effect=[limited, complete]) as call, \
             patch.object(classifier.time, "sleep"), redirect_stderr(io.StringIO()):
            classifier._call_classifier_chunk(lens="event", evidence="Fixture evidence", content_basis="full_text")
        self.assertEqual([c.kwargs["payload"]["max_tokens"] for c in call.call_args_list], [1600, 2400])

    def test_existing_concise_field_limits_are_enforced_by_the_schema(self):
        payload = fixtures.model_payload()
        for key in ("human_reasoning", "ai_reasoning", "summary", "public_takeaway", "people_evidence"):
            self.assertEqual(RESPONSE_SCHEMA["properties"][key]["maxLength"], 280)
            with self.subTest(key=key), self.assertRaises(ModelOutputError):
                response_result(fixtures.completion(json.dumps({**payload, key: "x" * 281})))


class LastEventRecoveryTests(unittest.TestCase):
    def test_recovery_reuses_269_rows_and_classifies_only_the_missing_event(self):
        harness = fixtures.RecoveryTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        release = "2026-W36"
        harness.seed(269, release)
        saved = harness.db.tables["symbiosis_classifications"]
        saved[0]["review_status"] = "accepted"
        saved[0]["final_reasoning"] = "Owner decision retained."
        original = copy.deepcopy(saved)
        article = fixtures.unit(270, release)
        missing = {**article, "unit_key": "event:2026-W36:fd27a296-a477-4b7b-a423-0abd44c8203e",
                   "event_id": "fd27a296-a477-4b7b-a423-0abd44c8203e", "event_title": "Fixture event",
                   "event_date": "2026-09-02",
                   "member_articles": [article], "model_member_articles": [article]}
        units = [("coverage", fixtures.unit(n, release)) for n in range(1, 270)] + [("event", missing)]
        args = argparse.Namespace(scope="latest", lens="both", release_id=release, replace=False,
                                  resume_only=False, limit=0, time_budget_minutes=225,
                                  status_output=str(harness.root / "status.json"))
        releases = [{"release_id": release, "lineage": {"collection_run_id": "collection", "classification_run_id": "empowerment"}}]
        reply = StreamReply(valid_lines())
        with patch.object(classifier, "parse_args", return_value=args), \
             patch.object(classifier, "selected_releases", return_value=(releases, [])), \
             patch.object(classifier, "release_units", return_value=units), \
             patch.object(transport.requests, "post", return_value=reply) as post:
            self.assertEqual(classifier.main(), 0)
        status = json.loads((harness.root / "status.json").read_text())
        self.assertTrue(status["complete"])
        self.assertEqual(status["selected_units"], 270)
        self.assertEqual(status["saved_units"], 270)
        self.assertEqual(status["new_units"], 1)
        self.assertEqual(status["remaining_units"], 0)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(harness.db.tables["symbiosis_classifications"][:269], original)
        self.assertEqual(harness.db.tables["symbiosis_classifications"][-1]["lens"], "event")
        self.assertEqual(harness.db.tables["symbiosis_classification_runs"][0]["status"], "success")


if __name__ == "__main__":
    unittest.main()
