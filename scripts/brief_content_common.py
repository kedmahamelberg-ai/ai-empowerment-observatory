from __future__ import annotations
import hashlib
import re
from urllib.parse import urlsplit

def normalize_space(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()

def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def domain_of(url: str) -> str:
    try:
        return urlsplit(url).netloc.casefold().removeprefix("www.")
    except Exception:
        return ""

def article_url(row: dict) -> str:
    meta = row.get("source_metadata") if isinstance(row.get("source_metadata"), dict) else {}
    candidates = [
        row.get("canonical_url"), row.get("url"), row.get("link"), row.get("source_url"),
        meta.get("canonical_url"), meta.get("url"), meta.get("link"), meta.get("source_url")
    ]
    for value in candidates:
        value = normalize_space(value)
        if value.startswith("http://") or value.startswith("https://"):
            return value
    return ""
