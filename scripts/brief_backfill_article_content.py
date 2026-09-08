#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import time
import ipaddress
import signal
import socket
import subprocess
import sys
import traceback
from functools import lru_cache
from pathlib import Path
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from types import SimpleNamespace
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
import trafilatura
from bs4 import BeautifulSoup
from supabase import create_client

from brief_content_common import (
    MIN_FULL_BODY_EVIDENCE_UNITS,
    article_url,
    domain_of,
    evidence_unit_count,
    normalize_space,
    sha256_text,
)

USER_AGENT = "AIEOResearchBot/1.2 (+https://observatory.hamelberg-ai.com/methodology/)"
ROBOTS_TIMEOUT = (5, 10)
TDM_TIMEOUT = (5, 10)
ARTICLE_TIMEOUT = (10, 30)
from source_evidence_quality import assess_body
import article_recovery_support as recovery
from article_consent import needs_consent_browser
MIN_WORDS = MIN_FULL_BODY_EVIDENCE_UNITS
FETCH_RETRY_ATTEMPTS = 3
MAX_ALTERNATE_URLS = 6
MAX_REDIRECTS = 4
RECOVERY_STRATEGY_VERSION = "publisher_body_recovery_v7_consent_2026_09_08"
MAX_RESPONSE_BYTES = 12_000_000
_POLICY_CACHE = {}
TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


@lru_cache(maxsize=1024)
def public_url(url):
    try:
        p = urlsplit(url)
        if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password or p.port not in {None, 80, 443}:
            return False
        addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(row[4][0]).is_global for row in addresses)
    except (ValueError, OSError):
        return False


def _request_headers(*, accept: str) -> dict[str, str]:
    # Do not ask publishers for an English variant.  The publisher's original
    # public page is the evidence, including French, Chinese, bilingual
    # Canadian and other multilingual reporting.
    return {
        "User-Agent": USER_AGENT,
        "Accept": accept,
    }


def compact_error(error: Any, limit: int = 280) -> str:
    """Keep useful diagnostics without persisting response or body content."""
    return re.sub(r"\s+", " ", str(error or "")).strip()[:limit]


def exception_result(exc: Exception) -> dict[str, Any]:
    """Record the failing collector function, never locals or source HTML."""
    frames = traceback.extract_tb(exc.__traceback__)
    local = [frame for frame in frames if Path(frame.filename).parent == Path(__file__).resolve().parent]
    frame = (local or frames)[-1] if frames else None
    return {
        "outcome": "exception", "error_class": type(exc).__name__,
        "error_message": compact_error(exc),
        "error_location": f"{Path(frame.filename).name}:{frame.name}:{frame.lineno}" if frame else None,
    }


def policy_diagnostics(result: dict[str, Any]) -> dict[str, Any]:
    """Expose the failed prerequisite separately from the article response."""
    robots = result.get("robots_detail") or {}
    tdm = result.get("tdm") or {}
    return {
        "robots_url": result.get("robots_url"),
        "robots_http_status": robots.get("http_status"),
        "robots_policy_state": robots.get("policy_state"),
        "robots_error_class": robots.get("error_class"),
        "robots_error_message": compact_error(robots.get("error_message")),
        "robots_request_attempts": robots.get("request_attempts") or [],
        "tdmrep_url": tdm.get("url"),
        "tdm_http_status": tdm.get("http_status"),
        "tdm_check_state": tdm.get("check_state"),
        "tdm_error_class": tdm.get("error_class"),
        "tdm_error_message": compact_error(tdm.get("error_message")),
        "tdm_request_attempts": tdm.get("request_attempts") or [],
    }


def retry_delay_seconds(response: requests.Response | None, attempt: int) -> float:
    """Respect a modest Retry-After value and otherwise use bounded backoff."""
    retry_after = ""
    if response is not None:
        retry_after = str(response.headers.get("Retry-After") or "").strip()
    try:
        return max(0.0, float(retry_after))
    except (TypeError, ValueError):
        if retry_after:
            try:
                return max(0.0, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError):
                pass
        return min(4.0, 0.8 * (2 ** max(0, attempt - 1)))


