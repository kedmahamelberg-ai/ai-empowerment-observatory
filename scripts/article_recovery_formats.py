"""Read publisher-provided full feeds, CMS records and born-digital PDFs."""
from __future__ import annotations

import io
import json
import re
from xml.etree import ElementTree as ET

from article_recovery_support import html_text, url_identity


def publisher_content(raw, mime, original_url):
    """Return full content of the exact linked article; never accept a summary."""
    if "json" in mime:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
        rows = data if isinstance(data, list) else [data]
        for row in rows[:100]:
            if not isinstance(row, dict) or row.get("status") != "publish":
                continue
            if url_identity(row.get("link")) != url_identity(original_url):
                continue
            content = row.get("content") or {}
            if not isinstance(content, dict) or content.get("protected") is True or row.get("password"):
                continue
            body = content.get("rendered")
            if isinstance(body, str) and body.strip():
                title = row.get("title") or {}
                return {"html": body, "text": html_text(body), "title": html_text(title.get("rendered", "")), "method": "publisher_cms_full_content"}
        return None
    if not any(s in mime for s in ("xml", "rss", "atom")):
        return None
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)", raw, re.I):
        return None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    atom = "{http://www.w3.org/2005/Atom}"
    for entry in list(root.iter("item")) + list(root.iter(atom+"entry")):
        urls = [entry.findtext("link", "")]
        urls.extend(node.get("href", "") for node in entry.findall(atom+"link") if node.get("rel", "alternate") == "alternate")
        if url_identity(original_url) not in {url_identity(u) for u in urls if u}:
            continue
        content = entry.find("{http://purl.org/rss/1.0/modules/content/}encoded")
        if content is None:
            content = entry.find(atom+"content")
        if content is None or content.get("src"):
            continue
        fragment = (content.text or "") + "".join(ET.tostring(child, encoding="unicode") for child in content)
        if not fragment.strip():
            continue
        return {"html": fragment, "text": html_text(fragment), "title": entry.findtext("title") or entry.findtext(atom+"title", ""), "method": "publisher_feed_full_content"}
    return None


def pdf_content(raw):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(raw), strict=False)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDF requires publisher access")
    if len(reader.pages) > 120:
        raise ValueError("PDF exceeds the 120-page recovery budget")
    pages = [page.extract_text() or "" for page in reader.pages]
    # Do not describe a partial OCR-less result as the full document.
    if not pages or any(len(re.sub(r"\W", "", page)) < 25 for page in pages):
        raise ValueError("PDF has pages without usable text; OCR/manual review is required")
    return {"text": "\n\n".join(pages), "pages": len(pages), "method": "publisher_pdf_full_text"}
