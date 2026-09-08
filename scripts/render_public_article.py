#!/usr/bin/env python3
"""Isolated, bounded browser fallback for an already accessible public page.

The initial HTML is the exact checked response. Never solve challenges, click
consent/login controls, use credentials, or fetch a protected alternative.
"""
from __future__ import annotations

import json
import sys


def render(payload):
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    import brief_backfill_article_content as base
    from article_recovery_support import same_publisher

    url, html = payload["url"], payload["html"]
    blocked = []
    calls = 0
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
                headers = {k: v for k, v in payload.get("headers", {}).items() if k.lower() in {"content-security-policy", "referrer-policy", "x-content-type-options"}}
                headers["content-type"] = "text/html; charset=utf-8"
                route.fulfill(status=200, headers=headers, body=html)
                return
            if request.method != "GET" or request.resource_type in {"image", "media", "font", "websocket", "manifest"}:
                route.abort()
                return
            if not base.public_url(request.url):
                route.abort()
                return
            # Only the original publisher may supply article documents/data.
            if request.resource_type in {"document", "xhr", "fetch"}:
                if not same_publisher(request.url, url):
                    route.abort()
                    return
                if request.resource_type == "document":
                    blocked.append("unexpected_navigation")
                    route.abort()
                    return
                calls += 1
                if calls > 12:
                    route.abort()
                    return
                robots, _, _ = base.robots_allowed(request.url)
                tdm = base.tdmrep_for(request.url)
                if robots is not True or tdm.get("reservation") == "1" or tdm.get("check_state") in {"unavailable", "invalid"}:
                    blocked.append("publisher_data_unavailable")
                    route.abort()
                    return
                response = route.fetch(timeout=12000, max_redirects=0)
                if response.status != 200 or response.headers.get("tdm-reservation", "").strip() == "1":
                    blocked.append("publisher_data_unavailable")
                    route.abort()
                    return
                route.fulfill(response=response)
                return
            route.continue_()

        context.route("**/*", guard)
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
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
        output = page.content()
        final_url = page.url
        context.close()
        browser.close()
        if len(output.encode("utf-8")) > base.MAX_RESPONSE_BYTES:
            raise ValueError("Rendered HTML exceeds response size limit")
        return {"html": output, "final_url": final_url, "blocked_requests": sorted(set(blocked))}


if __name__ == "__main__":
    try:
        result = render(json.load(sys.stdin))
    except Exception as exc:
        result = {"error_class": type(exc).__name__, "error_message": str(exc)[:240]}
    print(json.dumps(result, ensure_ascii=False))
