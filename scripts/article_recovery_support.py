"""Publisher identity and article-specific extraction; never infer missing prose."""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

ARTICLE_SELECTORS = "[itemprop='articleBody'], .article-body, .story-body, .entry-content, .post-content, .field--name-body, .article-content, article"


def url_identity(url):
    p = urlsplit(str(url or ""))
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.startswith("utm_") and k not in {"fbclid", "gclid"}]
    return urlunsplit(("https", p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), urlencode(sorted(query)), ""))


@lru_cache(maxsize=1)
def publisher_extractor():
    import tldextract
    return tldextract.TLDExtract(suffix_list_urls=(), include_psl_private_domains=True)


def same_publisher(a, b):
    """Use the bundled public-suffix list, including private hosting domains."""
    extract = publisher_extractor()
    pa, pb = urlsplit(a), urlsplit(b)
    if pa.scheme not in {"http", "https"} or pb.scheme not in {"http", "https"}:
        return False
    if not pa.hostname or not pb.hostname or pa.username or pb.username:
        return False
    ea, eb = extract(pa.hostname), extract(pb.hostname)
    return bool(ea.top_domain_under_public_suffix and ea.top_domain_under_public_suffix == eb.top_domain_under_public_suffix)


def title_matches(a, b):
    def tokens(value):
        value = unicodedata.normalize("NFKC", value).casefold()
        # Character pairs also handle unsegmented Chinese titles.
        chars = "".join(re.findall(r"[\u3400-\u9fff]", value))
        return set(re.findall(r"[^\W_]{3,}", value, re.UNICODE)) if len(chars) < 6 else {chars[i:i+2] for i in range(len(chars)-1)}
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) >= 3 and len(ta & tb) / max(1, min(len(ta), len(tb))) >= .8


def page_title(html):
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one("meta[property='og:title']")
    if node and node.get("content"):
        return str(node["content"]).strip()
    node = soup.find("h1") or soup.title
    return node.get_text(" ", strip=True) if node else ""


def canonical_url(html, base):
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one("link[rel='canonical'][href]")
    return urljoin(base, str(node["href"])) if node else base


def article_objects(html, url=""):
    soup = BeautifulSoup(html, "html.parser")
    canonical = url_identity(canonical_url(html, url)) if url else ""
    def walk(obj, depth=0):
        if depth > 15:
            return
        if isinstance(obj, dict):
            types = obj.get("@type", [])
            types = [types] if isinstance(types, str) else types
            if any(str(t).split("/")[-1].lower() in {"article", "newsarticle", "blogposting", "scholarlyarticle", "report"} for t in types):
                ident = obj.get("url") or obj.get("mainEntityOfPage") or obj.get("@id")
                if isinstance(ident, dict):
                    ident = ident.get("@id") or ident.get("url")
                if not ident or not canonical or url_identity(urljoin(url, str(ident))) == canonical:
                    yield obj
            for key in ("@graph", "mainEntity"):
                yield from walk(obj.get(key), depth+1)
        elif isinstance(obj, list):
            for item in obj[:300]:
                yield from walk(item, depth+1)
    for node in soup.select("script[type='application/ld+json']"):
        try:
            yield from walk(json.loads(node.string or node.get_text()))
        except (ValueError, TypeError):
            continue


def visible_text(html, strip_layout=True):
    soup = BeautifulSoup(html, "html.parser")
    selector = "script, style, template, noscript, [hidden], [aria-hidden='true']"
    if strip_layout:
        selector += ", nav, footer, aside"
    for node in soup.select(selector):
        node.decompose()
    # A removed parent decomposes its children too. Visit children first so a
    # nested styled element is never inspected after its attrs became None.
    for node in reversed(soup.select("[style]")):
        if re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", node.get("style", ""), re.I):
            node.decompose()
    return soup.get_text(" ", strip=True)


def access_challenge(html):
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if re.search(r"^(?:just a moment[.!…]*|attention required.*|access denied|security verification|verify (?:that )?you are human|captcha)\s*$", title, re.I):
        return True
    if soup.select_one("form#challenge-form, #challenge-running, #cf-challenge-running"):
        return True
    text = visible_text(html)
    # A CAPTCHA mentioned in an article or loaded by a comment form is not a gate.
    return len(text) < 900 and bool(re.search(r"verify (?:that )?you are human|checking your browser|enable javascript and cookies to continue|access denied by security policy", text, re.I))


def paywall(html, url=""):
    for obj in article_objects(html, url):
        if obj.get("isAccessibleForFree") is False or str(obj.get("isAccessibleForFree")).lower() == "false":
            return True
        parts = obj.get("hasPart") or []
        parts = [parts] if isinstance(parts, dict) else parts
        if any(isinstance(p, dict) and str(p.get("isAccessibleForFree")).lower() == "false" for p in parts):
            return True
    # Gate copy must be visible; subscription code in unused JS is not evidence.
    text = visible_text(html, strip_layout=False)
    return bool(re.search(
        r"(?:subscribe|sign in|log in) to (?:continue(?: reading)?|read (?:the full|this) article)|"
        r"(?:cet article|la suite de cet article|contenu) est r[eé]serv[eé](?:e)? aux abonn[eé]s|"
        r"(?:la lecture des articles|cet article|la suite|ce contenu) (?:est |sont )?r[eé]serv[eé][e·.]*s? aux abonn[eé][e·.]*s|"
        r"(?:abonnez-vous|connectez-vous) pour (?:lire|continuer)|"
        r"(?:subscribe|subscription) (?:is )?required to (?:read|access)|"
        r"订阅后(?:可)?阅读|登录后阅读全文", text, re.I))


