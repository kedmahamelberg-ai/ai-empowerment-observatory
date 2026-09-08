import copy
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from complete_content import build_cohort, brief_export, digest, export_audit_csv


def fixture():
    events, readings, articles = [], [], []
    for i, (direction, complete, language) in enumerate((('none', True, 'fr'), ('gain', True, 'zh'), ('unresolved', False, 'en'))):
        eid, aid = f'event-{i}', f'source-{i}'
        source = {'article_id': aid, 'url': f'https://example.com/article/{i}', 'publisher': 'Source', 'headline': 'A reported development', 'source_language': language}
        events.append({'event_id': eid, 'event_title': '=Test title', 'novelty_status': 'first_time', 'sources': [source]})
        articles.append({**source, 'event_id': eid, 'search_markets': ['FRA' if language == 'fr' else 'CHN' if language == 'zh' else 'USA']})
        body_hash = digest('Complete original text ' + language)
        readings.append({'event_id': eid, 'event_title': '=Test title', 'sources': [source],
             'axes': {'evidence_complete': complete, 'human': {'direction': direction, 'evidence': 'source evidence'}, 'ai': {'direction': 'none' if complete else 'unresolved', 'evidence': 'source evidence'}},
             'evidence_basis_summary': {'source_quality': {aid: {'usable_complete_body': complete, 'flags': [] if complete else ['missing'], 'body_sha256': body_hash, 'scope': 'written_page'}}, 'source_fingerprints': {aid: body_hash}}})
    release = {'release_id': '2026-W36', 'period_start': '2026-08-31', 'period_end': '2026-09-06', 'content_sha256': 'a' * 64,
               'counts': {'ai_relevant_event_records': 3, 'ai_relevant_articles': 3}, 'evidence': events, 'units': {'coverage_articles': articles}}
    relationship = {**{key:release[key] for key in ('release_id','period_start','period_end')}, 'content_sha256': 'b' * 64, 'source_release_sha256': release['content_sha256'], 'evidence': readings}
    return release, relationship


class CompleteContent(unittest.TestCase):
    def test_full_content_none_stays_in_denominator_and_missing_does_not(self):
        release, relationship = fixture()
        cohort = build_cohort(release, relationship)
        self.assertEqual(cohort['denominator']['value'], 2)
        self.assertEqual(cohort['directional_summary']['human'], {'gain':1,'loss':0,'mixed':0,'none':1,'unresolved':0})
        exported = brief_export(release, relationship, cohort)
        self.assertEqual({row['event_id'] for row in exported['release']['evidence']}, {'event-0','event-1'})
        self.assertNotIn('source-2', json.dumps(exported))
        self.assertNotIn('event-2', json.dumps(exported))

    def test_multiple_sources_count_once_and_missing_companion_is_not_exported(self):
        release, relationship = fixture()
        extra = {'article_id':'unavailable-companion','url':'https://example.com/blocked','publisher':'Other'}
        release['evidence'][0]['sources'].append(extra)
        relationship['evidence'][0]['sources'].append(copy.deepcopy(extra))
        release['counts']['ai_relevant_articles'] += 1
        release['units']['coverage_articles'].append({**extra,'event_id':'event-0'})
        cohort = build_cohort(release, relationship)
        self.assertEqual(cohort['counts']['eligible_developments'], 2)
        self.assertEqual(cohort['counts']['eligible_sources'], 2)
        self.assertNotIn('unavailable-companion', json.dumps(brief_export(release, relationship, cohort)))

    def test_changed_or_missing_body_hash_excludes_stale_reading(self):
        for change in ('wrong', None):
            release, relationship = fixture()
            relationship['evidence'][0]['evidence_basis_summary']['source_fingerprints']['source-0'] = change
            self.assertEqual(build_cohort(release, relationship)['counts']['eligible_developments'], 1)

    def test_media_summary_requires_recorded_complete_transcript(self):
        release, relationship = fixture()
        release['evidence'][0]['sources'][0]['url'] = 'https://station.example/audio/story'
        self.assertEqual(build_cohort(release, relationship)['counts']['eligible_developments'], 1)
        relationship['evidence'][0]['evidence_basis_summary']['source_quality']['source-0']['scope'] = 'complete_transcript'
        self.assertEqual(build_cohort(release, relationship)['counts']['eligible_developments'], 2)

    def test_empty_eligible_week_is_valid_and_not_replaced_by_inventory(self):
        release, relationship = fixture()
        for row in relationship['evidence']:
            row['axes']['evidence_complete'] = False
        cohort = build_cohort(release, relationship)
        exported = brief_export(release, relationship, cohort)
        self.assertEqual(cohort['denominator']['value'], 0)
        self.assertEqual(exported['release']['evidence'], [])
        self.assertEqual(exported['relationship']['evidence'], [])

    def test_mixed_revision_duplicate_rows_and_count_drift_fail(self):
        for mutation in ('hash','duplicate','count'):
            release, relationship = fixture()
            if mutation == 'hash': relationship['source_release_sha256'] = 'old'
            if mutation == 'duplicate': relationship['evidence'][1] = relationship['evidence'][0]
            if mutation == 'count': release['counts']['ai_relevant_articles'] += 1
            with self.assertRaises(ValueError): build_cohort(release, relationship)

    def test_audit_reconciles_every_row_and_escapes_spreadsheet_formulas(self):
        release, relationship = fixture()
        cohort = build_cohort(release, relationship)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'audit.csv'
            export_audit_csv(cohort, release, path)
            with path.open(encoding='utf-8-sig', newline='') as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(row['included_in_analysis']=='true' for row in rows), 2)
        self.assertTrue(all(row['development'].startswith("'=") for row in rows))

    def test_saved_release_inclusions_and_exclusions_reconcile(self):
        release = json.loads((ROOT/'data/releases/current.json').read_text())
        relationship = json.loads((ROOT/'data/symbiosis/current.json').read_text())
        if relationship.get('public_status') == 'classification_in_progress' or relationship.get('source_release_sha256') != release.get('content_sha256'):
            self.skipTest('The next weekly reading is still in progress.')
        cohort = build_cohort(release, relationship)
        self.assertEqual(cohort['counts']['eligible_developments'] + cohort['counts']['excluded_developments'], release['counts']['ai_relevant_event_records'])
        self.assertEqual(sum(cohort['directional_summary']['human'].values()), cohort['counts']['eligible_developments'])


if __name__ == '__main__': unittest.main()
