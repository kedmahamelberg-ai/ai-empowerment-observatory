from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "utm_source","utm_medium","utm_campaign","utm_term","utm_content",
    "gclid","fbclid","mc_cid","mc_eid",
}

SNIPPET_KEYS = (
    "snippet",
    "description",
    "summary",
    "source_snippet",
    "excerpt",
)

def normalize_space(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()

def canonicalize_url(value) -> str:
    raw = normalize_space(value)
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    filtered_query = [
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in TRACKING_PARAMS
    ]
    return urlunsplit((
        parts.scheme.casefold(),
        parts.netloc.casefold(),
        parts.path.rstrip("/"),
        urlencode(filtered_query),
        "",
    ))

def domain_of(url: str) -> str:
    try:
        return urlsplit(url).netloc.casefold().removeprefix("www.")
    except Exception:
        return ""

def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def source_name(item: dict) -> str:
    source = item.get("source")
    if isinstance(source, dict):
        return normalize_space(source.get("name") or source.get("title"))
    return normalize_space(source)

def iter_news_items(items: Iterable):
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if item.get("title") and item.get("link"):
            yield item
        highlight = item.get("highlight")
        if isinstance(highlight, dict):
            yield from iter_news_items([highlight])
        stories = item.get("stories")
        if isinstance(stories, list):
            yield from iter_news_items(stories)

def snippet_from_item(item: dict) -> tuple[str, str | None]:
    for key in SNIPPET_KEYS:
        value = normalize_space(item.get(key))
        if value:
            return value, key

    # Some Google News structures expose a nested source/summary object.
    for container_key in ("metadata", "news_source", "source"):
        node = item.get(container_key)
        if not isinstance(node, dict):
            continue
        for key in SNIPPET_KEYS:
            value = normalize_space(node.get(key))
            if value:
                return value, f"{container_key}.{key}"

    return "", None

def decode_storage_json(raw: bytes):
    return json.loads(raw.decode("utf-8"))
