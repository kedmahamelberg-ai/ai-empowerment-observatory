"""Acquisition regressions: wrong-body acceptance is worse than an honest gap."""
import copy
import io
import json
import os
import sys
import tempfile
import unittest
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
if any(importlib.util.find_spec(name) is None for name in ('bs4', 'trafilatura', 'tldextract', 'pypdf')):
    raise unittest.SkipTest('Article acquisition tests require requirements-extraction.txt; the enrichment workflow installs it and requires these tests.')
import brief_backfill_article_content as base
import brief_backfill_article_content_resumable as runner
import article_recovery_support as support
from article_recovery_formats import publisher_content, pdf_content

URL = "https://news.example.com/research/new-community-tools"
TITLE = "Researchers publish new community tools"
BODY = "\n\n".join(f"Paragraph {i}. Researchers describe how people use these tools, assess their limitations, and retain control of the decisions. " * 2 for i in range(8))


def page(body=BODY, extra="", title=TITLE):
    return f'<html><head><title>{title}</title>{extra}</head><body><article><h1>{title}</h1><div class="article-body">{body}</div></article></body></html>'


def response(raw, mime="text/html", status=200, url=URL):
    import requests
    r = requests.Response()
    r._content = raw.encode() if isinstance(raw, str) else raw
    r._content_consumed = True
    r.headers["content-type"] = mime
    r.status_code, r.url, r.encoding = status, url, None
    return r


