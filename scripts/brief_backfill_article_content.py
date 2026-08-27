#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
import trafilatura
from bs4 import BeautifulSoup
from supabase import create_client

from brief_content_common import article_url, domain_of, normalize_space, sha256_text

USER_AGENT = "AIEOResearchBot/1.1 (+https://observatory.hamelberg-ai.com/methodology/)"
TIMEOUT = (10, 35)
MIN_WORDS = 80

def robots_allowed(url: str) -> tuple[bool | None, str]:
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        return bool(rp.can_fetch(USER_AGENT, url)), robots_url
    except Exception:
        return None, robots_url

def tdmrep_for(url: str) -> dict:
    parts = urlsplit(url)
    endpoint = f"{parts.scheme}://{parts.netloc}/.well-known/tdmrep.json"
    out = {"url": endpoint, "reservation": "unset", "policy": None}
    try:
        r = requests.get(endpoint, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        if r.status_code != 200:
            return out
        rules = r.json()
        if not isinstance(rules, list):
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
    except Exception:
        pass
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

def fetch_and_extract(url: str):
    robots, robots_url = robots_allowed(url)
    if robots is False:
        return {"outcome":"blocked_robots", "robots_allowed":False, "robots_url":robots_url,
                "tdm":{"reservation":"unset","policy":None}}

    tdm = tdmrep_for(url)
    if tdm.get("reservation") == "1":
        return {"outcome":"blocked_tdm_reserved", "robots_allowed":robots,
                "robots_url":robots_url, "tdm":tdm}

    started = time.monotonic()
    r = requests.get(
        url,
        headers={"User-Agent":USER_AGENT, "Accept":"text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"},
        timeout=TIMEOUT,
        allow_redirects=True,
    )
    elapsed_ms = round((time.monotonic() - started) * 1000)
    content_type = r.headers.get("content-type") or ""
    html = r.text if "html" in content_type.casefold() else ""

    reservation, policy = html_tdm_signal(r, html)
    if reservation == "1":
        tdm["reservation"] = "1"
        tdm["policy"] = policy or tdm.get("policy")
        return {"outcome":"blocked_tdm_reserved", "http_status":r.status_code,
                "robots_allowed":robots, "robots_url":robots_url, "tdm":tdm,
                "elapsed_ms":elapsed_ms, "content_type":content_type,
                "response_bytes":len(r.content)}

    if r.status_code != 200:
        return {"outcome":"http_error", "http_status":r.status_code,
                "robots_allowed":robots, "robots_url":robots_url, "tdm":tdm,
                "elapsed_ms":elapsed_ms, "content_type":content_type,
                "response_bytes":len(r.content)}

    if detect_paywall(html):
        return {"outcome":"blocked_paywall_or_login", "http_status":r.status_code,
                "robots_allowed":robots, "robots_url":robots_url, "tdm":tdm,
                "elapsed_ms":elapsed_ms, "content_type":content_type,
                "response_bytes":len(r.content), "paywall_detected":True}

    picked = choose_best_extraction(html)
    if picked["word_count"] < MIN_WORDS:
        return {"outcome":"too_little_extractable_text", "http_status":r.status_code,
                "robots_allowed":robots, "robots_url":robots_url, "tdm":tdm,
                "elapsed_ms":elapsed_ms, "content_type":content_type,
                "response_bytes":len(r.content), "word_count":picked["word_count"]}

    soup = BeautifulSoup(html, "html.parser")
    title = normalize_space(soup.title.get_text(" ", strip=True)) if soup.title else ""

    return {
        "outcome":"stored", "http_status":r.status_code, "robots_allowed":robots,
        "robots_url":robots_url, "tdm":tdm, "elapsed_ms":elapsed_ms,
        "content_type":content_type, "response_bytes":len(r.content),
        "paywall_detected":False, "body_text":picked["text"],
        "word_count":picked["word_count"], "extraction_method":picked["method"],
        "extraction_quality":picked["quality"], "title_extracted":title,
        "final_url":r.url,
    }

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
    client.table("brief_article_fetch_attempts").insert({
        "article_id":article_id,
        "source_url":url,
        "source_domain":domain_of(url),
        "workflow_run_id":workflow_run_id,
        "retrieval_method":"direct_public_web_v1.1",
        "http_status":result.get("http_status"),
        "robots_allowed":result.get("robots_allowed"),
        "tdm_reservation":(result.get("tdm") or {}).get("reservation"),
        "tdm_policy_url":(result.get("tdm") or {}).get("policy"),
        "paywall_detected":result.get("paywall_detected"),
        "outcome":result.get("outcome") or "unknown",
        "response_content_type":result.get("content_type"),
        "response_bytes":result.get("response_bytes"),
        "elapsed_ms":result.get("elapsed_ms"),
        "metadata":{
            "robots_url":result.get("robots_url"),
            "tdmrep_url":(result.get("tdm") or {}).get("url"),
            "final_url":result.get("final_url"),
            "word_count":result.get("word_count"),
            "extraction_method":result.get("extraction_method"),
            "extraction_quality":result.get("extraction_quality"),
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
        "retrieval_method":result.get("extraction_method") or "direct_public_web_v1.1",
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
        "tdm_reservation":(result.get("tdm") or {}).get("reservation"),
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
            result = {"outcome":"exception","error":f"{type(exc).__name__}: {exc}"}

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
