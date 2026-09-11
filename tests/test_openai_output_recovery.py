import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ai_runtime as ai


class OpenAIIncompleteOutputRecovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(
            os.environ,
            {
                "AIEO_AI_PROVIDER": "openai",
                "AIEO_AI_MODEL": "gpt-5-nano",
                "OPENAI_API_KEY": "test-only-never-a-real-key",
                "AIEO_AI_LEDGER": self.temp.name + "/usage.json",
            },
            clear=True,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        ai.selected_policy.cache_clear()
        self.addCleanup(ai.selected_policy.cache_clear)
        self.messages = [{"role": "user", "content": "Source evidence"}]
        self.schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        }

    def test_unfinished_answer_is_typed_recorded_and_not_accepted(self):
        body = {
            "model": "gpt-5-nano-returned-snapshot",
            "choices": [{"finish_reason": "length", "message": {"content": ""}}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "completion_tokens_details": {"reasoning_tokens": 50},
            },
        }
        response = Mock(
            status_code=200,
            headers={"x-request-id": "req-test"},
            json=Mock(return_value=body),
        )
        with patch.object(ai.requests, "post", return_value=response):
            with self.assertRaises(ai.AIOutputIncomplete) as raised:
                ai.completion(self.messages, self.schema)
        self.assertEqual(raised.exception.finish_reason, "length")
        self.assertTrue(raised.exception.diagnostics[0]["retryable"])
        ledger = json.loads(ai.ledger_path().read_text())
        self.assertEqual(ledger["calls"][0]["status"], "unfinished")
        self.assertEqual(ledger["calls"][0]["finish_reason"], "length")

    def test_classifier_defers_typed_incomplete_output_per_unit(self):
        source = (ROOT / "scripts/classify_symbiosis.py").read_text(encoding="utf-8")
        self.assertIn(
            "except (ModelOutputError, ai_runtime.AIOutputIncomplete) as exc:",
            source,
        )
        self.assertIn("the next bounded pass retries the remaining gap", source)


if __name__ == "__main__":
    unittest.main()