def decode_article_html(response: requests.Response) -> str:
    """Decode publisher HTML without assuming an English or Latin page.

    Requests can fall back to ISO-8859-1 for a page that omits a charset.
    That fallback silently turns UTF-8 Chinese, French and multilingual
    Canadian reporting into mojibake. Prefer explicit page metadata, then
    charset detection, before using that legacy fallback.
    """
    raw = bytes(response.content or b"")
    if not raw:
        return ""

    candidates: list[str] = []
    content_type = str(response.headers.get("content-type") or "")
    header_match = re.search(r"charset\s*=\s*['\"]?([^;\s'\"]+)", content_type, re.I)
    if header_match:
        candidates.append(header_match.group(1))

    head = raw[:8192].decode("ascii", errors="ignore")
    meta_match = re.search(r"<meta[^>]+charset\s*=\s*['\"]?([^\s'\">/]+)", head, re.I)
    if not meta_match:
        meta_match = re.search(r"charset\s*=\s*['\"]?([^;\s'\">]+)", head, re.I)
    if meta_match:
        candidates.append(meta_match.group(1))

    # A valid UTF-8 byte stream is stronger than a statistical charset guess.
    candidates.append("utf-8-sig")
    apparent = str(getattr(response, "apparent_encoding", "") or "").strip()
    if apparent:
        candidates.append(apparent)
    declared = str(response.encoding or "").strip()
    if declared and declared.casefold() not in {"iso-8859-1", "latin-1"}:
        candidates.append(declared)
    candidates.extend(["utf-8", declared, "windows-1252"])

    seen = set()
    for encoding in candidates:
        normalized = str(encoding or "").strip()
        if not normalized or normalized.casefold() in seen:
            continue
        seen.add(normalized.casefold())
        try:
            return raw.decode(normalized)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def public_get(
    url: str,
    *,
    timeout: tuple[int, int],
    accept: str,
    max_attempts: int = FETCH_RETRY_ATTEMPTS,
    allow_redirects: bool = False,
    _redirect_hops: int = 0,
) -> tuple[requests.Response | None, list[dict[str, Any]], dict[str, str] | None]:
    """Fetch a public URL with bounded retries for transient failures only.

    This does not rotate identities, solve challenges, use a proxy, or retry a
    publisher's explicit access decision. It is solely for ordinary temporary
    transport failures and the HTTP statuses publishers label as retryable.
    """
    if not public_url(url):
        return None, [], {"error_class": "InvalidPublicURL", "error_message": "URL is not a public HTTP(S) resource"}
    is_policy = urlsplit(url).path in {"/robots.txt", "/.well-known/tdmrep.json"}
    cached = _POLICY_CACHE.get(url) if is_policy else None
    if cached and time.monotonic() - cached[0] < 3600:
        return cached[1], [{"cached": True, "http_status": cached[1].status_code, "elapsed_ms": 0}], None
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
                allow_redirects=False,
                stream=True,
            )
            limit = 512_000 if is_policy else MAX_RESPONSE_BYTES
            chunks, size = [], 0
            try:
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > limit:
                        return None, history, {"error_class": "ResponseSizeLimit", "error_message": f"Response exceeded {limit} bytes"}
                    chunks.append(chunk)
                response._content = b"".join(chunks)
                response._content_consumed = True
            finally:
                response.close()
            elapsed_ms = round((time.monotonic() - started) * 1000)
            history.append(
                {
                    "attempt": attempt,
                    "http_status": response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "final_url": str(response.url or url),
                }
            )
            if allow_redirects and response.status_code in REDIRECT_STATUS_CODES:
                target = urljoin(url, response.headers.get("location", ""))
                if _redirect_hops >= MAX_REDIRECTS or target == url:
                    return response, history, None
                redirected, extra, error = public_get(target, timeout=timeout, accept=accept,
                    max_attempts=max_attempts, allow_redirects=True, _redirect_hops=_redirect_hops+1)
                return redirected, history + extra, error
            if is_policy and response.status_code in {200, 404, 410}:
                _POLICY_CACHE[url] = (time.monotonic(), response)
            if response.status_code not in TRANSIENT_STATUS_CODES or attempt >= max_attempts:
                return response, history, None
            delay = retry_delay_seconds(response, attempt)
            if delay > 8:
                # Defer this source to a later run instead of ignoring Retry-After.
                return response, history, None
            time.sleep(delay)
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
        detail["policy_state"] = "access_denied"
        return False, robots_url, detail
    # RFC 9309 defines a missing robots resource as unavailable. That means
    # there are no robots rules to apply, not that the article is blocked.
    # The article itself still goes through the separate TDM, paywall and
    # access-control checks below.
    if response.status_code in {404, 410}:
        detail["policy_state"] = "absent"
        return True, robots_url, detail
    if response.status_code != 200:
        detail["policy_state"] = "unavailable"
        return None, robots_url, detail
    try:
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.parse(response.text.splitlines())
        detail["policy_state"] = "parsed"
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

