#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
import trafilatura
from bs4 import BeautifulSoup
from supabase import create_client

from brief_content_common import article_url, domain_of, normalize_space, sha256_text

USER_AGENT = "AIEOResearchBot/1.2 (+https://observatory.hamelberg-ai.com/methodology/)"
ROBOTS_TIMEOUT = (5, 10)
TDM_TIMEOUT = (5, 10)
ARTICLE_TIMEOUT = (10, 30)
MIN_WORDS = 80
FETCH_RETRY_ATTEMPTS = 3
MAX_ALTERNATE_URLS = 3
MAX_REDIRECTS = 4
RECOVERY_STRATEGY_VERSION = "safe_public_recovery_v2"
TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


def _request_headers(*, accept: str) -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "Accept-Language": "en;q=0.9,*;q=0.5",
    }


def compact_error(error: Any, limit: int = 280) -> str:
    """Keep useful diagnostics without persisting response or body content."""
    return re.sub(r"\s+", " ", str(error or "")).strip()[:limit]


def retry_delay_seconds(response: requests.Response | None, attempt: int) -> float:
    """Respect a modest Retry-After value and otherwise use bounded backoff."""
    retry_after = ""
    if response is not None:
        retry_after = str(response.headers.get("Retry-After") or "").strip()
    try:
        return max(0.0, min(8.0, float(retry_after)))
    except (TypeError, ValueError):
        return min(4.0, 0.8 * (2 ** max(0, attempt - 1)))


def public_get(
    url: str,
    *,
    timeout: tuple[int, int],
    accept: str,
    max_attempts: int = FETCH_RETRY_ATTEMPTS,
    allow_redirects: bool = False,
) -> tuple[requests.Response | None, list[dict[str, Any]], dict[str, str] | None]:
    """Fetch a public URL with bounded retries for transient failures only.

    This does not rotate identities, solve challenges, use a proxy, or retry a
    publisher's explicit access decision. It is solely for ordinary temporary
    transport failures and the HTTP statuses publishers label as retryable.
    """
    history: list[dict[str, Any]] = []
    last_error: dict[str, str] | None = None
    for attempt in range(1, max(1, max_attempts) + 1):
        started = time.monotonic()
        response: requests.Response | None = None
        try:
            response = requests.get(
                url,
                headers=_request_headers(accept=accept),
                timeout=timeout,
                allow_redirects=allow_redirects,
            )
            elapsed_ms = round((time.monotonic() - started) * 1000)
            history.append(
                {
                    "attempt": attempt,
                    "http_status": response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "final_url": str(response.url or url),
                }
            )
            if response.status_code not in TRANSIENT_STATUS_CODES or attempt >= max_attempts:
                return response, history, None
            time.sleep(retry_delay_seconds(response, attempt))
        except requests.RequestException as exc:
            elapsed_ms = round((time.monotonic() - started) * 1000)
            last_error = {
                "error_class": type(exc).__name__,
                "error_message": compact_error(exc),
            }
            history.append(
                {
                    "attempt": attempt,
                    "error_class": last_error["error_class"],
                    "error_message": last_error["error_message"],
                    "elapsed_ms": elapsed_ms,
                }
            )
            if attempt < max_attempts:
                time.sleep(retry_delay_seconds(None, attempt))
    return None, history, last_error


def robots_allowed(url: str) -> tuple[bool | None, str, dict[str, Any]]:
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    response, history, error = public_get(
        robots_url,
        timeout=ROBOTS_TIMEOUT,
        accept="text/plain,text/*;q=0.9,*/*;q=0.1",
        allow_redirects=True,
    )
    detail: dict[str, Any] = {"request_attempts": history}
    if error:
        detail.update(error)
        return None, robots_url, detail
    if response is None:
        return None, robots_url, detail
    detail["http_status"] = response.status_code
    if response.status_code in {401, 403}:
        return False, robots_url, detail
    if response.status_code != 200:
        return None, robots_url, detail
    try:
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.parse(response.text.splitlines())
        return bool(rp.can_fetch(USER_AGENT, url)), robots_url, detail
    except Exception as exc:
        detail.update(
            {
                "error_class": type(exc).__name__,
                "error_message": compact_error(exc),
            }
        )
        return None, robots_url, detail