def html_text(fragment):
    soup = BeautifulSoup(str(fragment or ""), "html.parser")
    for node in soup.select("script, style, nav, footer, aside, form, .related-posts, .related-articles, .comments, #comments, [hidden]"):
        node.decompose()
    return "\n\n".join(line.strip() for line in soup.get_text("\n", strip=True).splitlines() if line.strip())


def abstract_only(html):
    soup = BeautifulSoup(html, "html.parser")
    if not soup.select_one("meta[name='citation_doi'], meta[name='citation_journal_title']"):
        return False
    abstract = soup.select_one("#abstract, #Abs1, .abstract, section[data-title='Abstract']")
    if not abstract:
        return False
    # A scholarly abstract is not the missing full paper. Open full-text
    # versions normally expose their subsequent sections as headings.
    headings = " ".join(node.get_text(" ", strip=True) for node in soup.select("h2, h3"))
    return not bool(re.search(r"introduction|methods|results|discussion|conclusion|方法|结果|讨论", headings, re.I))


def embedded_bodies(html, url=""):
    """Accept named article bodies, not arbitrary long 'text' from recommendations."""
    for obj in article_objects(html, url):
        if isinstance(obj.get("articleBody"), str):
            yield "jsonld_articleBody", html_text(obj["articleBody"])
    soup = BeautifulSoup(html, "html.parser")
    expected = page_title(html)
    def walk(obj, depth=0):
        if depth > 12:
            return
        if isinstance(obj, dict):
            headline = obj.get("headline") or obj.get("title")
            headline = headline.get("rendered") if isinstance(headline, dict) else headline
            matching = isinstance(headline, str) and title_matches(headline, expected)
            for key, value in obj.items():
                name = key.casefold().replace("_", "")
                if name in {"articlebody", "bodytext", "renderedbody", "articlecontent"} or (matching and name in {"body", "content"}):
                    if isinstance(value, str):
                        yield "embedded_json_article_body", html_text(value)
                    elif matching and isinstance(value, list):
                        chunks = [x.get("text") or x.get("content") for x in value if isinstance(x, dict) and x.get("type") in {"text", "paragraph", "heading", "html"}]
                        yield "embedded_json_article_blocks", html_text("\n".join(x for x in chunks if isinstance(x, str)))
                if isinstance(value, (dict, list)) and name not in {"related", "recommendations", "relatedarticles", "comments"}:
                    yield from walk(value, depth+1)
        elif isinstance(obj, list):
            for item in obj[:300]:
                yield from walk(item, depth+1)
    for node in soup.select("script[type='application/json'], script#__NEXT_DATA__"):
        try:
            yield from walk(json.loads(node.string or node.get_text()))
        except (ValueError, TypeError):
            continue


def alternate_links(html, base):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for node in soup.select("link[href]"):
        rel = set(node.get("rel") or [])
        mime = str(node.get("type") or "").lower()
        kind = ""
        if "canonical" in rel: kind = "publisher_linked_canonical"
        elif "amphtml" in rel: kind = "publisher_linked_amp"
        elif "https://api.w.org/" in rel: continue
        elif "alternate" in rel and mime == "application/json": kind = "publisher_linked_cms"
        elif "alternate" in rel and mime in {"application/rss+xml", "application/atom+xml"}: kind = "publisher_linked_feed"
        elif "alternate" in rel and mime == "application/pdf": kind = "publisher_linked_pdf"
        elif str(node.get("media") or "").lower() == "print" and "html" in mime: kind = "publisher_linked_print"
        if kind:
            out.append({"url": urljoin(base, node["href"]), "kind": kind})
    for node in soup.select("meta[name='citation_pdf_url'][content]"):
        out.append({"url": urljoin(base, node["content"]), "kind": "publisher_linked_pdf"})
    for node in soup.select("a[href]"):
        text = node.get_text(" ", strip=True)
        # Landing pages such as research.arizona.edu explicitly link the full story.
        if re.match(r"^(?:read (?:more|the (?:full |original )?(?:article|story))|lire (?:la suite|l['’]article)|阅读全文|查看原文)", text, re.I):
            out.append({"url": urljoin(base, node["href"]), "kind": "publisher_linked_full_story"})
    seen = {url_identity(base)}
    result = []
    for row in out:
        ident = url_identity(row["url"])
        if ident not in seen and same_publisher(row["url"], base):
            seen.add(ident)
            result.append(row)
    return result[:6]


def same_article(html, final_url, original_url, expected_title=""):
    if url_identity(canonical_url(html, final_url)) == url_identity(original_url):
        return True
    return bool(expected_title and title_matches(page_title(html), expected_title))
