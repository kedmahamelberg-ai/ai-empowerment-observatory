import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';

const code = readFileSync(new URL('../analytics.js', import.meta.url), 'utf8');
const site = 'https://observatory.hamelberg-ai.com';

function openPage({ url = site + '/edu/', referrer = '', hidden = false, userAgent = 'Chrome' } = {}) {
  const requests = [];
  const listeners = new Map();
  const scripts = [];
  const window = {addEventListener: (name, callback) => listeners.set(name, callback)};
  const document = {
    referrer,
    visibilityState: hidden ? 'hidden' : 'visible',
    head: { appendChild: script => scripts.push(script) },
    createElement: () => ({}),
    addEventListener: (event, callback) => listeners.set(event, callback),
    removeEventListener: event => listeners.delete(event)
  };
  runInNewContext(code, {
    window, document, URL, URLSearchParams, location: new URL(url), navigator: { userAgent },
    fetch: (url, options) => {
      requests.push({ url, options });
      return Promise.resolve();
    }
  });
  return {
    requests, scripts, window, document, restore: persisted => listeners.get('pageshow')({persisted}),
    show() {
      document.visibilityState = 'visible';
      listeners.get('visibilitychange')?.();
    }
  };
}

test('counts each visible page once and classifies only permitted source categories', () => {
  const arrivals = [
    ['https://www.google.co.uk/search?q=private', '', 'google'],
    ['https://chatgpt.com/c/private', '', 'chatgpt'],
    ['https://claude.ai/chat/private', '', 'claude'],
    ['', '?utm_source=ChatGPT&utm_campaign=hidden', 'chatgpt'],
    ['', '?utm_source=claude', 'claude'],
    ['', '?utm_source=google', 'google'],
    ['', '?utm_source=someone%40private.org', 'direct_or_unknown'],
    ['https://other.example/private', '', 'other_referral'],
    [site + '/report/?private', '?utm_source=google', 'internal']
  ];
  for (const [referrer, search, source] of arrivals) {
    const page = openPage({ url: site + '/edu/index.html' + search, referrer, hidden: true });
    assert.equal(page.requests.length, 0);
    page.show();
    page.show();
    assert.equal(page.requests.length, 1);
    const { url, options } = page.requests[0];
    assert.match(url, /\/observatory_record_page_view$/);
    assert.equal(options.credentials, 'omit');
    assert.equal(options.referrerPolicy, 'no-referrer');
    assert.deepEqual(JSON.parse(options.body), { p_path: '/edu/', p_source: source });
  }
});

test('does not record preview, crawler, or unknown paths', () => {
  assert.equal(openPage({ url: 'https://preview.example/edu/' }).requests.length, 0);
  assert.equal(openPage({ userAgent: 'Claude-User' }).requests.length, 0);
  assert.equal(openPage({ url: site + '/private/' }).requests.length, 0);
});

test('Google storage is denied before configuration and custom events omit URLs', () => {
  const page = openPage({ url: site + '/report/?utm_source=google&name=private', referrer: 'https://www.google.com/search?q=private' });
  const calls = page.window.dataLayer.map(args => Array.from(args));
  assert.equal(calls[0][0], 'consent');
  assert.equal(calls[0][2].analytics_storage, 'denied');
  assert.equal(calls[0][2].ad_storage, 'denied');
  assert.equal(calls[2][2].send_page_view, false);
  assert.equal(calls[3][1], 'page_view');
  assert.equal(calls[3][2].page_location, site + '/report/');
  assert.equal(calls[3][2].page_referrer, '');
  assert.equal(page.scripts[0].referrerPolicy, 'no-referrer');
  page.window.AIEOAnalytics.trackEvent('quarterly_report_download', { report_id: '2026-q3', link_url: 'https://other.example/private' });
  page.window.AIEOAnalytics.trackEvent('newsletter_signup_click', { email: 'private@example.com' });
  assert.doesNotMatch(JSON.stringify(page.window.dataLayer), /private|utm_source|link_url/);
});

test('counts a cached return once in both systems using the existing tag', () => {
  const page = openPage({referrer: 'https://www.google.nl/'});
  page.restore(false);
  page.document.visibilityState = 'hidden';
  page.restore(true);
  assert.equal(page.requests.length, 1);
  page.show();
  page.show();
  assert.deepEqual(page.requests.map(r => JSON.parse(r.options.body).p_source), ['google', 'direct_or_unknown']);
  assert.equal(page.window.dataLayer.filter(call => call[1] === 'page_view').length, 2);
  assert.equal(page.scripts.length, 1);
});
