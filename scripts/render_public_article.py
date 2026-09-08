#!/usr/bin/env python3
"""Isolated, bounded browser fallback for an already accessible public page.

The initial HTML is the exact checked response. Cookie acceptance is explicitly
authorized by the owner. Credentials, challenges and protected alternatives are
never used. Browser cookies live only for this source's isolated context.
"""
from __future__ import annotations

import json
import re
import sys
from urllib.parse import urlsplit

from article_consent import accept_cookie_consent, cmp_url, consent_endpoint
from article_recovery_support import same_publisher, url_identity


def request_kind(url, original, method, resource_type, *, child_frame=False, consent_started=False):
    """Classify allowed traffic; consent providers cannot supply article prose."""
    if resource_type in {"image", "media", "font", "websocket", "manifest"}:
        return "deny"
    publisher = same_publisher(url, original)
    consent = cmp_url(url) or (publisher and consent_endpoint(url))
    if method == "POST":
        bootstrap = cmp_url(url) and bool(re.search(r"/(?:messages?|config(?:uration)?|notices?)(?:/|$)", urlsplit(url).path, re.I))
        return "consent" if consent and (consent_started or bootstrap) and resource_type in {"xhr", "fetch"} else "deny"
    if method != "GET":
        return "deny"
    if resource_type == "document":
        if child_frame:
            return "consent" if consent else "deny"
        return "article" if consent_started and url_identity(url) == url_identity(original) else "deny"
    if resource_type in {"xhr", "fetch"}:
        return "consent" if consent else ("article" if publisher else "deny")
    return "asset"


def render(payload):
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    import brief_backfill_article_content as base
    url, html = payload["url"], payload["html"]
    blocked = []
    consent_failures = []
    calls = 0
    consent_started = False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=base.USER_AGENT, service_workers="block", accept_downloads=False)
        page = context.new_page()
        initial = True

        def guard(route):
            nonlocal initial, calls
            request = route.request
            if initial and request.url == url and request.resource_type == "document":
                initial = False
                headers = {k: v for k, v in payload.get("headers", {}).items() if k.lower() in {"content-security-policy", "referrer-policy", "x-content-type-options", "set-cookie"}}
                headers["content-type"] = "text/html; charset=utf-8"
                route.fulfill(status=200, headers=headers, body=html)
                return
            if not base.public_url(request.url):
                route.abort()
                return
            kind = request_kind(request.url, url, request.method, request.resource_type,
                child_frame=request.frame != page.main_frame, consent_started=consent_started)
            if kind == "deny":
                if request.resource_type == "document" and request.frame == page.main_frame:
                    blocked.append("unexpected_navigation")
                route.abort()
                return
            if kind in {"article", "consent"}:
                calls += 1
                if calls > 32:
                    (blocked if kind == "article" else consent_failures).append("request_budget_exceeded")
                    route.abort()
                    return
                # Consent UI is ancillary. Every article/data request still
                # receives the original robots/TDM checks, even after acceptance.
                if kind == "article":
                    robots, _, _ = base.robots_allowed(request.url)
                    tdm = base.tdmrep_for(request.url)
                    if robots is not True or tdm.get("reservation") == "1" or tdm.get("check_state") in {"unavailable", "invalid"}:
                        blocked.append("publisher_data_unavailable")
                        route.abort()
                        return
                try:
                    # max_redirects=0 prevents unchecked redirects to other
                    # origins, credentials or protected article endpoints.
                    response = route.fetch(timeout=8000, max_redirects=0)
                    valid = (200 <= response.status < 300) if kind == "consent" else response.status == 200
                    if not valid or (kind == "article" and response.headers.get("tdm-reservation", "").strip() == "1"):
                        raise ValueError("unavailable response")
                    if len(response.body()) > base.MAX_RESPONSE_BYTES:
                        raise ValueError("response size limit")
                    route.fulfill(response=response)
                except Exception:
                    (blocked if kind == "article" else consent_failures).append(kind + "_request_unavailable")
                    route.abort()
                return
            route.continue_()

        context.route("**/*", guard)
        # No popup can turn a consent click into a second signed-in/payment page.
        context.on("page", lambda popup: popup.close() if popup != page else None)
        page.goto(url, wait_until="domcontentloaded", timeout=25000)

        def authorize_consent(_frame):
            nonlocal consent_started
            consent_started = True

        consent = accept_cookie_consent(page, before_click=authorize_consent)
        if consent['status'] == 'unresolved':
            # Do not repeatedly click a stuck dialog in the later render phase.
            output, final_url = page.content(), page.url
            context.close()
            browser.close()
            return {"html": output, "final_url": final_url, "blocked_requests": sorted(set(blocked)),
                    "consent": {**consent, "request_failures": sorted(set(consent_failures))}}
        try:
            page.wait_for_function("""() => {
              const n = document.querySelector('[itemprop="articleBody"], .article-body, .story-body, .entry-content, .field--name-body, article, main');
              return n && n.innerText.length >= 650;
            }""", timeout=12000)
        except BrowserTimeout:
            pass
        # Wait for a stable article length, with a hard 6-second bound.
        last, stable = -1, 0
        for _ in range(6):
            page.wait_for_timeout(1000)
            length = page.locator("body").inner_text(timeout=2000)
            stable = stable + 1 if len(length) == last else 0
            last = len(length)
            if stable >= 2:
                break
        # Late-loading/multi-stage banners get one final bounded inspection.
        final_consent = accept_cookie_consent(page, timeout_ms=3000, initial_wait_ms=0,
            before_click=authorize_consent, max_clicks=max(0, 3-consent['click_count']))
        consent['actions'] += final_consent['actions']
        consent['click_count'] += final_consent['click_count']
        consent['detected'] = consent['detected'] or final_consent['detected']
        consent['status'] = ('unresolved' if final_consent['status'] == 'unresolved'
            else 'accepted' if consent['click_count'] else 'not_present')
        if consent_failures and consent['status'] == 'not_present':
            consent['status'] = 'unresolved'
        if final_consent['click_count']:
            # A late dialog can trigger another delayed article render.
            try:
                page.wait_for_function("""() => {
                    const n=document.querySelector('[itemprop="articleBody"], .article-body, article');
                    return n && n.innerText.length >= 650;
                }""", timeout=6000)
                page.wait_for_timeout(1500)
            except BrowserTimeout:
                pass
        output = page.content()
        final_url = page.url
        context.close()
        browser.close()
        if len(output.encode("utf-8")) > base.MAX_RESPONSE_BYTES:
            raise ValueError("Rendered HTML exceeds response size limit")
        return {"html": output, "final_url": final_url, "blocked_requests": sorted(set(blocked)),
                "consent": {**consent, "request_failures": sorted(set(consent_failures))}}


if __name__ == "__main__":
    try:
        result = render(json.load(sys.stdin))
    except Exception as exc:
        result = {"error_class": type(exc).__name__, "error_message": str(exc)[:240]}
    print(json.dumps(result, ensure_ascii=False))
