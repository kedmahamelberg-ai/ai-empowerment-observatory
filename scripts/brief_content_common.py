from __future__ import annotations
import hashlib
import re
import unicodedata
from urllib.parse import urlsplit


# A body is considered usable once it contains enough linguistic evidence to
# support a classification.  The database column retains its historic name
# ``word_count``, but this measure must not assume that all news languages use
# whitespace between words.  Chinese, Japanese, Korean, Thai, Lao, Khmer and
# Myanmar are included because a whitespace-only counter silently discards
# their article bodies.
MIN_FULL_BODY_EVIDENCE_UNITS = 80
SPACELESS_SCRIPT_RE = re.compile(
    r"[\u1000-\u109F\u1780-\u17FF\u3040-\u30FF\u3400-\u4DBF"
    r"\u4E00-\u9FFF\uAC00-\uD7AF\uF900-\uFAFF\u0E00-\u0EFF]"
)

def normalize_space(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def evidence_unit_count(value: str) -> int:
    """Count multilingual article evidence without an English-only rule.

    Languages that mark word boundaries with spaces are counted as tokens.
    For scripts that commonly do not, each letter or number is an evidence
    unit.  This is deliberately a minimum-body gate, not a linguistic claim
    that every character is a word.
    """
    text = str(value or "")
    spaceless_characters = SPACELESS_SCRIPT_RE.findall(text)
    without_spaceless = SPACELESS_SCRIPT_RE.sub(" ", text)
    whitespace_tokens = re.findall(r"\S+", without_spaceless)
    script_units = sum(
        1
        for character in spaceless_characters
        if unicodedata.category(character).startswith(("L", "N"))
    )
    return len(whitespace_tokens) + script_units

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