def detect_paywall(html: str, url: str = "") -> bool:
    return recovery.paywall(html, url)


def detect_access_challenge(html: str) -> bool:
    return recovery.access_challenge(html)


def word_count(text: str) -> int:
    """Backwards-compatible name for the shared multilingual body measure."""
    return evidence_unit_count(text)

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

def choose_best_extraction(html: str, url: str = ""):
    candidates = list(recovery.embedded_bodies(html, url))
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select(recovery.ARTICLE_SELECTORS):
        # Skip broad containers of unrelated article cards.
        if len(node.select("article")) <= 1:
            candidates.append(("semantic_html", recovery.html_text(str(node))))
    for precision in (True, False):
        try:
            text = trafilatura.extract(html, url=url or None, include_comments=False,
                include_tables=True, favor_precision=precision, favor_recall=not precision) or ""
            candidates.append(("trafilatura_precision" if precision else "trafilatura_recall", clean_text(text)))
        except Exception:
            continue
    accepted = []
    for method, text in candidates:
        if word_count(text) < MIN_WORDS or not assess_body({"body_text": text})["usable_complete_body"]:
            continue
        # Prefer article-specific structure; never pick generic app JSON or page text.
        score = extraction_quality(text, method)
        accepted.append((score, min(word_count(text), 10000), method, text))
    if not accepted:
        return {"text": "", "method": None, "word_count": 0, "quality": 0.0}
    accepted.sort(reverse=True)
    _, _, method, text = accepted[0]
    # If a verified article container extends the selected text, preserve its ending.
    # This is containment, not concatenation of different candidates/stories.
    compact = normalize_space(text)
    for _, _, other_method, other in accepted:
        if other_method in {"semantic_html", "jsonld_articleBody", "embedded_json_article_body", "embedded_json_article_blocks"}:
            if compact in normalize_space(other) and len(other) > len(text):
                method, text, compact = other_method, other, normalize_space(other)
    return {"text": text, "method": method, "word_count": word_count(text), "quality": extraction_quality(text, method)}


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
    return recovery.same_publisher(candidate, base_url)


def public_alternate_urls(html: str, base_url: str) -> list[dict[str, str]]:
    return recovery.alternate_links(html, base_url)


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
        "error_message": compact_error(result.get("error_message")),
        "error_location": result.get("error_location"),
        "consent": result.get("consent"),
        "redirect_chain": result.get("redirect_chain") or [],
        "request_attempts": result.get("request_attempts") or [],
        **policy_diagnostics(result),
    }


def fetch_public_candidate(
    url: str,
    *,
    kind: str,
    redirect_hops: int = 0,
    identity_url: str = "",
) -> dict[str, Any]:
    """Collect one public candidate only after every access check succeeds.

    Standard publisher redirects are followed explicitly and bounded.  Each
    target URL gets its own robots and TDM check before it is requested, so a
    redirect cannot turn a permitted source into an unchecked collection.
    """
    if not public_url(url):
        return {"outcome": "http_error", "error_class": "InvalidPublicURL", "candidate_kind": kind}
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
    response_text = decode_article_html(response)
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
        "_headers": dict(response.headers),
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
            identity_url=identity_url,
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
        return extract_non_html(response, result, identity_url or url)
    return extract_html_result(html, result)


def extract_html_result(html, result, *, consent_checked=False):
    title = recovery.page_title(html)
    if re.match(r"^(?:404(?:\s*[-:|]\s*.*)?|page not found(?:\s*[-|].*)?|not found|page introuvable(?:\s*[-|].*)?|页面不存在|页面未找到)\s*$", title, re.I):
        return {**result, "outcome": "source_unavailable", "error_class": "PublisherSoft404"}
    if detect_access_challenge(html):
        return {**result, "outcome": "blocked_bot_challenge"}
    if not consent_checked and needs_consent_browser(html):
        return {**result, "outcome": "consent_required"}
    if detect_paywall(html, result.get("final_url", "")):
        return {**result, "outcome": "blocked_paywall_or_login", "paywall_detected": True}
    if recovery.abstract_only(html):
        return {**result, "outcome": "abstract_only"}
    picked = choose_best_extraction(html, result.get("final_url", ""))
    if picked["word_count"] < MIN_WORDS:
        return {**result, "outcome": "too_little_extractable_text", "word_count": picked["word_count"]}
    return {**result, "outcome": "stored", "paywall_detected": False,
        "body_text": picked["text"], "word_count": picked["word_count"],
        "extraction_method": picked["method"], "extraction_quality": picked["quality"],
        "title_extracted": title}