def tdmrep_for(url: str) -> dict:
    parts = urlsplit(url)
    endpoint = f"{parts.scheme}://{parts.netloc}/.well-known/tdmrep.json"
    out: dict[str, Any] = {
        "url": endpoint,
        "reservation": "unset",
        "policy": None,
        "check_state": "absent_or_unset",
    }
    response, history, error = public_get(
        endpoint,
        timeout=TDM_TIMEOUT,
        accept="application/json,text/plain;q=0.9,*/*;q=0.1",
        allow_redirects=True,
    )
    out["request_attempts"] = history
    if error:
        out.update(error)
        out["check_state"] = "unavailable"
        return out
    if response is None:
        out["check_state"] = "unavailable"
        return out
    out["http_status"] = response.status_code
    # 404 and 410 mean no TDMRep resource is published. A 401/403 or a server
    # error is not treated as permission to collect the page.
    if response.status_code in {404, 410}:
        return out
    if response.status_code != 200:
        out["check_state"] = "unavailable"
        return out
    try:
        rules = response.json()
    except (TypeError, ValueError, requests.RequestException) as exc:
        out.update(
            {
                "check_state": "unavailable",
                "error_class": type(exc).__name__,
                "error_message": compact_error(exc),
            }
        )
        return out
    if not isinstance(rules, list):
        out["check_state"] = "invalid"
        return out
    path = parts.path or "/"
    matches = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        loc = str(rule.get("location") or "")
        if not loc:
            continue
        prefix = loc.rstrip("*")
        if path.startswith(prefix):
            matches.append((len(prefix), rule))
    if matches:
        _, rule = sorted(matches, reverse=True, key=lambda x: x[0])[0]
        value = rule.get("tdm-reservation")
        out["reservation"] = str(value) if value is not None else "unset"
        out["policy"] = rule.get("tdm-policy")
    out["check_state"] = "checked"
    return out

def html_tdm_signal(response: requests.Response, html: str) -> tuple[str, str | None]:
    headers = {k.casefold(): v for k, v in response.headers.items()}
    reservation = headers.get("tdm-reservation")
    policy = headers.get("tdm-policy")
    if reservation is not None:
        return str(reservation).strip(), policy
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find("meta", attrs={"name": re.compile(r"^tdm-reservation$", re.I)})
    if node and node.get("content") is not None:
        reservation = str(node.get("content")).strip()
    pnode = soup.find("meta", attrs={"name": re.compile(r"^tdm-policy$", re.I)})
    if pnode and pnode.get("content"):
        policy = str(pnode.get("content")).strip()
    return reservation or "unset", policy

def detect_paywall(html: str) -> bool:
    lower = html.casefold()
    markers = [
        '"isaccessibleforfree":false',
        '"isaccessibleforfree": false',
        'meteredcontent',
        'subscriptionrequired',
        'subscribe to continue',
        'sign in to continue',
        'already a subscriber',
    ]
    return any(m in lower for m in markers)


def detect_access_challenge(html: str) -> bool:
    """Recognize a challenge page so the audit explains the real blocker.

    The collector does not attempt to solve a CAPTCHA or evade a bot-control
    service.  Naming the response prevents repeated retries from being
    mistaken for an extraction failure.
    """
    lower = html.casefold()
    markers = [
        "cf-chl-",
        "challenge-platform",
        "just a moment...",
        "verify you are human",
        "captcha",
        "access denied by security policy",
    ]
    return any(marker in lower for marker in markers)

def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))

def clean_text(value: str) -> str:
    lines = [normalize_space(x) for x in (value or "").splitlines()]
    lines = [x for x in lines if x]
    # Collapse exact repeated adjacent lines.
    out = []
    for line in lines:
        if not out or out[-1] != line:
            out.append(line)
    return "\n\n".join(out).strip()

def extract_trafilatura(html: str, favor_precision: bool) -> str:
    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        include_links=False,
        favor_precision=favor_precision,
        favor_recall=not favor_precision,
        output_format="txt",
    ) or ""
    return clean_text(text)

def iter_jsonld_objects(data):
    if isinstance(data, dict):
        yield data
        graph = data.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from iter_jsonld_objects(item)
    elif isinstance(data, list):
        for item in data:
            yield from iter_jsonld_objects(item)


