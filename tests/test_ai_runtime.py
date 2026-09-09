import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import ai_runtime as ai


class AIRuntime(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'AIEO_AI_PROVIDER': 'openai', 'AIEO_AI_MODEL': 'gpt-5-nano',
            'OPENAI_API_KEY': 'test-only-never-a-real-key', 'AIEO_AI_LEDGER': self.temp.name+'/usage.json'}, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        ai.selected_policy.cache_clear(); self.addCleanup(ai.selected_policy.cache_clear)
        self.messages = [{'role':'user','content':'Private article text. /no_think'}]
        self.schema = {'type':'object','properties':{'answer':{'type':'string'}},'required':['answer'],'additionalProperties':False}

    def response(self, status=200, **overrides):
        body = {'model':'gpt-5-nano-returned-snapshot','choices':[{'finish_reason':'stop','message':{'content':'{"answer":"ok"}'}}],
                'usage':{'prompt_tokens':100,'completion_tokens':50,'completion_tokens_details':{'reasoning_tokens':20}}, **overrides}
        return Mock(status_code=status, headers={'x-request-id':'req-test'}, json=Mock(return_value=body))

    def test_cutover_at_exact_december_boundary_and_future_years(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ai.resolve_policy(datetime(2026,12,9,23,59,59,tzinfo=timezone.utc))['model'],'gpt-5-nano')
            self.assertEqual(ai.resolve_policy(datetime(2026,12,10,tzinfo=timezone.utc))['model'],'gpt-5.6-luna')
            self.assertEqual(ai.resolve_policy(datetime(2027,1,1,tzinfo=timezone.utc))['model'],'gpt-5.6-luna')

    def test_key_missing_stops_before_network_or_ledger(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY':''}), patch.object(ai.requests,'post') as post:
            with self.assertRaises(ai.AIError):ai.completion(self.messages,self.schema)
            post.assert_not_called(); self.assertFalse(ai.ledger_path().exists())

    def test_official_parameters_provenance_and_no_private_ledger_content(self):
        with patch.object(ai.requests,'post',return_value=self.response()) as post:
            result=ai.completion(self.messages,self.schema)
        payload=post.call_args.kwargs['json']
        self.assertFalse(payload['store']);self.assertEqual(payload['model'],'gpt-5-nano')
        self.assertTrue(payload['response_format']['json_schema']['strict'])
        self.assertGreaterEqual(payload['max_completion_tokens'],2048)
        for unsupported in ('temperature','top_p','top_k','seed','max_tokens','chat_template_kwargs'):
            self.assertNotIn(unsupported,payload)
        self.assertNotIn('/no_think',payload['messages'][0]['content'])
        self.assertEqual(result['aieo_ai']['returned_model'],'gpt-5-nano-returned-snapshot')
        log=ai.ledger_path().read_text();self.assertNotIn('Private article',log);self.assertNotIn('test-only',log)
        self.assertAlmostEqual(json.loads(log)['calls'][0]['cost_usd'],.000025)

    def test_luna_payload_uses_luna_and_its_price(self):
        with patch.dict(os.environ, {'AIEO_AI_MODEL':'gpt-5.6-luna'}), patch.object(ai.requests,'post',return_value=self.response()) as post:
            result=ai.completion(self.messages,self.schema)
        self.assertEqual(post.call_args.kwargs['json']['model'],'gpt-5.6-luna')
        self.assertAlmostEqual(result['aieo_ai']['cost_usd'],.000085)

    def test_cost_cap_is_reserved_before_request(self):
        with patch.dict(os.environ, {'AIEO_AI_MAX_JOB_USD':'0.000001'}), patch.object(ai.requests,'post') as post:
            with self.assertRaises(ai.AIBudgetExceeded):ai.completion(self.messages,self.schema)
            post.assert_not_called()

    def test_timeout_keeps_reservation_and_does_not_duplicate_request(self):
        with patch.object(ai.requests,'post',side_effect=ai.requests.Timeout) as post:
            with self.assertRaises(ai.AIError):ai.completion(self.messages,self.schema)
        self.assertEqual(post.call_count,1)
        self.assertGreater(json.loads(ai.ledger_path().read_text())['calls'][0]['cost_usd'],0)

    def test_quota_failure_is_not_retried_and_stops_following_calls(self):
        with patch.object(ai.requests,'post',return_value=self.response(429,error={'code':'insufficient_quota','message':'private diagnostic'})) as post:
            for _ in range(2):
                with self.assertRaises(ai.AIError) as error:ai.completion(self.messages,self.schema)
                self.assertNotIn('private diagnostic',str(error.exception))
            self.assertEqual(post.call_count,1)

    def test_rate_limit_retries_are_bounded(self):
        with patch.object(ai.requests,'post',side_effect=[self.response(429),self.response()]) as post, patch.object(ai.time,'sleep'):
            ai.completion(self.messages,self.schema)
            self.assertEqual(post.call_count,2)

    def test_unfinished_and_refused_answers_are_not_accepted(self):
        for choice in ({'finish_reason':'length','message':{'content':'{"answer":'}},
                       {'finish_reason':'stop','message':{'refusal':'refused'}}):
            with patch.object(ai.requests,'post',return_value=self.response(choices=[choice])):
                with self.assertRaises(ai.AIError):ai.completion(self.messages,self.schema)

    def test_schema_conversion_preserves_disjoint_branches_and_input(self):
        original={'type':'object','properties':{'dimension':{'oneOf':[
            {'type':'object','properties':{'present':{'type':'boolean','enum':[value]}}} for value in (True,False)]}}}
        before=copy.deepcopy(original);result=ai.strict_schema(original)
        self.assertEqual(original,before);self.assertEqual(result['required'],['dimension'])
        for branch in result['properties']['dimension']['anyOf']:
            self.assertEqual(branch['required'],['present']);self.assertFalse(branch['additionalProperties'])

    def test_nonfinite_budget_and_unapproved_model_are_rejected(self):
        for setting in ({'AIEO_AI_MAX_JOB_USD':'nan'},{'AIEO_AI_MAX_JOB_USD':'-1'},{'AIEO_AI_MODEL':'imaginary-model'}):
            with patch.dict(os.environ,setting):
                with self.assertRaises(ValueError):ai.resolve_policy()


if __name__ == '__main__':unittest.main()