class AcquisitionTests(unittest.TestCase):
    def test_comment_captcha_is_not_a_page_challenge(self):
        html = page(extra='<script src="https://www.google.com/recaptcha/api.js"></script>')
        self.assertFalse(base.detect_access_challenge(html))
        self.assertFalse(base.detect_access_challenge(page(BODY + " This research tests CAPTCHA accuracy.")))

    def test_real_challenge_remains_blocked(self):
        for html in ['<title>Just a moment...</title><script src="/cdn-cgi/challenge-platform"></script>',
                     '<form id="challenge-form">Complete this verification</form>',
                     '<h1>Verify you are human</h1>']:
            self.assertTrue(base.detect_access_challenge(html))

    def test_unused_subscription_code_and_footer_are_not_paywalls(self):
        html = page(extra='<script>const widget = "subscriptionRequired meteredContent already a subscriber";</script>')
        html = html.replace('</body>', '<footer>Already a subscriber? Sign in</footer></body>')
        self.assertFalse(base.detect_paywall(html, URL))

    def test_true_paywalls_and_french_gate_remain_blocked(self):
        for gate in ('Subscribe to continue reading', 'Cet article est réservé aux abonnés', 'Connectez-vous pour lire la suite'):
            self.assertTrue(base.detect_paywall(page(gate), URL))
        data = {"@type": "NewsArticle", "url": URL, "isAccessibleForFree": False, "articleBody": BODY}
        self.assertTrue(base.detect_paywall(page(extra=f'<script type="application/ld+json">{json.dumps(data)}</script>'), URL))

    def test_related_paid_story_cannot_block_free_article(self):
        data = {"@graph": [{"@type": "NewsArticle", "url": URL, "isAccessibleForFree": True},
                            {"@type": "NewsArticle", "url": URL+"-different", "isAccessibleForFree": False}]}
        self.assertFalse(base.detect_paywall(page(extra=f'<script type="application/ld+json">{json.dumps(data)}</script>'), URL))

    def test_character_encodings_are_honoured(self):
        for encoding, text in [('gb18030', '人工智能帮助研究人员分析数据并提高能力。'*12), ('windows-1252', 'Créativité, égalité et étude à Québec. '*15), ('utf-8', '数据和研究，à Québec. '*25)]:
            raw = f'<html><head><meta charset="{encoding}"></head><body>{text}</body></html>'.encode(encoding)
            self.assertIn(text, base.decode_article_html(response(raw)))
        r = response('研究人员使用数据。'*40)
        r.encoding = 'ISO-8859-1'
        self.assertIn('研究人员', base.decode_article_html(r))

    def test_arbitrary_embedded_text_is_not_an_article(self):
        data = {"recommendations": [{"text": BODY*3}], "analytics": {"text": BODY*5}}
        html = '<script id="__NEXT_DATA__" type="application/json">'+json.dumps(data)+'</script>'
        self.assertEqual(list(support.embedded_bodies(html, URL)), [])

    def test_scholarly_abstract_is_not_classified_as_full_paper(self):
        html = '<meta name="citation_doi" content="10.1000/test"><section id="abstract"><h2>Abstract</h2>'+BODY+'</section>'
        self.assertEqual(base.extract_html_result(html, {'final_url': URL})['outcome'], 'abstract_only')
        self.assertFalse(support.abstract_only(html+'<section><h2>Methods</h2>'+BODY+'</section>'))

    def test_hidden_subscription_template_is_not_a_gate(self):
        html = page().replace('</body>', '<div style="display: none">Subscribe to continue reading</div></body>')
        self.assertFalse(base.detect_paywall(html, URL))

    def test_soft_404_never_turns_recommendations_into_a_body(self):
        html = page(BODY, title='Page not found')
        self.assertEqual(base.extract_html_result(html, {'final_url': URL})['outcome'], 'source_unavailable')

    def test_structured_body_is_plain_text_and_complete(self):
        data = {"@type": "NewsArticle", "url": URL, "articleBody": '<p>'+BODY+'</p><p>Final evidence contradicts the initial promise.</p>'}
        html = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        picked = base.choose_best_extraction(html, URL)
        self.assertIn('Final evidence contradicts', picked['text'])
        self.assertNotIn('<p>', picked['text'])

    def test_publisher_siblings_and_private_suffixes(self):
        self.assertTrue(base.same_publisher_site('https://news.arizona.edu/story', 'https://research.arizona.edu/story'))
        self.assertFalse(base.same_publisher_site('https://evil.co.uk/story', 'https://news.co.uk/story'))
        self.assertFalse(base.same_publisher_site('https://evil.github.io/story', 'https://good.github.io/story'))
        self.assertFalse(base.same_publisher_site('https://arizona.edu.evil.com/story', 'https://arizona.edu/story'))

    def test_full_story_link_is_discovered_and_wrong_title_rejected(self):
        html = '<h1>'+TITLE+'</h1><a href="https://news.arizona.edu/news/community">Read more at University of Arizona News</a>'
        links = base.public_alternate_urls(html, 'https://research.arizona.edu/news/community')
        self.assertEqual(links[0]['kind'], 'publisher_linked_full_story')
        self.assertFalse(support.same_article(page(title='Contact the university admissions office'), links[0]['url'], URL, TITLE))

    def test_feed_accepts_only_matching_full_content(self):
        xml = f'<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item><link>{URL}</link><title>{TITLE}</title><description>summary only</description><content:encoded><![CDATA[<p>{BODY}</p>]]></content:encoded></item></channel></rss>'.encode()
        self.assertIn('Paragraph 7', publisher_content(xml, 'application/rss+xml', URL)['text'])
        self.assertIsNone(publisher_content(xml, 'application/rss+xml', URL+'-other'))
        self.assertIsNone(publisher_content(f'<rss><channel><item><link>{URL}</link><description>{BODY}</description></item></channel></rss>'.encode(), 'application/rss+xml', URL))

    def test_xml_entities_are_never_expanded(self):
        self.assertIsNone(publisher_content(b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss>&x;</rss>', 'application/rss+xml', URL))

    def test_cms_requires_public_exact_article(self):
        post = {'status': 'publish', 'link': URL, 'title': {'rendered': TITLE}, 'content': {'rendered': '<p>'+BODY+'</p>', 'protected': False}}
        self.assertIsNotNone(publisher_content(json.dumps(post).encode(), 'application/json', URL))
        for replacement in ({'link': URL+'-other'}, {'status': 'private'}, {'content': {'rendered': BODY, 'protected': True}}):
            self.assertIsNone(publisher_content(json.dumps({**post, **replacement}).encode(), 'application/json', URL))

    def test_linked_pdf_preserves_all_pages_and_rejects_missing_text(self):
        class Reader:
            is_encrypted = False
            pages = [SimpleNamespace(extract_text=lambda: BODY), SimpleNamespace(extract_text=lambda: 'SECOND PAGE '+BODY)]
        with patch('pypdf.PdfReader', return_value=Reader()):
            self.assertIn('SECOND PAGE', pdf_content(b'pdf')['text'])
            Reader.pages[1] = SimpleNamespace(extract_text=lambda: '')
            with self.assertRaises(ValueError):
                pdf_content(b'pdf')

    def test_no_fallback_after_real_access_refusal(self):
        for reason in ('blocked_paywall_or_login', 'blocked_bot_challenge', 'blocked_robots', 'blocked_access_control', 'blocked_tdm_reserved'):
            with patch.object(base, 'fetch_public_candidate', return_value={'outcome': reason, '_html': page()}), patch.object(base, 'render_fallback') as render:
                self.assertEqual(base.fetch_and_extract(URL)['outcome'], reason)
                render.assert_not_called()

    def test_full_story_recovery_records_identity_and_provenance(self):
        linked = 'https://www.example.com/research/new-community-tools'
        primary = {'outcome': 'too_little_extractable_text', 'final_url': URL, '_html': f'<h1>{TITLE}</h1><a href="{linked}">Read the full article</a>'}
        alternate = {'outcome': 'stored', 'final_url': linked, '_html': page(), 'body_text': BODY}
        with patch.object(base, 'fetch_public_candidate', side_effect=[primary, alternate]):
            result = base.fetch_and_extract(URL)
        self.assertEqual(result['body_text'], BODY)
        self.assertTrue(result['recovered_from_alternate'])
        self.assertEqual(len(result['recovery_trace']), 2)
        self.assertNotIn('_html', result)

    def test_browser_failure_does_not_become_a_complete_body(self):
        primary = {'outcome': 'too_little_extractable_text', 'final_url': URL, '_html': '<div id="app"></div>'}
        with patch.object(base, 'fetch_public_candidate', return_value=primary), patch.object(base, 'render_fallback', return_value={'outcome': 'browser_timeout'}):
            result = base.fetch_and_extract(URL)
        self.assertEqual(result['outcome'], 'browser_timeout')
        self.assertNotIn('body_text', result)

    def test_long_retry_after_is_deferred(self):
        r = response('', status=429)
        r.headers['Retry-After'] = '120'
        with patch.object(base, 'public_url', return_value=True), patch.object(base.requests, 'get', return_value=r) as get, patch.object(base.time, 'sleep') as sleep:
            returned, _, _ = base.public_get(URL, timeout=(2, 2), accept='text/html')
        self.assertEqual(returned.status_code, 429)
        self.assertEqual(get.call_count, 1)
        sleep.assert_not_called()

    def test_private_network_urls_are_rejected(self):
        for url in ('file:///etc/passwd', 'http://localhost/x', 'http://127.0.0.1/x', 'http://169.254.169.254/latest/meta-data', 'http://user:pass@example.com/'):
            self.assertFalse(base.public_url(url))

    @unittest.skipUnless(os.environ.get('AIEO_TEST_BROWSER') == '1', 'Browser smoke test runs in the enrichment workflow after Chromium installation')
    def test_real_browser_renders_javascript_article(self):
        text = json.dumps(BODY)
        html = '<title>'+TITLE+'</title><article></article><script>setTimeout(()=>{document.querySelector("article").innerText='+text+'}, 300)</script>'
        with patch.dict(os.environ, {'AIEO_RENDER_ARTICLES': '1'}):
            result = base.render_fallback({'final_url': URL, '_html': html})
        self.assertEqual(result['outcome'], 'stored', result)
        self.assertIn('Paragraph 7', result['body_text'])


class ResumeTests(unittest.TestCase):
    def test_old_false_blocks_are_rechecked_but_saved_bodies_are_reused(self):
        prior = {'a': 'blocked_bot_challenge'}
        self.assertFalse(runner.should_skip('a', set(), prior, 'retryable', detail={'retrieval_method': 'safe_public_recovery_v4'})[0])
        self.assertTrue(runner.should_skip('a', {'a'}, prior, 'all')[0])

    def test_terminal_results_have_cooldowns_not_permanent_blacklisting(self):
        now = datetime.now(timezone.utc)
        detail = {'retrieval_method': base.RECOVERY_STRATEGY_VERSION, 'attempted_at': now.isoformat()}
        prior = {'a': 'blocked_robots'}
        self.assertTrue(runner.should_skip('a', set(), prior, 'retryable', detail=detail, now=now)[0])
        self.assertFalse(runner.should_skip('a', set(), prior, 'retryable', detail=detail, now=now+timedelta(days=8))[0])

    def test_continuation_pass_does_not_repeat_this_sessions_failures(self):
        detail = {'metadata': {'recovery_session': 'run-1'}}
        self.assertTrue(runner.should_skip('a', set(), {'a': 'http_error'}, 'all', detail=detail, session_id='run-1')[0])
        self.assertFalse(runner.should_skip('a', set(), {'a': 'http_error'}, 'all', detail=detail, session_id='run-2')[0])

    def test_stored_attempt_without_a_saved_snapshot_is_retried(self):
        self.assertFalse(runner.should_skip('a', set(), {'a': 'stored'}, 'retryable', detail={'retrieval_method': base.RECOVERY_STRATEGY_VERSION})[0])

    def test_failed_snapshot_insert_preserves_existing_body(self):
        actions = []
        class Query:
            def select(self, *a): actions.append('select'); return self
            def eq(self, *a): return self
            def execute(self): return SimpleNamespace(data=[{'text_sha256': 'old'}])
            def upsert(self, payload, **kw):
                actions.append('upsert')
                if payload['is_current']: raise AssertionError('Must stage first')
                raise RuntimeError('Database connection dropped')
            def update(self, *a): actions.append('update'); return self
        client = SimpleNamespace(table=lambda *a: Query())
        with self.assertRaises(RuntimeError):
            base.store_snapshot(client, {'article_id': 'a'}, URL, {'body_text': BODY})
        self.assertEqual(actions, ['select', 'upsert'])


class MemoryDatabase:
    """Exercise the real CLI, snapshot promotion, attempt ledger and reports."""
    def __init__(self):
        self.tables = {'articles': [{'article_id': 'a', 'url': URL, 'title': TITLE}, {'article_id': 'b', 'url': URL+'-b', 'title': TITLE}],
            'brief_article_content_snapshots': [{'article_id': 'a', 'body_text': BODY, 'text_sha256': base.sha256_text(BODY), 'is_current': True}],
            'brief_article_fetch_attempts': []}
        self.fail_write = False

    def table(self, name):
        db = self
        class Query:
            def __init__(self):
                self.filters, self.payload, self.mode, self.bounds = [], None, 'select', (0, 10000)
            def select(self, *a): return self
            def order(self, *a, **kw): return self
            def eq(self, key, value): self.filters.append(lambda row: row.get(key) == value); return self
            def in_(self, key, values): self.filters.append(lambda row: row.get(key) in values); return self
            def range(self, lo, hi): self.bounds = (lo, hi+1); return self
            def update(self, payload): self.mode, self.payload = 'update', payload; return self
            def upsert(self, payload, **kw): self.mode, self.payload = 'upsert', payload; return self
            def insert(self, payload): self.mode, self.payload = 'insert', payload; return self
            def execute(self):
                table = db.tables[name]
                matching = [row for row in table if all(f(row) for f in self.filters)]
                if self.mode == 'select':
                    return SimpleNamespace(data=copy.deepcopy(matching[self.bounds[0]:self.bounds[1]]))
                if db.fail_write: raise RuntimeError('Simulated storage failure')
                if self.mode == 'update':
                    for row in matching: row.update(copy.deepcopy(self.payload))
                else:
                    value = copy.deepcopy(self.payload)
                    value.setdefault('attempted_at', datetime.now(timezone.utc).isoformat())
                    if self.mode == 'upsert':
                        table[:] = [r for r in table if (r.get('article_id'), r.get('text_sha256')) != (value.get('article_id'), value.get('text_sha256'))]
                    table.append(value)
                return SimpleNamespace(data=[])
        return Query()


class RecoveryCLITests(unittest.TestCase):
    def run_recovery(self, db):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(temporary)
        release = root/'data/releases/weekly/2026-W36.json'
        release.parent.mkdir(parents=True)
        release.write_text(json.dumps({'units': {'coverage_articles': [{'article_id': 'a'}, {'article_id': 'b'}]}}))
        report = root/'report.json'
        self.enterContext(patch.object(runner, 'ROOT', root))
        self.enterContext(patch.object(runner, 'create_client', return_value=db))
        self.enterContext(patch.dict(os.environ, {'SUPABASE_URL': 'https://unused.example', 'SUPABASE_SECRET_KEY': 'fixture', 'GITHUB_OUTPUT': str(root/'outputs'), 'GITHUB_STEP_SUMMARY': str(root/'summary')}))
        self.enterContext(patch.object(sys, 'argv', ['recover', '--scope', 'release', '--release-id', '2026-W36', '--retry-mode', 'all', '--sleep', '0', '--session-id', 'one', '--report-output', str(report)]))
        self.enterContext(patch('sys.stdout', new=io.StringIO()))
        with patch.object(base, 'fetch_and_extract', return_value={'outcome': 'stored', 'body_text': BODY+' New recovered ending.', 'final_url': URL+'-b', 'extraction_method': 'semantic_html'}) as fetch:
            result = runner.main()
        return result, json.loads(report.read_text()), fetch.call_count

    def test_cli_saves_only_missing_body_and_reports_every_article(self):
        db = MemoryDatabase()
        result, report, fetched = self.run_recovery(db)
        self.assertEqual(result, 0)
        self.assertEqual(fetched, 1)
        self.assertEqual(report['new_bodies_saved'], 1)
        self.assertEqual(report['target_articles_with_full_body'], 2)
        self.assertEqual(report['remaining_unattempted'], 0)
        self.assertEqual(len(report['articles']), 2)
        self.assertNotIn(BODY, json.dumps(report))
        self.assertEqual(db.tables['brief_article_fetch_attempts'][0]['metadata']['recovery_session'], 'one')

    def test_cli_storage_failure_is_red_and_does_not_claim_recovery(self):
        db = MemoryDatabase()
        db.fail_write = True
        result, report, _ = self.run_recovery(db)
        self.assertEqual(result, 1)
        self.assertEqual(report['new_bodies_saved'], 0)
        self.assertEqual(report['target_articles_with_full_body'], 1)
        self.assertEqual(report['counts']['db_error'], 1)

    def test_second_run_reuses_both_saved_bodies_without_fetching(self):
        db = MemoryDatabase()
        self.run_recovery(db)
        result, report, fetched = self.run_recovery(db)
        self.assertEqual(result, 0)
        self.assertEqual(fetched, 0)
        self.assertEqual(report['new_bodies_saved'], 0)


if __name__ == '__main__':
    unittest.main()
