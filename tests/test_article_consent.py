"""Consent regressions; browser cases are mandatory before production recovery."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
if any(importlib.util.find_spec(name) is None for name in ('bs4', 'tldextract')):
    raise unittest.SkipTest('Extraction dependencies are installed in the enrichment workflow')
import article_consent as consent
import article_recovery_support as recovery
from render_public_article import request_kind

URL = 'https://news.example.com/articles/community-research'
BODY = ' '.join(f'Paragraph {i}: The researchers interviewed local teachers and reviewed the complete study records. '
                'The article describes how the new tools affect access to education, with limitations and independent observations.' for i in range(12))


def fixture(label='Accepter & fermer', *, gate=False, shadow=False, iframe=False, delay=0, stuck=False, unknown=False):
    import html
    text = 'La lecture des articles est réservée aux abonné·es' if gate else BODY
    click = '' if stuck else "this.closest('[role=dialog]').remove(); parent.document.querySelector('article').textContent=" + json.dumps(text) + ";"
    dialog = f'''<div role="dialog" aria-label="Cookies"><p>Votre choix pour vos données. Nous utilisons des cookies.</p>
      <button onclick="document.body.dataset.wrong='subscribe'">Refuser &amp; s'abonner</button>
      <button {'id="onetrust-accept-btn-handler"' if unknown else ''} onclick="{html.escape(click, quote=True)}">{html.escape(label)}</button></div>'''
    container = '<div id="host"></div>'
    if iframe:
        setup = 'const f=document.createElement("iframe");f.title="Privacy choices";f.srcdoc=' + json.dumps(dialog) + ';document.getElementById("host").append(f);'
    elif shadow:
        # In shadow cases the publisher's own handler closes its actual dialog.
        setup = 'document.getElementById("host").attachShadow({mode:"open"}).innerHTML=' + json.dumps(dialog) + ';'
    else:
        setup = 'document.getElementById("host").innerHTML=' + json.dumps(dialog) + ';'
    return '<!doctype html><html lang="fr"><head><title>Community research</title></head><body><h1>Community research</h1><article></article>' + container + '<script>setTimeout(()=>{' + setup + '},' + str(delay) + ');</script></body></html>'


class ConsentPolicyTests(unittest.TestCase):
    def test_examples_and_multilingual_acceptance(self):
        for label in ['ACCEPTER & FERMER', 'Accepter', 'I Accept', 'Accept Essential', 'J’accepte',
                      'Alle akzeptieren', 'Aceptar todas las cookies', 'Aceitar tudo', '同意',
                      '接受所有 Cookies', 'すべて同意する', '모두 동의', 'قبول الكل', 'ยอมรับทั้งหมด',
                      'स्वीकार करें', 'Tümünü kabul et', 'Chấp nhận tất cả', 'Akceptuję']:
            with self.subTest(label=label):
                self.assertIsNotNone(consent.acceptance_method(label))

    def test_language_independent_cmp_control(self):
        self.assertEqual(consent.acceptance_method('Unlisted localized consent label', known=True), 'cmp_control')

    def test_subscription_login_notifications_and_ambiguous_buttons_are_not_consent(self):
        for label in ['Accept and subscribe', "REFUSER & S’ABONNER", 'I accept payment',
                      'Agree to notifications', 'Sign in with Google', '登录', 'Continue', 'OK', 'Enable', 'Read more']:
            with self.subTest(label=label):
                self.assertIsNone(consent.acceptance_method(label))
        self.assertIsNone(consent.acceptance_method('Accept and subscribe', known=True))

    def test_static_dialog_and_cmp_bootstrap_require_browser(self):
        for html in ['<div role="dialog">Cookies<button>Accepter</button></div>',
                     '<script src="https://cdn.cookielaw.org/consent/abc.js"></script>',
                     '<iframe src="https://cdn.privacy-mgmt.com/consent"></iframe>']:
            self.assertTrue(consent.needs_consent_browser(html))

    def test_hidden_templates_and_unrelated_accept_do_not_require_consent(self):
        for html in ['<article>How cookies work<button>Accept</button></article>',
                     '<template><div role="dialog">Cookies<button>Accept</button></div></template>',
                     '<div hidden><button id="onetrust-accept-btn-handler">Accept</button></div>',
                     '<div style="display:none"><div style="display:none">Cookies<button>Accept</button></div></div>']:
            self.assertFalse(consent.needs_consent_browser(html))

    def test_consent_provider_domain_boundary(self):
        self.assertTrue(consent.cmp_url('https://cdn.privacy-mgmt.com/consent'))
        for url in ['https://privacy-mgmt.com.attacker.example/consent', 'https://attackerprivacy-mgmt.com/consent',
                    'https://user@privacy-mgmt.com/consent', 'javascript:accept()']:
            self.assertFalse(consent.cmp_url(url))

    def test_request_scope_allows_consent_frames_and_click_submissions(self):
        cmp = 'https://cdn.privacy-mgmt.com/consent'
        self.assertEqual(request_kind(cmp, URL, 'GET', 'document', child_frame=True), 'consent')
        self.assertEqual(request_kind(cmp, URL, 'POST', 'fetch'), 'deny')
        self.assertEqual(request_kind('https://cdn.privacy-mgmt.com/wrapper/v2/message', URL, 'POST', 'fetch'), 'consent')
        self.assertEqual(request_kind(cmp, URL, 'POST', 'fetch', consent_started=True), 'consent')
        self.assertEqual(request_kind('https://news.example.com/api/consent', URL, 'POST', 'xhr', consent_started=True), 'consent')
        self.assertEqual(request_kind(URL, URL, 'GET', 'document', consent_started=True), 'article')

    def test_acceptance_never_authorizes_login_or_unchecked_article_origins(self):
        for url, method, kind, child in [
            ('https://news.example.com/login', 'POST', 'fetch', False),
            ('https://news.example.com/subscribe', 'GET', 'document', False),
            ('https://other.example.net/article', 'GET', 'fetch', False),
            ('https://other.example.net/login', 'GET', 'document', True),
            ('https://news.example.com/different-article', 'GET', 'document', False),
        ]:
            self.assertEqual(request_kind(url, URL, method, kind, child_frame=child, consent_started=True), 'deny')
        self.assertEqual(request_kind('https://news.example.com/api/article', URL, 'GET', 'fetch', consent_started=True), 'article')

    def test_mediapart_subscriber_preview_remains_excluded(self):
        self.assertTrue(recovery.paywall('<article>La lecture des articles est réservée aux abonné·es</article>', URL))


@unittest.skipUnless(importlib.util.find_spec('trafilatura') and importlib.util.find_spec('supabase'), 'Extraction dependencies required')
class AcquisitionConsentTests(unittest.TestCase):
    def setUp(self):
        import brief_backfill_article_content as base
        self.base = base

    def test_consent_prose_cannot_become_article_body(self):
        html = '<div role="dialog"><p>Cookies ' + BODY + '</p><button>Accepter</button></div>'
        self.assertEqual(self.base.extract_html_result(html, {'final_url': URL})['outcome'], 'consent_required')

    def test_original_banner_render_precedes_alternatives(self):
        primary = {'outcome': 'consent_required', 'final_url': URL, '_html': '<p>Cookie dialog</p>'}
        rendered = {'outcome': 'stored', 'final_url': URL, 'body_text': BODY, 'consent': {'status': 'accepted'}}
        with patch.object(self.base, 'fetch_public_candidate', return_value=primary), patch.object(self.base, 'render_fallback', return_value=rendered), patch.object(self.base, 'public_alternate_urls') as alt:
            result = self.base.fetch_and_extract(URL)
        self.assertEqual(result['body_text'], BODY)
        self.assertEqual(result['recovery_trace'][-1]['consent']['status'], 'accepted')
        alt.assert_not_called()

    def test_browser_acceptance_still_runs_article_paywall_checks(self):
        html = '<article>' + BODY + '</article><p>La lecture des articles est réservée aux abonné·es</p>'
        result = self.base.extract_html_result(html, {'final_url': URL}, consent_checked=True)
        self.assertEqual(result['outcome'], 'blocked_paywall_or_login')
        self.assertNotIn('body_text', result)

    def test_unresolved_consent_is_an_explicit_failure_not_stored(self):
        primary = {'outcome': 'consent_required', 'final_url': URL, '_html': '<p>Cookies</p>'}
        proc = unittest.mock.MagicMock()
        proc.communicate.return_value = (json.dumps({'html': '<article>' + BODY + '</article>', 'final_url': URL,
            'consent': {'status': 'unresolved', 'click_count': 1}}), '')
        proc.poll.return_value = 0
        with patch.dict(os.environ, {'AIEO_RENDER_ARTICLES': '1'}), patch.object(self.base.subprocess, 'Popen', return_value=proc):
            result = self.base.render_fallback(primary)
        self.assertEqual(result['outcome'], 'consent_unresolved')
        self.assertNotIn('body_text', result)

    def test_post_consent_reservation_still_blocks_collection(self):
        proc = unittest.mock.MagicMock()
        proc.poll.return_value = 0
        proc.communicate.return_value = (json.dumps({'html': '<meta name="tdm-reservation" content="1"><article>' + BODY + '</article>',
            'final_url': URL, 'consent': {'status': 'accepted'}}), '')
        with patch.dict(os.environ, {'AIEO_RENDER_ARTICLES': '1'}), patch.object(self.base.subprocess, 'Popen', return_value=proc):
            result = self.base.render_fallback({'final_url': URL, '_html': '<div>Cookies</div>'})
        self.assertEqual(result['outcome'], 'blocked_tdm_reserved')


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'Browser driver installed in enrichment workflow')
class ConsentLoopTests(unittest.TestCase):
    def drive(self, *, stuck=False, label='Accepter', stages=1, budget=3):
        clock = [0.0]
        state = {'remaining': stages, 'clicks': 0}
        page = unittest.mock.MagicMock()
        frame = page.main_frame
        page.frames = [frame]
        row = {'index': 0, 'label': label, 'known': False, 'visible': True, 'context': True, 'disabled': False}
        def rows(*args):
            return [dict(row, label=row['label'] + (' ' * state['clicks']))] if state['remaining'] else []
        frame.locator.return_value.evaluate_all.side_effect = rows
        control = frame.locator.return_value.nth.return_value
        control.evaluate_all.side_effect = rows
        def click(**kwargs):
            state['clicks'] += 1
            if not stuck:
                state['remaining'] -= 1
        control.click.side_effect = click
        page.wait_for_timeout.side_effect = lambda ms: clock.__setitem__(0, clock[0]+ms/1000)
        with patch.object(consent.time, 'monotonic', side_effect=lambda: clock[0]):
            result = consent.accept_cookie_consent(page, timeout_ms=1500, initial_wait_ms=100,
                settled_ms=100, max_clicks=budget)
        return result, state

    def test_real_loop_accepts_then_requires_dialog_absence(self):
        result, state = self.drive()
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(state['clicks'], 1)

    def test_unknown_label_stays_unresolved(self):
        result, state = self.drive(label='Continue')
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(state['clicks'], 0)

    def test_multi_stage_dialogs_obey_shared_click_budget(self):
        result, state = self.drive(stages=4, budget=2)
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(state['clicks'], 2)

    def test_stuck_dialog_cannot_become_complete(self):
        result, state = self.drive(stuck=True, budget=1)
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(state['clicks'], 1)


@unittest.skipUnless(os.environ.get('AIEO_TEST_BROWSER') == '1', 'Enabled in enrichment workflow after Chromium installation')
class ConsentBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.runtime = sync_playwright().start()
        cls.browser = cls.runtime.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def run_fixture(self, **kwargs):
        context = self.browser.new_context()
        page = context.new_page()
        page.set_content(fixture(**kwargs))
        result = consent.accept_cookie_consent(page, timeout_ms=2200, initial_wait_ms=500, settled_ms=200)
        body = page.locator('article').inner_text()
        wrong = page.locator('body').get_attribute('data-wrong')
        context.close()
        self.assertIsNone(wrong)
        return result, body

    def test_real_clicks_for_languages_and_delayed_dialogs(self):
        for label in ['Accepter & fermer', 'I Accept', 'Alle akzeptieren', '全部同意', 'قبول الكل']:
            with self.subTest(label=label):
                state, body = self.run_fixture(label=label, delay=250)
                self.assertEqual(state['status'], 'accepted')
                self.assertEqual(state['click_count'], 1)
                self.assertEqual(body, BODY)

    def test_iframe_consent_click(self):
        state, body = self.run_fixture(iframe=True)
        self.assertEqual(state['status'], 'accepted')
        self.assertEqual(state['actions'][0]['context'], 'frame')
        self.assertEqual(body, BODY)

    def test_shadow_root_and_language_independent_control(self):
        state, body = self.run_fixture(shadow=True, unknown=True, label='Localized CMP acceptance')
        self.assertEqual(state['actions'][0]['method'], 'cmp_control')
        self.assertEqual(body, BODY)

    def test_stuck_banner_not_clicked_repeatedly_or_removed(self):
        state, body = self.run_fixture(stuck=True)
        self.assertEqual(state['status'], 'unresolved')
        self.assertEqual(state['click_count'], 1)
        self.assertEqual(body, '')

    def test_subscriber_preview_after_consent(self):
        state, body = self.run_fixture(gate=True)
        self.assertEqual(state['status'], 'accepted')
        self.assertTrue(recovery.paywall('<article>' + body + '</article>', URL))

    def test_real_renderer_and_extractor_together(self):
        import brief_backfill_article_content as base
        for gate, expected in [(False, 'stored'), (True, 'blocked_paywall_or_login')]:
            with self.subTest(gate=gate):
                # Inline fixtures make no external requests and use the exact renderer.
                with patch.dict(os.environ, {'AIEO_RENDER_ARTICLES': '1'}):
                    result = base.render_fallback({'final_url': URL, '_html': fixture(gate=gate)})
                self.assertEqual(result['consent']['status'], 'accepted')
                self.assertEqual(result['outcome'], expected)


if __name__ == '__main__':
    unittest.main()