def extract_non_html(response, result, identity_url):
    from article_recovery_formats import publisher_content, pdf_content
    mime = result.get("content_type", "").lower()
    kind = result.get("candidate_kind", "")
    if "pdf" in mime and (kind.startswith("publisher_linked_pdf") or urlsplit(identity_url).path.lower().endswith(".pdf")):
        try:
            picked = pdf_content(response.content)
        except Exception as exc:
            return {**result, "outcome": "pdf_needs_review", "error_class": type(exc).__name__, "error_message": compact_error(exc)}
        if word_count(picked["text"]) >= MIN_WORDS and assess_body({"body_text": picked["text"]})["usable_complete_body"]:
            return {**result, "outcome": "stored", "body_text": picked["text"], "word_count": word_count(picked["text"]), "extraction_method": picked["method"], "extraction_quality": .8, "pdf_pages": picked["pages"], "identity_url": identity_url}
    if kind.startswith(("publisher_linked_cms", "publisher_linked_feed")):
        picked = publisher_content(response.content, mime, identity_url)
        if picked and not detect_paywall(picked["html"]) and not detect_access_challenge(picked["html"]):
            text = picked["text"]
            # Full feeds must not end with a continuation/teaser link.
            teaser = re.search(r"(?:read (?:more|the full (?:article|story))|continue reading|lire la suite)\W*$", text, re.I)
            if not teaser and word_count(text) >= MIN_WORDS and assess_body({"body_text": text})["usable_complete_body"]:
                return {**result, "outcome": "stored", "body_text": text, "word_count": word_count(text), "extraction_method": picked["method"], "extraction_quality": .85, "title_extracted": picked["title"], "identity_url": identity_url}
        return {**result, "outcome": "no_matching_full_content"}
    return {**result, "outcome": "non_article_media"}


def without_private_html(result: dict[str, Any]) -> dict[str, Any]:
    output = dict(result)
    output = {key: value for key, value in output.items() if not key.startswith("_")}
    return output


def render_fallback(primary):
    if os.environ.get("AIEO_RENDER_ARTICLES", "0").lower() not in {"1", "true"}:
        return {"outcome": "browser_not_enabled"}
    request = {"url": primary["final_url"], "html": primary["_html"], "headers": primary.get("_headers", {})}
    proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name("render_public_article.py"))],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, _ = proc.communicate(json.dumps(request), timeout=65)
        data = json.loads(stdout)
        if data.get("error_class"):
            return {"outcome": "browser_error", **data}
        consent = data.get("consent") or {}
        if consent.get("status") == "unresolved":
            return {"outcome": "consent_unresolved", "consent": consent}
        if data.get("blocked_requests"):
            return {"outcome": "browser_data_unavailable", "consent": consent, "error_message": ", ".join(data["blocked_requests"])}
        if not data.get("html") or data.get("final_url") != primary["final_url"]:
            return {"outcome": "browser_identity_mismatch"}
        rendered = {**primary, "_html": data["html"], "consent": consent, "candidate_kind": "browser_rendered_public_page"}
        # Recheck publisher reservation signals in the post-consent document.
        reservation, _ = html_tdm_signal(SimpleNamespace(headers=primary.get('_headers', {})), data['html'])
        if reservation == '1':
            return {**rendered, 'outcome': 'blocked_tdm_reserved', 'tdm': {**(primary.get('tdm') or {}), 'reservation': '1'}}
        result = extract_html_result(data["html"], rendered, consent_checked=True)
        if result.get("outcome") == "stored":
            result["extraction_method"] = "browser_" + result["extraction_method"]
        return result
    except subprocess.TimeoutExpired:
        return {"outcome": "browser_timeout"}
    except (ValueError, OSError) as exc:
        return {"outcome": "browser_error", "error_class": type(exc).__name__, "error_message": compact_error(exc)}
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()


