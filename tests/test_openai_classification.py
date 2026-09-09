import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ai_runtime as ai
import classify_symbiosis as classifier
from test_relationship_recovery import model_payload, completion, MemoryDB
from symbiosis_model_output import ModelOutputError


class OpenAIClassification(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'AIEO_AI_PROVIDER':'openai','AIEO_AI_MODEL':'gpt-5-nano','OPENAI_API_KEY':'fixture-only'})
        env.start(); self.addCleanup(env.stop)
        ai.selected_policy.cache_clear(); self.addCleanup(ai.selected_policy.cache_clear)

    def test_api_answer_uses_existing_domain_validator_and_records_model(self):
        reply = completion(json.dumps(model_payload()))
        reply['aieo_ai'] = {'requested_model':'gpt-5-nano','returned_model':'gpt-5-nano-fixture','usage':{'completion_tokens':321}}
        with patch.object(ai, 'completion', return_value=reply) as request:
            result = classifier._call_classifier_chunk(lens='event', evidence='Fixture article body', content_basis='full_text')
        self.assertEqual(request.call_args.kwargs['name'], 'observatory_relationship')
        self.assertEqual(result['structured_output_mode'], 'openai_json_schema')
        self.assertEqual(result['raw_output']['generation']['returned_model'], 'gpt-5-nano-fixture')
        bad = completion(json.dumps({**model_payload(), 'human_reasoning': 'x' * 281}))
        with patch.object(ai, 'completion', return_value=bad), self.assertRaises(ModelOutputError):
            classifier._call_classifier_chunk(lens='event', evidence='Fixture article body', content_basis='full_text')

    def test_nano_cannot_resume_an_interrupted_local_or_luna_run(self):
        db=MemoryDB()
        for name in (classifier.QWEN_REPO, 'gpt-5.6-luna'):
            db.tables['symbiosis_classification_runs']=[{'symbiosis_run_id':'old','run_key':'old','scope':'latest_release',
                'target_release_id':'2026-W36','classifier_version':classifier.CLASSIFIER_VERSION,'codebook_version':classifier.CODEBOOK_VERSION,
                'model_name':name,'model_revision':ai.identity()['revision'],'status':'failed','started_at':'2026-09-08'}]
            with self.subTest(model=name), self.assertRaises(classifier.SymbiosisClassificationError):
                classifier.resume_or_start_run(db, scope='latest_release', target_release_id='2026-W36', collection_run_id=None,
                    empowerment_run_id=None, model_revision=ai.identity()['revision'], resume_only=True)
        self.assertEqual(len(db.tables['symbiosis_classification_runs']), 1)

    def test_api_path_does_not_start_local_server(self):
        self.assertEqual(classifier.start_server(), (None, None))