def iter_embedded_objects(data: Any, *, depth: int = 0):
    """Walk public embedded JSON with a strict depth limit.

    Some newspaper pages render the article after JavaScript starts but still
    expose the article payload in a public Next.js, Nuxt, or application/json
    script. Reading that public payload is a static extraction fallback, not a
    browser automation or an access-control bypass.
    """
    if depth > 12:
        return
    if isinstance(data, dict):
        yield data
        for value in data.values():
            if isinstance(value, (dict, list)):
                yield from iter_embedded_objects(value, depth=depth + 1)
    elif isinstance(data, list):
        for value in data[:300]:
            if isinstance(value, (dict, list)):
                yield from iter_embedded_objects(value, depth=depth + 1)

def extract_jsonld_article_body(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for node in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = node.string or node.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for obj in iter_jsonld_objects(data):
            body = obj.get("articleBody")
            if isinstance(body, str):
                body = clean_text(body)
                if word_count(body) >= MIN_WORDS:
                    candidates.append(body)
    return max(candidates, key=word_count, default="")


def extract_embedded_json_article_body(html: str) -> str:
    """Extract a public article body embedded in application JSON safely."""
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []
    body_keys = {
        "articlebody",
        "body",
        "bodytext",
        "content",
        "contenttext",
        "text",
        "renderedbody",
        "articlecontent",
    }
    for node in soup.find_all("script"):
        node_id = str(node.get("id") or "").casefold()
        node_type = str(node.get("type") or "").casefold()
        if node_id != "__next_data__" and "json" not in node_type:
            continue
        raw = node.string or node.get_text()
        if not raw or len(raw) > 5_000_000:
            continue
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        for obj in iter_embedded_objects(data):
            for key, value in obj.items():
                if str(key).casefold().replace("_", "") not in body_keys:
                    continue
                if not isinstance(value, str):
                    continue
                text = clean_text(value)
                if word_count(text) >= MIN_WORDS:
                    candidates.append(text)
    return max(candidates, key=word_count, default="")

def extract_semantic_article(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        tag.decompose()

    selectors = [
        "article",
        "main article",
        "[itemprop='articleBody']",
        ".article-body",
        ".article-content",
        ".story-body",
        ".story-content",
        ".entry-content",
        ".post-content",
        "main",
    ]
    candidates = []
    for selector in selectors:
        for node in soup.select(selector):
            text = clean_text(node.get_text("\n", strip=True))
            n = word_count(text)
            if n >= MIN_WORDS:
                candidates.append((n, text))
    if not candidates:
        return ""
    candidates.sort(reverse=True, key=lambda x: x[0])
    return candidates[0][1]

def extraction_quality(text: str, method: str) -> float:
    n = word_count(text)
    if n < MIN_WORDS:
        return 0.0
    score = 0.55
    if n >= 250: score += 0.10
    if n >= 600: score += 0.10
    if n >= 1200: score += 0.05
    if method in {"trafilatura_precision", "jsonld_articleBody"}: score += 0.10
    # Penalise extremely short average token shape / likely nav fragments only mildly.
    alpha = sum(ch.isalpha() for ch in text)
    if text and alpha / max(1, len(text)) > 0.55:
        score += 0.05
    return round(min(score, 1.0), 3)

def choose_best_extraction(html: str):
    attempts = []
    for method, fn in [
        ("trafilatura_precision", lambda: extract_trafilatura(html, True)),
        ("jsonld_articleBody", lambda: extract_jsonld_article_body(html)),
        ("embedded_json_article_body", lambda: extract_embedded_json_article_body(html)),
        ("trafilatura_recall", lambda: extract_trafilatura(html, False)),
        ("semantic_html", lambda: extract_semantic_article(html)),
    ]:
        try:
            text = fn()
        except Exception:
            text = ""
        n = word_count(text)
        if n >= MIN_WORDS:
            q = extraction_quality(text, method)
            attempts.append((q, n, method, text))
    if not attempts:
        return {"text": "", "method": None, "word_count": 0, "quality": 0.0}
    # Quality first, then length. Prevent giant boilerplate from winning just by length.
    attempts.sort(reverse=True, key=lambda x: (x[0], min(x[1], 2500)))
    q, n, method, text = attempts[0]
    return {"text": text, "method": method, "word_count": n, "quality": q}


def same_origin_url(candidate: str, base_url: str) -> bool:
    try:
        base = urlsplit(base_url)
        target = urlsplit(candidate)
    except ValueError:
        return False
    return (
        target.scheme in {"http", "https"}
        and target.scheme == base.scheme
        and target.netloc.casefold() == base.netloc.casefold()
    )


def same_publisher_site(candidate: str, base_url: str) -> bool:
    """Allow an explicit publisher link to a normal related subdomain.

    This permits ordinary `www` and `amp` variants while still rejecting a
    third-party mirror.  Every selected URL receives its own robots and TDM
    check before its body can be collected.
    """
    try:
        base_host = (urlsplit(base_url).hostname or "").casefold()
        target = urlsplit(candidate)
        target_host = (target.hostname or "").casefold()
    except ValueError:
        return False
    if target.scheme not in {"http", "https"} or not base_host or not target_host:
        return False
    if base_host.startswith("www."):
        base_host = base_host[4:]
    if target_host.startswith("www."):
        target_host = target_host[4:]
    return (
        target_host == base_host
        or target_host.endswith("." + base_host)
        or base_host.endswith("." + target_host)
    )


def public_alternate_urls(html: str, base_url: str) -> list[dict[str, str]]:
    """Use only same-origin alternates the publisher explicitly links to."""
    soup = BeautifulSoup(html, "html.parser")
    found: list[dict[str, str]] = []
    seen = {base_url}
    for node in soup.find_all("link", href=True):
        rel = {str(value).casefold() for value in (node.get("rel") or [])}
        link_type = str(node.get("type") or "").casefold()
        media = str(node.get("media") or "").casefold()
        kind = ""
        if "canonical" in rel:
            kind = "publisher_linked_canonical"
        elif "amphtml" in rel or "application/amp+html" in link_type:
            kind = "publisher_linked_amp"
        elif "alternate" in rel and "amp" in link_type:
            kind = "publisher_linked_amp"
        elif "print" in media:
            kind = "publisher_linked_print"
        if not kind:
            continue
        candidate = urljoin(base_url, str(node.get("href") or "").strip())
        if not candidate or candidate in seen or not same_publisher_site(candidate, base_url):
            continue
        seen.add(candidate)
        found.append({"url": candidate, "kind": kind})
        if len(found) >= MAX_ALTERNATE_URLS:
            break
    return found


def trace_item(result: dict[str, Any], *, requested_url: str, kind: str) -> dict[str, Any]:
    """Create a body-free recovery trace suitable for the private audit."""
    return {
        "requested_url": requested_url,
        "kind": kind,
        "outcome": str(result.get("outcome") or "unknown"),
        "http_status": result.get("http_status"),
        "final_url": result.get("final_url"),
        "robots_allowed": result.get("robots_allowed"),
        "tdm_reservation": (result.get("tdm") or {}).get("reservation"),
        "paywall_detected": bool(result.get("paywall_detected")),
        "word_count": int(result.get("word_count") or 0),
        "extraction_method": result.get("extraction_method"),
        "error_class": result.get("error_class"),
        "redirect_chain": result.get("redirect_chain") or [],
        "request_attempts": result.get("request_attempts") or [],
    }


def fetch_public_candidate(
    url: str,
    *,
    kind: str,
    redirect_hops: int = 0,
) -> dict[str, Any]:
    """Collect one public candidate only after every access check succeeds.

    Standard publisher redirects are followed explicitly and bounded.  Each
    target URL gets its own robots and TDM check before it is requested, so a
    redirect cannot turn a permitted source into an unchecked collection.
    """
    robots, robots_url, robots_detail = robots_allowed(url)
    if robots is False:
        return {
            "outcome": "blocked_robots",
            "robots_allowed": False,
            "robots_url": robots_url,
            "robots_detail": robots_detail,
            "tdm": {"reservation": "unset", "policy": None},
            "candidate_kind": kind,
        }
    if robots is None:
        return {
            "outcome": "robots_unavailable",
            "robots_allowed": None,
            "robots_url": robots_url,
            "robots_detail": robots_detail,
            "tdm": {"reservation": "unset", "policy": None},
            "candidate_kind": kind,
        }

    tdm = tdmrep_for(url)
    if tdm.get("check_state") in {"unavailable", "invalid"}:
        return {
            "outcome": "tdm_unavailable",
            "robots_allowed": robots,
            "robots_url": robots_url,
            "robots_detail": robots_detail,
            "tdm": tdm,
            "candidate_kind": kind,
        }
    if tdm.get("reservation") == "1":
        return {
            "outcome": "blocked_tdm_reserved",
            "robots_allowed": robots,
            "robots_url": robots_url,
            "robots_detail": robots_detail,
            "tdm": tdm,
            "candidate_kind": kind,
        }

    response, request_attempts, error = public_get(
        url,
        timeout=ARTICLE_TIMEOUT,
        accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
    )
    if response is None:
        return {
            "outcome": "http_error",
            "robots_allowed": robots,
            "robots_url": robots_url,
            "robots_detail": robots_detail,
            "tdm": tdm,
            "candidate_kind": kind,
            "request_attempts": request_attempts,
            **(error or {}),
        }
    elapsed_ms = sum(int(item.get("elapsed_ms") or 0) for item in request_attempts)
    content_type = str(response.headers.get("content-type") or "")
    response_text = response.text
    looks_like_html = (
        "html" in content_type.casefold()
        or response_text.lstrip().casefold().startswith(("<!doctype html", "<html", "<article"))
    )
    html = response_text if looks_like_html else ""
    result: dict[str, Any] = {
        "http_status": response.status_code,
        "robots_allowed": robots,
        "robots_url": robots_url,
        "robots_detail": robots_detail,
        "tdm": tdm,
        "candidate_kind": kind,
        "request_attempts": request_attempts,
        "elapsed_ms": elapsed_ms,
        "content_type": content_type,
        "response_bytes": len(response.content),
        "final_url": str(response.url or url),
        "_html": html,
    }
    if response.status_code in REDIRECT_STATUS_CODES:
        location = str(response.headers.get("location") or "").strip()
        target = urljoin(url, location) if location else ""
        redirect = {
            "from_url": url,
            "to_url": target,
            "http_status": response.status_code,
            "candidate_kind": kind,
        }
        if not target or urlsplit(target).scheme not in {"http", "https"}:
            return {
                **result,
                "outcome": "non_article_media",
                "redirect_chain": [redirect],
                "error_class": "InvalidRedirect",
                "error_message": "Publisher response did not provide an HTTP(S) article redirect.",
            }
        if redirect_hops >= MAX_REDIRECTS:
            return {
                **result,
                "outcome": "http_error",
                "redirect_chain": [redirect],
                "error_class": "RedirectLimitExceeded",
                "error_message": f"Publisher redirect chain exceeded {MAX_REDIRECTS} hops.",
            }
        redirected = fetch_public_candidate(
            target,
            kind=f"{kind}_redirect",
            redirect_hops=redirect_hops + 1,
        )
        redirected["redirect_chain"] = [
            redirect,
            *list(redirected.get("redirect_chain") or []),
        ]
        return redirected
    reservation, policy = html_tdm_signal(response, html)
    if reservation == "1":
        tdm["reservation"] = "1"
        tdm["policy"] = policy or tdm.get("policy")
        return {**result, "outcome": "blocked_tdm_reserved"}
    if response.status_code in {401, 403}:
        return {**result, "outcome": "blocked_access_control"}
    if response.status_code != 200:
        return {**result, "outcome": "http_error"}
    if not html:
        return {**result, "outcome": "non_article_media"}
    if detect_access_challenge(html):
        return {**result, "outcome": "blocked_bot_challenge"}
    if detect_paywall(html):
        return {**result, "outcome": "blocked_paywall_or_login", "paywall_detected": True}
    picked = choose_best_extraction(html)
    if picked["word_count"] < MIN_WORDS:
        return {**result, "outcome": "too_little_extractable_text", "word_count": picked["word_count"]}
    soup = BeautifulSoup(html, "html.parser")
    title = normalize_space(soup.title.get_text(" ", strip=True)) if soup.title else ""
    return {
        **result,
        "outcome": "stored",
        "paywall_detected": False,
        "body_text": picked["text"],
        "word_count": picked["word_count"],
        "extraction_method": picked["method"],
        "extraction_quality": picked["quality"],
        "title_extracted": title,
    }


def without_private_html(result: dict[str, Any]) -> dict[str, Any]:
    output = dict(result)
    output.pop("_html", None)
    return output


def fetch_and_extract(url: str):
    """Try a public article page, then its explicitly linked public variants.

    The recovery is deliberately limited: every candidate must be same-origin,
    publicly linked by the publisher, allowed by robots.txt, and free of an
    applicable TDM reservation. We do not bypass logins, paywalls, challenges,
    publisher blocks, or access controls.
    """
    primary = fetch_public_candidate(url, kind="canonical_public_page")
    trace = [trace_item(primary, requested_url=url, kind="canonical_public_page")]
    primary_html = str(primary.get("_html") or "")

    # A paywall/access refusal is final. An AMP or print variant may be a
    # different public representation, but using it after a paywall would be a
    # circumvention attempt rather than technical recovery.
    if primary.get("outcome") in {
        "stored",
        "blocked_paywall_or_login",
        "blocked_robots",
        "blocked_tdm_reserved",
        "blocked_access_control",
        "blocked_bot_challenge",
        "robots_unavailable",
        "tdm_unavailable",
    }:
        result = without_private_html(primary)
        result["recovery_strategy_version"] = RECOVERY_STRATEGY_VERSION
        result["recovery_trace"] = trace
        return result

    if primary.get("outcome") != "too_little_extractable_text" or not primary_html:
        result = without_private_html(primary)
        result["recovery_strategy_version"] = RECOVERY_STRATEGY_VERSION
        result["recovery_trace"] = trace
        return result

    for alternate in public_alternate_urls(primary_html, str(primary.get("final_url") or url)):
        candidate = fetch_public_candidate(alternate["url"], kind=alternate["kind"])
        trace.append(trace_item(candidate, requested_url=alternate["url"], kind=alternate["kind"]))
        if candidate.get("outcome") == "stored":
            result = without_private_html(candidate)
            result["recovery_strategy_version"] = RECOVERY_STRATEGY_VERSION
            result["recovery_trace"] = trace
            result["recovered_from_alternate"] = True
            return result

    # Keep the canonical result as the decisive reason, while retaining every
    # attempted public alternate in the audit trace.
    result = without_private_html(primary)
    result["recovery_strategy_version"] = RECOVERY_STRATEGY_VERSION
    result["recovery_trace"] = trace
    return result

def page_rows(client, page_size=200):
    start = 0
    while True:
        resp = client.table("articles").select("*").range(start, start + page_size - 1).execute()
        rows = resp.data or []
        if not rows:
            break
        yield from rows
        if len(rows) < page_size:
            break
        start += page_size

def already_stored(client, article_id):
    resp = (client.table("brief_article_content_snapshots")
            .select("snapshot_id").eq("article_id", article_id)
            .eq("is_current", True).limit(1).execute())
    return bool(resp.data)

def insert_attempt(client, article_id, url, result, workflow_run_id):
    # The audit records operational facts only. Never put article body text or
    # raw HTML in the fetch-attempt table, which remains a metadata ledger.
    recovery_trace = result.get("recovery_trace")
    if not isinstance(recovery_trace, list):
        recovery_trace = []
    safe_trace = [item for item in recovery_trace if isinstance(item, dict)][: MAX_ALTERNATE_URLS + 1]
    tdm_reserved = str((result.get("tdm") or {}).get("reservation") or "").strip() == "1"
    client.table("brief_article_fetch_attempts").insert({
        "article_id":article_id,
        "source_url":url,
        "source_domain":domain_of(url),
        "workflow_run_id":workflow_run_id,
        "retrieval_method":RECOVERY_STRATEGY_VERSION,
        "http_status":result.get("http_status"),
        "robots_allowed":result.get("robots_allowed"),
        "tdm_reservation":tdm_reserved,
        "tdm_policy_url":(result.get("tdm") or {}).get("policy"),
        "paywall_detected":result.get("paywall_detected"),
        "outcome":result.get("outcome") or "unknown",
        "response_content_type":result.get("content_type"),
        "response_bytes":result.get("response_bytes"),
        "elapsed_ms":result.get("elapsed_ms"),
        "metadata":{
            "recovery_strategy_version": result.get("recovery_strategy_version") or RECOVERY_STRATEGY_VERSION,
            "candidate_kind": result.get("candidate_kind"),
            "robots_url":result.get("robots_url"),
            "robots_detail":result.get("robots_detail"),
            "tdmrep_url":(result.get("tdm") or {}).get("url"),
            "tdm_check_state":(result.get("tdm") or {}).get("check_state"),
            "final_url":result.get("final_url"),
            "word_count":result.get("word_count"),
            "extraction_method":result.get("extraction_method"),
            "extraction_quality":result.get("extraction_quality"),
            "recovered_from_alternate":bool(result.get("recovered_from_alternate")),
            "request_attempts":result.get("request_attempts") or [],
            "redirect_chain":result.get("redirect_chain") or [],
            "recovery_trace":safe_trace,
            "error_class":result.get("error_class"),
            "error_message":compact_error(result.get("error_message")),
        },
    }).execute()

def store_snapshot(client, row, url, result):
    article_id = str(row.get("article_id") or row.get("id") or "").strip()
    text = result["body_text"]
    digest = sha256_text(text)

    client.table("brief_article_content_snapshots").update({"is_current":False}).eq(
        "article_id", article_id
    ).execute()

    client.table("brief_article_content_snapshots").upsert({
        "article_id":article_id,
        "source_url":result.get("final_url") or url,
        "source_domain":domain_of(result.get("final_url") or url),
        "retrieval_method":result.get("extraction_method") or RECOVERY_STRATEGY_VERSION,
        "http_status":result.get("http_status"),
        "mime_type":result.get("content_type"),
        "extracted_title":result.get("title_extracted"),
        "body_text":text,
        "word_count":result.get("word_count") or word_count(text),
        "text_sha256":digest,
        "extraction_quality":result.get("extraction_quality"),
        "content_basis":"full_page_extraction",
        "rights_status":"stored_private_unreserved_signal",
        "rights_basis":"lawfully accessible public page; robots not denied; no detected TDM reservation; private analytical storage only",
        "robots_allowed":result.get("robots_allowed"),
        "tdm_reservation":str((result.get("tdm") or {}).get("reservation") or "").strip() == "1",
        "tdm_policy_url":(result.get("tdm") or {}).get("policy"),
        "paywall_detected":False,
        "is_current":True,
    }, on_conflict="article_id,text_sha256").execute()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--retry-existing", action="store_true")
    parser.add_argument("--sleep", type=float, default=1.0)
    args = parser.parse_args()

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    workflow_run_id = os.environ.get("GITHUB_RUN_ID")
    counters, methods = {}, {}
    processed = 0

    for row in page_rows(client):
        article_id = str(row.get("article_id") or row.get("id") or "").strip()
        url = article_url(row)
        if not article_id or not url:
            counters["missing_id_or_url"] = counters.get("missing_id_or_url",0)+1
            continue
        if not args.retry_existing and already_stored(client, article_id):
            counters["already_stored"] = counters.get("already_stored",0)+1
            continue
        if args.limit and processed >= args.limit:
            break

        processed += 1
        print(f"[{processed}] {article_id} {url}", flush=True)
        try:
            result = fetch_and_extract(url)
        except Exception as exc:
            result = {
                "outcome": "exception",
                "error_class": type(exc).__name__,
                "error_message": compact_error(exc),
            }

        outcome = result.get("outcome") or "unknown"
        counters[outcome] = counters.get(outcome,0)+1
        method = result.get("extraction_method")
        if method:
            methods[method] = methods.get(method,0)+1
        label = "retrievable" if args.dry_run and outcome == "stored" else outcome
        print(f"  -> {label} ({result.get('word_count','-')} words; {method or '-'})", flush=True)

        if not args.dry_run:
            insert_attempt(client, article_id, url, result, workflow_run_id)
            if outcome == "stored":
                store_snapshot(client, row, url, result)

        time.sleep(max(0.0,args.sleep))

    print(json.dumps({"processed":processed,"counts":counters,"extraction_methods":methods}, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
