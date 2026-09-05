"""Run the real classifier loop with an in-memory database and model endpoint.

No credentials, article downloads, or generated research findings are needed.
The fixtures exercise the September failure sequence and future releases.
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import classify_symbiosis as classifier
import classify_dual_lens as dual
import symbiosis_common as common
import publish_symbiosis_release as publisher
from symbiosis_model_output import ModelOutputError, dimension_schema, require_schema, response_result


def model_payload():
    return {
        "ai_relevant": True, "evidence_status": "sufficient", "relational_signal": "complete",
        "human_experience_type": "expansion", "ai_expressive_role": "ai_extension",
        "human_reasoning": "The source describes new access for researchers.",
        "ai_reasoning": "The service is being used.", "summary": "Researchers gain access.",
        "confidence": 0.8, "topic": "research", "geographic_scope": "country", "country_iso3s": ["CAN"],
        "relationship_patterns": {"mutualism": True, "ai_benefiting_parasitism": False,
                                  "human_benefiting_parasitism": False, "competition": False},
        "distribution_signal": "not_shown", "public_takeaway": "Researchers gain access to a service.",
        "people_evidence": "The service gives researchers access to the documented dataset.",
    }


def completion(content=None, reason="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": reason}],
            "usage": {"completion_tokens": 400}}


def http_reply(payload):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)


class MemoryDB:
    def __init__(self):
        self.tables = {"symbiosis_classifications": [], "symbiosis_classification_runs": []}
        self.next_id = 1

    def table(self, name):
        return Query(self, name)


class Query:
    def __init__(self, db, name):
        self.db, self.name, self.filters = db, name, []
        self.operation, self.payload, self.bounds, self.order_by = "select", None, None, None

    def select(self, *_): return self
    def eq(self, key, value):
        self.filters.append(lambda r: r.get(key) == value)
        return self
    def in_(self, key, values):
        self.filters.append(lambda r: r.get(key) in values)
        return self
    def is_(self, key, value): return self.eq(key, None if value == "null" else value)
    def order(self, key, desc=False):
        self.order_by = (key, desc)
        return self
    def limit(self, count): return self.range(0, count - 1)
    def range(self, start, end):
        self.bounds = (start, end + 1)
        return self
    def insert(self, payload):
        self.operation, self.payload = "insert", copy.deepcopy(payload)
        return self
    def update(self, payload):
        self.operation, self.payload = "update", copy.deepcopy(payload)
        return self
    def execute(self):
        table = self.db.tables.setdefault(self.name, [])
        if self.operation == "insert":
            row = self.payload
            key = "symbiosis_classification_id" if self.name == "symbiosis_classifications" else "symbiosis_run_id"
            row[key] = f"generated-{self.db.next_id}"
            self.db.next_id += 1
            if self.name == "symbiosis_classifications":
                # The actual production constraint behind the earlier failure.
                assert row["content_basis"] in {"headline_only", "headline_and_snippet", "article_summary", "full_text", "multiple_sources"}
                assert not any((old["symbiosis_run_id"], old["unit_key"]) == (row["symbiosis_run_id"], row["unit_key"]) for old in table)
            table.append(row)
            rows = [row]
        else:
            rows = [r for r in table if all(f(r) for f in self.filters)]
            if self.operation == "update":
                for row in rows: row.update(self.payload)
            if self.order_by:
                key, reverse = self.order_by
                rows = sorted(rows, key=lambda r: str(r.get(key) or ""), reverse=reverse)
            if self.bounds: rows = rows[slice(*self.bounds)]
        return SimpleNamespace(data=copy.deepcopy(rows))


def unit(number, release="2026-W35", body=True):
    return {
        "unit_key": f"coverage:{release}:{number:03}", "article_id": f"{number:03}",
        "release_id": release, "period_start": "2026-08-24", "period_end": "2026-08-30",
        "content_basis": "full_text" if body else "not_available",
        "evidence_basis_summary": {"source_count": 1, "full_text_sources": int(body),
                                   "not_available_sources": int(not body), "input_policy": classifier.FULL_BODY_REQUIRED_POLICY},
        "headline_english": "Research access in Canada", "headline_original": "Accès aux données",
        "source_language": "fr", "publisher": "Fixture", "date": "2026-08-25", "url": "https://example.com/research",
        "evidence_text": "Les chercheurs gagnent un accès documenté aux données grâce au service. " * 12 if body else "",
    }


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.db = MemoryDB()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.stack.enter_context(redirect_stderr(io.StringIO()))
        self.stack.enter_context(patch.object(classifier, "OUTPUT_PATH", self.root / "latest.json"))
        self.stack.enter_context(patch.object(classifier, "create_client", return_value=self.db))
        self.stack.enter_context(patch.object(classifier, "required_env", return_value="test-only"))
        self.stack.enter_context(patch.object(classifier, "HfApi", return_value=SimpleNamespace(model_info=lambda _: SimpleNamespace(sha="fixture"))))
        self.stack.enter_context(patch.object(classifier, "start_server", return_value=(None, None)))
        self.stack.enter_context(patch.object(classifier.time, "sleep"))

    def seed(self, count=46, release="2026-W35"):
        self.db.tables["symbiosis_classification_runs"] = [{
            "symbiosis_run_id": "saved-run", "run_key": "saved-run-key", "status": "failed",
            "scope": "latest_release", "target_release_id": release, "collection_run_id": "collection",
            "classifier_version": common.CLASSIFIER_VERSION, "codebook_version": common.CODEBOOK_VERSION,
            "started_at": "2026-09-04T19:47:00Z",
        }]
        for number in range(1, count + 1):
            result = common.validate_model_payload(model_payload())
            result["raw_output"] = {"model_response": model_payload()}
            classifier.insert_result(self.db, run_id="saved-run", lens="coverage", unit=unit(number, release), result=result)

    def run_pass(self, units, replies, resume_only=True):
        args = argparse.Namespace(scope="latest", lens="both", release_id=units[0]["release_id"],
                                  replace=True, resume_only=resume_only, limit=0,
                                  time_budget_minutes=225, status_output=str(self.root / "status.json"))
        releases = [{"release_id": args.release_id, "lineage": {"collection_run_id": "collection", "classification_run_id": "empowerment"}}]
        with patch.object(classifier, "parse_args", return_value=args), \
             patch.object(classifier, "selected_releases", return_value=(releases, [])), \
             patch.object(classifier, "release_units", return_value=[("coverage", row) for row in units]), \
             patch.object(classifier.requests, "post", side_effect=[http_reply(r) for r in replies]) as post:
            self.assertEqual(classifier.main(), 0)
        return json.loads((self.root / "status.json").read_text()), post

    def test_item_47_failure_does_not_lose_46_or_starve_48_and_next_pass_finishes(self):
        self.seed()
        original = copy.deepcopy(self.db.tables["symbiosis_classifications"])
        units = [unit(n) for n in range(1, 49)]
        status, post = self.run_pass(units, [completion()] * 3 + [completion(json.dumps(model_payload()))])
        self.assertFalse(status["complete"])
        self.assertEqual(status["saved_units"], 47)
        self.assertEqual(status["remaining_units"], 1)
        self.assertEqual(post.call_count, 4)
        self.assertEqual(status["failed_units"][0]["unit_key"], unit(47)["unit_key"])
        self.assertEqual(self.db.tables["symbiosis_classifications"][:46], original)
        self.assertNotEqual(self.db.tables["symbiosis_classification_runs"][0]["status"], "success")
        status, post = self.run_pass(units, [completion(json.dumps(model_payload()))])
        self.assertTrue(status["complete"])
        self.assertEqual(status["saved_units"], 48)
        self.assertEqual(status["new_units"], 1)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.db.tables["symbiosis_classification_runs"][0]["status"], "success")
        self.assertEqual(self.db.tables["symbiosis_classifications"][:46], original)

    def test_same_recovery_works_for_a_future_week(self):
        self.seed(1, "2027-W03")
        status, _ = self.run_pass([unit(1, "2027-W03"), unit(2, "2027-W03")], [completion(json.dumps(model_payload()))])
        self.assertTrue(status["complete"])
        self.assertEqual(status["new_units"], 1)

    def test_unavailable_body_uses_database_contract_and_truthful_provenance(self):
        self.seed(0)
        status, post = self.run_pass([unit(1, body=False)], [])
        self.assertTrue(status["complete"])
        post.assert_not_called()
        row = self.db.tables["symbiosis_classifications"][0]
        self.assertEqual(row["content_basis"], "headline_only")
        self.assertEqual(common.classification_input_evidence(row)[0], "not_available")
        self.assertTrue(row["raw_output"]["classification_not_run"])
        self.assertEqual(row["model_configuration"], "insufficient_evidence")

    def test_evidence_upgrade_preserves_valid_rows_and_original_human_decision(self):
        self.seed(2)
        first, stale = self.db.tables["symbiosis_classifications"]
        first["review_status"] = "accepted"
        first["final_reasoning"] = "Owner accepted this specific finding."
        stale["content_basis"] = "headline_only"
        stale["raw_output"] = {"content_basis": "not_available", "classification_not_run": True,
                               "input_evidence": {"source_count": 1, "not_available_sources": 1}}
        original = copy.deepcopy([first, stale])
        status, post = self.run_pass([unit(1), unit(2)], [completion(json.dumps(model_payload()))], resume_only=False)
        self.assertTrue(status["complete"])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.db.tables["symbiosis_classifications"][:2], original)
        copied = self.db.tables["symbiosis_classifications"][2]
        self.assertEqual(copied["review_status"], "accepted")
        self.assertEqual(copied["final_reasoning"], first["final_reasoning"])
        self.assertEqual(copied["raw_output"]["continued_from_classification_id"], first["symbiosis_classification_id"])


class ModelBoundaryTests(unittest.TestCase):
    def test_contradictory_dimension_cannot_pass_the_generation_contract(self):
        valid = {"present": False, "direction": "not_present", "degree": 0, "confidence": 0.85, "reasoning": "No shift described."}
        require_schema(valid, dimension_schema())
        with self.assertRaises(ModelOutputError):
            require_schema({**valid, "present": True}, dimension_schema())
        with self.assertRaises(ModelOutputError):
            require_schema({**valid, "confidence": 85}, dimension_schema())

    def test_stage7c_retries_empty_answer_without_normalizing_it_into_unclear(self):
        payload = {
            "ai_relevant": True, "empowerment_status": "non_empowerment", "empowerment_degree": 0,
            "narrative_frame": "descriptive_neutral", "distribution_breadth": "unclear", "dominant_dimension": "none",
            "dimensions": {name: {"present": False, "direction": "not_present", "degree": 0, "confidence": 0.8,
                                    "reasoning": "No change described."} for name in ("operational", "creative", "agentic", "normative")},
            "ai_authority_shift": "unchanged", "topic": "other", "geographic_scope": "global",
            "country_iso3s": [], "confidence": 0.8, "reasoning": "The article describes no empowerment change.",
        }
        good_reply = http_reply(completion(json.dumps(payload)))
        good_reply.ok = True
        empty_reply = http_reply(completion())
        empty_reply.ok = True
        with patch.object(dual.requests, "post", side_effect=[empty_reply, good_reply]) as post, \
             patch.object(dual.time, "sleep"), redirect_stderr(io.StringIO()):
            result = dual.call_classifier(codebook_prompt="Fixture codebook", lens="coverage", evidence_text="Article body", content_basis="full_text")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(result["empowerment_status"], "non_empowerment")
        self.assertFalse(post.call_args.kwargs["json"]["chat_template_kwargs"]["enable_thinking"])

    def test_missing_empty_truncated_or_reasoning_only_is_not_a_research_result(self):
        for text, reason in [(None, "stop"), ("{}", "stop"), ("{broken", "stop"),
                             ("<think>{\"confidence\": 0.9}", "stop"),
                             (json.dumps(model_payload()), "length")]:
            with self.subTest(text=text, reason=reason), self.assertRaises(ModelOutputError):
                response_result(completion(text, reason))

    def test_boolean_strings_and_missing_pattern_cannot_become_true_signals(self):
        for key, bad in [("ai_relevant", "false"), ("relationship_patterns", {"mutualism": "false"}),
                         ("confidence", float("nan")), ("confidence", 90)]:
            payload = model_payload()
            payload[key] = bad
            with self.subTest(key=key), self.assertRaises(ModelOutputError):
                response_result(completion(json.dumps(payload)))

    def test_thinking_wrapper_only_uses_final_answer(self):
        payload, _ = response_result(completion('<think>{"untrusted_example": true}</think>\n' + json.dumps(model_payload())))
        self.assertEqual(payload, model_payload())

    def test_each_attempt_disables_thinking_and_uses_required_schema(self):
        with patch.object(classifier.requests, "post", side_effect=[http_reply(completion())] * 3) as post, \
             patch.object(classifier.time, "sleep"), redirect_stderr(io.StringIO()), self.assertRaises(ModelOutputError):
            classifier.call_classifier(lens="coverage", evidence="Article français 中文", content_basis="full_text")
        self.assertEqual(post.call_count, 3)
        for call in post.call_args_list:
            body = call.kwargs["json"]
            self.assertIs(body["chat_template_kwargs"]["enable_thinking"], False)
            self.assertIn("people_evidence", body["response_format"]["schema"]["required"])
            self.assertGreaterEqual(body["max_tokens"], 1600)


class MultilingualBodyTests(unittest.TestCase):
    def test_both_real_body_loaders_accept_legacy_chinese_french_and_bilingual(self):
        texts = {
            "zh": "人工智能正在帮助研究人员分析公开数据并获得新的研究工具" * 8,
            "fr": "Les chercheurs peuvent accéder aux données avec un nouveau service. " * 12,
            "ca": "Les chercheurs ont accès aux données. Researchers gain access to the data. " * 12,
        }
        for module in (classifier, dual):
            db = MemoryDB()
            db.tables["brief_article_content_snapshots"] = [
                {"article_id": key, "body_text": body, "word_count": 1, "is_current": True} for key, body in texts.items()
            ] + [{"article_id": "short", "body_text": "Short text", "word_count": 999, "is_current": True}]
            with self.subTest(loader=module.__name__):
                loader = classifier.load_full_text_map if module is classifier else dual.load_current_full_text
                result = loader(db, [*texts, "short"])
                self.assertEqual(set(result), set(texts))
                for key in texts: self.assertIn(texts[key].strip(), result[key]["body_text"])

    def test_original_french_body_is_sent_without_english_translation(self):
        db = MemoryDB()
        db.tables["articles"] = [{"article_id": "fr", "headline": "Accès aux données", "language": "fr"}]
        db.tables["brief_article_content_snapshots"] = [{"article_id": "fr", "body_text": unit(1)["evidence_text"], "word_count": 1, "is_current": True}]
        article = classifier.load_articles(db, ["fr"])["fr"]
        self.assertEqual(article["content_basis"], "full_text")
        self.assertIn("Les chercheurs", classifier.article_evidence(article))


class PublicationAndWorkflowTests(unittest.TestCase):
    def test_publisher_rejects_missing_or_stale_full_body_rows(self):
        release = {
            "units": {"coverage_articles": [{"article_id": "a", "classification": {"content_basis": "full_text"}}]},
            "evidence": [{"event_id": "e", "member_article_ids": ["a"]}],
        }
        for row in ({}, {"content_basis": "headline_only"}):
            with self.subTest(row=row), self.assertRaises(publisher.PublishError):
                publisher.require_current_full_text_lineage(release, {"a": row}, {"e": row}, {})
        body_row = {"content_basis": "full_text", "raw_output": {"input_evidence": {"full_text_sources": 1}}}
        publisher.require_current_full_text_lineage(release, {"a": body_row}, {"e": body_row}, {})

    def test_recovery_jobs_run_after_failure_but_stop_on_completion_or_cancellation(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("classify-current-symbiosis.yml", "classify-dual-lenses.yml"):
            jobs = yaml.safe_load((root / ".github" / "workflows" / name).read_text())["jobs"]
            for job_name in ("pass_2", "pass_3"):
                expression = jobs[job_name]["if"].replace("${{", "").replace("}}", "").strip()
                self.assertIn("always()", expression)  # Overrides GitHub's implicit success-only gate.
                for cancelled, complete1, complete2, expected in [
                    (False, "false", "false", True), (False, "", "", True),
                    (True, "false", "false", False), (False, "true", "", False),
                ]:
                    code = expression.replace("always()", "True").replace("cancelled()", str(cancelled))
                    code = code.replace("needs.pass_1.outputs.complete", repr(complete1)).replace("needs.pass_2.outputs.complete", repr(complete2))
                    code = code.replace("&&", " and ").replace("||", " or ")
                    code = re.sub(r"!(?!=)", " not ", code)
                    # Input is the repository-owned boolean condition; no builtins or names are allowed.
                    self.assertEqual(eval(code.strip(), {"__builtins__": {}}, {}), expected, (name, job_name, expression))

    def test_single_recovery_action_requires_classification_before_publication(self):
        path = Path(__file__).resolve().parents[1] / ".github/workflows/resume-full-body-relationship-results.yml"
        jobs = yaml.safe_load(path.read_text())["jobs"]
        self.assertEqual(jobs["publish"]["needs"], "period_summaries")
        self.assertEqual(jobs["period_summaries"]["needs"], "recover")
        self.assertEqual(jobs["recover"]["with"]["lens"], "both")
        self.assertFalse(jobs["recover"]["with"]["replace"])


if __name__ == "__main__":
    unittest.main()
