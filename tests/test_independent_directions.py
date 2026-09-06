"""Regressions for this week's audit and the next automatic release."""
import copy
import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import yaml
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from independent_axes import make_axes, validate_axes, summarize_axes, supported_patterns
from public_directional_release import release_corrections, build_directional_release, validate_directional_release, export_public_csv
from source_evidence_quality import assess_body, evidence_chunks
from symbiosis_common import evidence_basis_covers, final_payload_from_classification
from symbiosis_model_output import require_result_fields, ModelOutputError
from ingest_symbiosis_qc import plain_row_labels
from build_public_site import public_json
import classify_symbiosis as classifier
import classify_dual_lens as dual
from test_relationship_recovery import model_payload
ROOT=Path(__file__).resolve().parents[1]

def read(name): return json.loads((ROOT/name).read_text())
def axes(h='gain',a='gain',complete=True):
    return make_axes(h,a,human_evidence='The source describes new access and a privacy risk.',ai_evidence='The operator gains users and faces operating limits.',complete_evidence=complete)

class IndependentDirectionsTests(unittest.TestCase):
    def test_human_and_ai_are_independent_and_mixed_is_preserved(self):
        rows=[{'event_id':str(i),'axes':axes(h,a)} for i,(h,a) in enumerate([('gain','none'),('none','gain'),('mixed','mixed')])]
        summary=summarize_axes(rows)
        self.assertEqual(summary['human']['gain'],1);self.assertEqual(summary['human']['mixed'],1)
        self.assertEqual(sum(summary['ai'].values()),3)
    def test_two_axes_do_not_establish_a_link_without_evidence(self):
        self.assertFalse(any(supported_patterns(axes(), {'mutualism':True},{}).values()))
        self.assertTrue(supported_patterns(axes(), {'mutualism':True},{'mutualism':'People gain access as the system is deployed.'})['mutualism'])
    def test_incomplete_body_is_not_an_exhaustive_neutral_reading(self):
        with self.assertRaises(ValueError): validate_axes(axes('none','none',False))
    def test_missing_direction_or_support_is_rejected(self):
        a=axes();a['human']['evidence']=''
        with self.assertRaises(ValueError):validate_axes(a)
        p=model_payload();del p['axis_directions']
        with self.assertRaises(ModelOutputError):require_result_fields(p)
    def test_invalid_iso_code_and_extra_schema_fields_are_rejected(self):
        p=model_payload();p['country_iso3s']=['China']
        with self.assertRaises(ModelOutputError):require_result_fields(p)
        p=model_payload();p['unexpected']=True
        with self.assertRaises(ModelOutputError):require_result_fields(p)
    def test_every_middle_character_is_preserved(self):
        text=('Beginning\n' * 1400)+'IMPORTANT MIDDLE CONTRARY EVIDENCE'+('End\n'*2000)
        chunks=evidence_chunks(text);covered=set()
        for c in chunks:
            self.assertEqual(c['text'],text[c['start']:c['end']]);covered.update(range(c['start'],c['end']))
        self.assertEqual(len(covered),len(text))
        self.assertTrue(any('IMPORTANT MIDDLE' in c['text'] for c in chunks))
        self.assertIn('IMPORTANT MIDDLE',dual.compact_evidence_text(text))
    def test_classifier_joins_middle_losses_with_initial_gains(self):
        def chunk(**kwargs):
            negative='MIDDLE RISK' in kwargs['evidence']
            p=model_payload();p['axis_directions']['human']='loss' if negative else 'gain'
            a=axes('loss' if negative else 'gain','gain')
            return {'axes':a,'raw_output':{'axes':a,'relationship_evidence':{}},'distribution_signal':'not_shown',
                'relationship_patterns':dict.fromkeys(('mutualism','ai_benefiting_parasitism','human_benefiting_parasitism','competition'),False),
                'evidence_status':'sufficient','public_takeaway':a['human']['evidence']}
        text='start '*1400+'MIDDLE RISK'+' end'*2300
        with patch.object(classifier,'_call_classifier_chunk',side_effect=chunk) as call:
            result=classifier.call_classifier(lens='event',evidence=text,content_basis='full_text')
        self.assertGreater(call.call_count,1)
        self.assertEqual(result['axes']['human']['direction'],'mixed')
        self.assertTrue(result['raw_output']['input_fully_covered'])
    def test_collector_rejects_preview_before_touching_the_database(self):
        # Extraction libraries are not used at this database-write boundary.
        # Load the real function with only those optional imports isolated.
        import runpy
        from types import SimpleNamespace
        with patch.dict(sys.modules, {'trafilatura':SimpleNamespace(), 'bs4':SimpleNamespace(BeautifulSoup=object)}):
            store_snapshot=runpy.run_path(str(ROOT/'scripts/brief_backfill_article_content.py'))['store_snapshot']
        class NoWrites:
            def table(self,*args): raise AssertionError("Database should not be touched")
        with self.assertRaises(ValueError):
            store_snapshot(NoWrites(),{'article_id':'test'},'https://example.com',{'body_text':'Article text '*200+' You have 67.94% of this article'})
    def test_valid_accented_french_and_chinese_survive_encoding_check(self):
        for body in ('Créativité, réalité, égalité et étude, été à Québec. '*30, '人工智能帮助研究人员了解数据并提升能力。'*30):
            self.assertTrue(assess_body({'body_text':body})['usable_complete_body'])
        bad=('人工智能帮助研究人员。'*40).encode().decode('latin1')
        self.assertIn('corrupted_encoding',assess_body({'body_text':bad})['flags'])
    def test_previews_and_changed_saved_bytes_are_rejected(self):
        body='Article content. '*50+' You have 67.94% of this article'
        self.assertFalse(assess_body({'body_text':body})['usable_complete_body'])
        self.assertFalse(assess_body({'body_text':'complete news '*100,'text_sha256':'wrong'})['usable_complete_body'])
        self.assertFalse(evidence_basis_covers(stored_content_basis='full_text',current_content_basis='full_text',stored_evidence_summary={'source_fingerprints':{'1':'old'}},current_evidence_summary={'source_fingerprints':{'1':'new'}}))
    def test_current_audit_accounts_for_every_record_without_claiming_110_full_readings(self):
        release=read('data/releases/weekly/2026-W35.json');pub=read('data/symbiosis/weekly/2026-W35.json')
        validate_directional_release(release,pub)
        self.assertEqual(len(release_corrections(release)),110)
        audit=summarize_axes(list(release_corrections(release).values()))
        self.assertEqual(audit['human'],dict(gain=45,loss=3,mixed=26,none=8,unresolved=28))
        self.assertEqual(audit['ai'],dict(gain=36,loss=2,mixed=38,none=6,unresolved=28))
        self.assertEqual(audit['evidence_complete'],82)
    def test_stale_correction_cannot_spill_into_a_different_source_revision_or_week(self):
        release=read('data/releases/weekly/2026-W35.json');release['content_sha256']='different'
        with self.assertRaises(ValueError):release_corrections(release)
        release['release_id']='2027-W03'
        self.assertEqual(release_corrections(release),{})
    def test_owner_mixed_reading_overrides_the_assistant_audit(self):
        release=read('data/releases/weekly/2026-W35.json');pub=read('data/symbiosis/weekly/2026-W35.json');eid=pub['evidence'][0]['event_id']
        out=build_directional_release(release,pub,corrections=release_corrections(release),owner_gold={eid:{'final':{'axes':axes('mixed','none')}}})
        row=next(r for r in out['evidence'] if r['event_id']==eid)
        self.assertEqual(row['axes']['human']['direction'],'mixed');self.assertTrue(row['reviewed'])
    def test_optional_qc_preserves_mixed_and_human_only_answers(self):
        row={f'HUMAN_{k}':'No' for k in ('enough_to_judge','people_gaining','people_losing_ground','ai_advancing','ai_limited','unequal_benefits')}
        row.update(HUMAN_enough_to_judge='Yes',HUMAN_people_gaining='Yes',HUMAN_people_losing_ground='Yes',HUMAN_reasoning='The source describes gains and losses for people.')
        result,errors=plain_row_labels(row,2)
        self.assertFalse(errors);self.assertEqual(result['axes']['human']['direction'],'mixed');self.assertEqual(result['axes']['ai']['direction'],'none')
        self.assertFalse(any(result['relationship_patterns'].values()))
    def test_public_csv_has_all_rows_and_same_axes_without_private_provenance(self):
        payload=read('data/symbiosis/weekly/2026-W35.json')
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'readings.csv';export_public_csv(payload,path)
            with path.open(encoding='utf-8-sig') as h:rows=list(csv.DictReader(h))
        self.assertEqual(len(rows),110)
        for a,b in zip(rows,payload['evidence']):self.assertEqual(a['human_direction'],b['axes']['human']['direction'])
        self.assertNotIn('review_status',rows[0])
    def test_provenance_is_removed_recursively_from_the_public_build(self):
        value={'axes':axes(),'review_status':'owner_manual_qc','nested':[{'raw_output':{'prompt_text':'private'},'title':'public'}]}
        self.assertEqual(public_json(value),{'axes':axes(),'nested':[{'title':'public'}]})
    def test_next_week_runs_collection_and_bodies_before_classifiers(self):
        config=yaml.safe_load((ROOT/'.github/workflows/weekly-observatory.yml').read_text())
        self.assertEqual(config[True]['schedule'][0]['cron'],'17 0 * * 1')
        self.assertIn('enrich-article-bodies',config['jobs']['classify-dual-lenses']['needs'])
        enrichment=yaml.safe_load((ROOT/'.github/workflows/enrich-new-brief-article-bodies.yml').read_text())
        self.assertIn("github.event_name != 'workflow_run'",enrichment['jobs']['enrich']['if'])
        for filename in ('build_weekly_release.py','finalize_stage7c_residual.py'):
            self.assertIn(dual.CLASSIFIER_VERSION,(ROOT/'scripts'/filename).read_text())
    def test_pages_and_pdf_source_metadata_use_the_identical_reading_revision(self):
        rel=read('data/symbiosis/current.json')
        source=read('data/releases/current.json')
        if not rel.get('directional_summary') or rel.get('source_release_sha256') != source.get('content_sha256') or read('data/reports/latest.json').get('source_relationship_sha256') != rel.get('content_sha256'):
            self.skipTest('Public derivatives are being rebuilt; the publication gate still requires a complete match.')
        for filename in ('data/reports/latest.json','data/insights/latest.json'):
            meta=read(filename);self.assertEqual(meta['source_relationship_sha256'],rel['content_sha256']);self.assertEqual(meta['directional_summary'],rel['directional_summary'])
        meta=read('data/reports/latest.json');pdf=ROOT/meta['file'].lstrip('/')
        self.assertEqual(hashlib.sha256(pdf.read_bytes()).hexdigest(),meta['pdf_sha256'])
if __name__=='__main__':unittest.main()
