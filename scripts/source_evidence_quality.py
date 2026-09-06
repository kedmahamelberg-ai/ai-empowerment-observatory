"""Check saved source bytes before using them as complete article evidence."""
from __future__ import annotations

import hashlib
import re
from typing import Any

QUALITY_VERSION = "stored_source_quality_v2"
ACCESS_GATES = (
    r"you have\s+\d+(?:[.,]\d+)?\s*%\s+of (?:this|the) article",
    r"(?:remaining|reste)\s+\d+(?:[.,]\d+)?\s*%",
    r"(?:subscribe|sign in|log in) to (?:continue reading|read (?:the full|this) article)",
    r"(?:cet article|la suite de cet article) est r[eé]serv[eé](?:e)? aux abonn[eé]s",
    r"already (?:a subscriber|subscribed)\??\s*(?:sign|log) in",
    r"enable javascript and cookies to continue",
    r"checking (?:your browser|if the site connection is secure)",
)


def assess_body(row: dict[str, Any], *, language: str = "") -> dict[str, Any]:
    body = str(row.get("body_text") or "").strip()
    flags = []
    actual_hash = hashlib.sha256(str(row.get("body_text") or "").encode("utf-8")).hexdigest()
    if not body:
        flags.append("body_missing")
    if body and row.get("text_sha256") and actual_hash != row["text_sha256"]:
        flags.append("body_hash_mismatch")
    if row.get("paywall_detected") is True or str(row.get("paywall_detected")).lower() == "true":
        flags.append("access_gate_recorded")
    if any(re.search(pattern, body, re.I) for pattern in ACCESS_GATES):
        flags.append("subscriber_preview_or_access_gate")
    replacement = body.count("\ufffd")
    mojibake = len(re.findall(r"[ÃÂ][\u0080-\u00bf]|[äåæçèé][\u0080-\u00bf\u2000-\u20ff]{2}", body))
    cjk = len(re.findall(r"[\u3400-\u9fff]", body))
    if replacement > max(4, len(body) * .005) or (len(body) > 200 and mojibake > max(5, len(body) * .025) and cjk < 8):
        flags.append("corrupted_encoding")
    if body and sum(char.isalpha() for char in body) < 80:
        flags.append("too_little_written_content")
    basis = str(row.get("content_basis") or "")
    if basis in {"headline_only", "headline_and_snippet", "article_summary", "abstract_only", "discovery_snippet"}:
        flags.append("partial_source")
    return {"quality_version": QUALITY_VERSION, "usable_complete_body": not flags,
            "flags": flags, "body_sha256": actual_hash if body else None,
            "characters": len(body), "source_language": language,
            "scope": "written_page", "automated_check_is_not_completeness_certification": True}


def evidence_chunks(text: str, *, limit: int = 6000, overlap: int = 240) -> list[dict[str, Any]]:
    """Cover every character; boundaries and hashes make omissions testable."""
    if limit <= overlap or overlap < 0:
        raise ValueError("Invalid evidence chunk size")
    result = []
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + limit // 2, end)
            if boundary > start:
                end = boundary + 1
        part = text[start:end]
        result.append({"start": start, "end": end, "text": part,
                       "sha256": hashlib.sha256(part.encode("utf-8")).hexdigest()})
        if end == len(text):
            break
        start = end - overlap
    return result