def fetch_and_extract(url: str, expected_title: str = ""):
    """Try accessible publisher representations of the same original article."""
    primary = fetch_public_candidate(url, kind="canonical_public_page")
    trace = [trace_item(primary, requested_url=url, kind="canonical_public_page")]
    def finish(result):
        return {**without_private_html(result), "recovery_strategy_version": RECOVERY_STRATEGY_VERSION, "recovery_trace": trace}
    if primary.get("outcome") == "stored" and expected_title and primary.get("final_url") and recovery.url_identity(primary["final_url"]) != recovery.url_identity(url):
        if not recovery.same_article(primary.get("_html", ""), primary["final_url"], url, expected_title):
            return finish({**primary, "outcome": "article_identity_mismatch", "body_text": ""})
    if primary.get("outcome") == "consent_required":
        # Resolve the original dialog before considering another representation.
        # Acceptance is not evidence of completeness: all extraction gates run again.
        rendered = render_fallback(primary)
        trace.append(trace_item(rendered, requested_url=primary.get('final_url') or url, kind='browser_cookie_consent'))
        if rendered.get('outcome') == 'stored' and expected_title and recovery.url_identity(rendered.get('final_url', url)) != recovery.url_identity(url):
            if not recovery.same_article(rendered.get('_html', ''), rendered['final_url'], url, expected_title):
                rendered = {**rendered, 'outcome': 'article_identity_mismatch', 'body_text': ''}
        return finish({**primary, **rendered})
    if primary.get("outcome") not in {"too_little_extractable_text", "abstract_only"} or not primary.get("_html"):
        return finish(primary)
    original = str(primary.get("final_url") or url)
    title = recovery.page_title(primary["_html"]) or expected_title
    # These links are discovered on an accessible original page, never a gate.
    for alternate in public_alternate_urls(primary["_html"], original):
        candidate = fetch_public_candidate(alternate["url"], kind=alternate["kind"], identity_url=original)
        if candidate.get("outcome") == "stored":
            verified = candidate.get("identity_url") == original or recovery.same_article(candidate.get("_html", ""), candidate.get("final_url", alternate["url"]), original, title)
            if not verified:
                candidate = {**candidate, "outcome": "article_identity_mismatch"}
        trace.append(trace_item(candidate, requested_url=alternate["url"], kind=alternate["kind"]))
        if candidate.get("outcome") == "stored":
            candidate["recovered_from_alternate"] = True
            return finish(candidate)
    rendered = render_fallback(primary)
    trace.append(trace_item(rendered, requested_url=original, kind="browser_rendered_public_page"))
    if rendered.get("outcome") == "stored":
        return finish(rendered)
    if rendered.get("outcome") in {"blocked_paywall_or_login", "blocked_bot_challenge", "browser_error", "browser_timeout", "browser_data_unavailable", "consent_unresolved", "blocked_tdm_reserved"}:
        return finish({**primary, **without_private_html(rendered)})
    return finish(primary)


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
    safe_trace = [item for item in recovery_trace if isinstance(item, dict)][: MAX_ALTERNATE_URLS + 2]
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
            **policy_diagnostics(result),
            "recovery_session": result.get("recovery_session"),
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
            "error_location":result.get("error_location"),
            "consent":result.get("consent"),
        },
    }).execute()

def store_snapshot(client, row, url, result):
    article_id = str(row.get("article_id") or row.get("id") or "").strip()
    text = result["body_text"]
    quality = assess_body(result)
    if not quality["usable_complete_body"]:
        raise ValueError("Rejected source body: " + ", ".join(quality["flags"]))
    digest = sha256_text(text)

    previous = client.table("brief_article_content_snapshots").select("text_sha256").eq("article_id", article_id).eq("is_current", True).execute().data or []
    if any(item.get("text_sha256") == digest for item in previous):
        return
    # Stage new bytes before clearing the old current pointer. A failed insert
    # must not remove a previously available source body.
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
        "is_current":False,
    }, on_conflict="article_id,text_sha256").execute()
    try:
        client.table("brief_article_content_snapshots").update({"is_current": False}).eq("article_id", article_id).execute()
        client.table("brief_article_content_snapshots").update({"is_current": True}).eq("article_id", article_id).eq("text_sha256", digest).execute()
    except Exception:
        for item in previous:
            client.table("brief_article_content_snapshots").update({"is_current": True}).eq("article_id", article_id).eq("text_sha256", item["text_sha256"]).execute()
        raise

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
            result = exception_result(exc)

        outcome = result.get("outcome") or "unknown"
        counters[outcome] = counters.get(outcome,0)+1
        method = result.get("extraction_method")
        if method:
            methods[method] = methods.get(method,0)+1
        label = "retrievable" if args.dry_run and outcome == "stored" else outcome
        print(
            f"  -> {label} ({result.get('word_count','-')} evidence units; {method or '-'})",
            flush=True,
        )

        if not args.dry_run:
            insert_attempt(client, article_id, url, result, workflow_run_id)
            if outcome == "stored":
                store_snapshot(client, row, url, result)

        time.sleep(max(0.0,args.sleep))

    print(json.dumps({"processed":processed,"counts":counters,"extraction_methods":methods}, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
