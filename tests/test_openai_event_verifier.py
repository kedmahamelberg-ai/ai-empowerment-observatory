import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ai_runtime
import openai_event_verifier as verifier


class EventVerifierTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"AIEO_AI_PROVIDER": "openai", "AIEO_AI_MODEL": "gpt-5-nano",
                                     "AIEO_EVENT_CACHE": temp.name}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        ai_runtime.selected_policy.cache_clear()
        self.addCleanup(ai_runtime.selected_policy.cache_clear)
        self.article = {"headline_english": "Company launches model A"}
        self.evidence = [{"evidence_id": "b", "headline_english": "Model A released by company"}]
        self.pair = {"evidence_id": "b", "relationship": "same_event", "reason": "Same named launch"}

    def response(self, pairs=None):
        return {"choices": [{"message": {"content": json.dumps({"pairs": pairs or [self.pair]})}}],
                "aieo_ai": {"returned_model": "nano-snapshot", "cost_usd": 0.001}}

    def scripted_reply(self, *args, **kwargs):
        if kwargs.get("name") == "event_identity_check":
            return {"choices": [{"message": {"content": json.dumps({"checks": [{
                "evidence_id": "b", "supported": True,
                "shared_occurrence": "Company releases model A", "reason": "Both identify this launch"
            }]})}}], "aieo_ai": {"returned_model": "nano-snapshot", "cost_usd": 0.001}}
        return self.response()

    def test_cache_reuses_identical_evidence_but_not_changed_text(self):
        with patch.object(ai_runtime, "completion", side_effect=self.scripted_reply) as request:
            a = verifier.verify(self.article, self.evidence)
            b = verifier.verify(self.article, self.evidence)
            self.assertEqual(request.call_count, 2)
            self.assertFalse(a["cache_hit"])
            self.assertTrue(b["cache_hit"])
            verifier.verify({"headline_english": "Different launch"}, self.evidence)
            self.assertEqual(request.call_count, 4)

    def test_cache_is_separated_by_model(self):
        with patch.object(ai_runtime, "completion", side_effect=self.scripted_reply) as request:
            verifier.verify(self.article, self.evidence)
            with patch.dict(os.environ, {"AIEO_AI_MODEL": "gpt-6-luna"}):
                ai_runtime.selected_policy.cache_clear()
                verifier.verify(self.article, self.evidence)
            self.assertEqual(request.call_count, 4)

    def test_missing_duplicate_and_unknown_evidence_fail_closed(self):
        for pairs in ([], [self.pair, self.pair], [{**self.pair, "evidence_id": "invented"}]):
            with self.subTest(pairs=pairs):
                with self.assertRaises(ValueError):
                    verifier.validate({"pairs": pairs}, {"b"})

    def test_invalid_and_truncated_outputs_require_review(self):
        for reply in (self.response([{**self.pair, "relationship": "invented"}]),
                      ai_runtime.AIOutputIncomplete("truncated", finish_reason="length")):
            with patch.object(ai_runtime, "completion", **({"side_effect": reply} if isinstance(reply, Exception) else {"return_value": reply})):
                verdict = verifier.verify(self.article, self.evidence)
                self.assertEqual(verdict["relationship"], "unclear")
                self.assertEqual(verifier.assignment_policy(verdict, similarity=.99, competing=False)[0], "review")

    def test_auth_and_budget_failures_stop_without_creating_labels(self):
        for error in (ai_runtime.AIError("quota"), ai_runtime.AIBudgetExceeded("cap")):
            with patch.object(ai_runtime, "completion", side_effect=error):
                with self.assertRaises(type(error)):
                    verifier.verify(self.article, self.evidence)

    def test_only_supported_uncontested_same_event_can_merge(self):
        for label, similarity, competing, expected in (
            ("same_event", .8, False, "auto_merge"),
            ("same_event", .8, True, "review"),
            ("same_event", .6, False, "review"),
            ("related_topic", .99, False, "new_event"),
            ("different_event", .99, False, "new_event"),
            ("unclear", .6, False, "review"),
        ):
            self.assertEqual(verifier.assignment_policy({"relationship": label}, similarity=similarity, competing=competing)[0], expected)

    def test_second_check_vetoes_a_merge_and_records_total_cost(self):
        def reply(*args, **kwargs):
            response = self.scripted_reply(*args, **kwargs)
            if kwargs.get("name") == "event_identity_check":
                payload = json.loads(response["choices"][0]["message"]["content"])
                payload["checks"][0].update(supported=False, shared_occurrence="", reason="Shared subject, no identified action")
                response["choices"][0]["message"]["content"] = json.dumps(payload)
            return response
        with patch.object(ai_runtime, "completion", side_effect=reply) as request:
            result = verifier.verify(self.article, self.evidence)
            cached = verifier.verify(self.article, self.evidence)
        self.assertEqual(result["relationship"], "unclear")
        self.assertEqual(result["audit"]["cost_usd"], 0.002)
        self.assertEqual(cached["identity_checks"], result["identity_checks"])
        self.assertEqual(request.call_count, 2)

    def test_second_check_rejects_missing_evidence_or_string_boolean(self):
        for checks in ([], [{"evidence_id": "b", "supported": "true", "shared_occurrence": "launch", "reason": "same"}]):
            response = {"choices": [{"message": {"content": json.dumps({"checks": checks})}}], "aieo_ai": {}}
            with patch.object(ai_runtime, "completion", side_effect=[self.response(), response]):
                result = verifier.verify(self.article, self.evidence)
            self.assertEqual(result["relationship"], "unclear")
            self.assertIn("error", result)

    def test_history_model_returns_proposal_without_confidence(self):
        response = {"choices": [{"message": {"content": json.dumps({
            "relationship": "follow_on_development", "reason": "A new dated lawsuit follows the policy"
        })}}], "aieo_ai": {"returned_model": "nano"}}
        with patch.object(ai_runtime, "completion", return_value=response) as request:
            result = verifier.verify_longitudinal({"new_event": "lawsuit", "earlier_event": "policy"})
        self.assertEqual(result["relationship"], "follow_on_development")
        self.assertIsNone(result["confidence"])
        self.assertNotIn("confidence", request.call_args.args[1]["properties"])

    def test_history_invalid_output_cannot_be_accepted_and_outages_propagate(self):
        response = {"choices": [{"message": {"content": '{"relationship":"same_event","reason":"x","confidence":1}'}}]}
        with patch.object(ai_runtime, "completion", return_value=response):
            self.assertEqual(verifier.verify_longitudinal({})["relationship"], "unclear")
        with patch.object(ai_runtime, "completion", side_effect=ai_runtime.AIBudgetExceeded("cap")):
            with self.assertRaises(ai_runtime.AIBudgetExceeded):
                verifier.verify_longitudinal({})

    def test_retirement_overrides_a_stale_nano_pin(self):
        policy = ai_runtime.resolve_policy(datetime(2026, 12, 9, 23, tzinfo=timezone.utc))
        self.assertEqual(policy["model"], "gpt-6-luna")
        self.assertEqual(policy["reasoning_effort"], "none")


if __name__ == "__main__":
    unittest.main()
