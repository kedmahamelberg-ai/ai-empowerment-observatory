"""Bounded cookie consent, using real visible controls in an isolated browser.

The owner authorized cookie acceptance for article collection. This module never
signs in, subscribes, solves challenges, removes overlays or invents cookies.
Known CMP controls work independently of language; other buttons need BOTH a
cookie/privacy dialog and an explicitly recognized acceptance label.
"""
from __future__ import annotations

import re
import time
import unicodedata
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

BUTTONS = "button, [role='button'], input[type='button'], input[type='submit'], a"
KNOWN_ACCEPT = (
    "#onetrust-accept-btn-handler, #didomi-notice-agree-button, "
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll, "
    "#CybotCookiebotDialogBodyButtonAccept, #acceptAllMain, "
    "#tarteaucitronPersonalize2, .qc-cmp2-summary-buttons button[mode='primary'], "
    "button.sp_choice_type_11, button[data-testid='uc-accept-all-button'], "
    "button[data-cookiefirst-action='accept'], button[data-cc='accept-all']"
)
CONTAINERS = (
    "[role='dialog'], [aria-modal='true'], dialog, #onetrust-banner-sdk, "
    "#didomi-host, #CybotCookiebotDialog, #tarteaucitronRoot, .qc-cmp2-container, "
    ".sp_message_container, [id*='cookie' i], [class*='cookie' i], "
    "[id*='consent' i], [class*='consent' i], [id*='privacy' i], [class*='privacy' i]"
)
COOKIE_WORDS = (
    r"cookie|cookies|traceurs|confidentialit[eé]|vie priv[eé]e|privacy|datenschutz|"
    r"privacidad|privacidade|riservatezza|personuppgifter|ev[aä]ste|informasjonskaps|"
    r"soubory|ciastecz|s[uü]ti|[cç]erez|конфиденциальност|файлы куки|"
    r"隐私|隱私|個人情報|クッキー|쿠키|개인정보|ملفات تعريف|الخصوصية|עוגיות|"
    r"คุกกี้|ความเป็นส่วนตัว|कुकी|quyền riêng tư|privasi"
)
# Full labels, not substring searches: "accept and subscribe" must never match.
LABELS = """
accept|accept all|accept all cookies|accept cookies|i accept|i agree|agree|agree and close|agree & close|agree and continue|accept & close|accept and close|accept and continue|allow all|allow all cookies|yes i agree
accepter|tout accepter|j'accepte|accepter tous les cookies|accepter les cookies|accepter & fermer|accepter et fermer|accepter et continuer|j'accepte tout
aceptar|aceptar todo|aceptar todas|aceptar todas las cookies|aceptar cookies|aceptar y cerrar|aceitar|aceitar todos|aceitar tudo|aceitar todos os cookies|aceitar e fechar
akzeptieren|alle akzeptieren|alle cookies akzeptieren|zustimmen|alle zulassen|akzeptieren und schließen
accetta|accetta tutto|accetta tutti|accetta tutti i cookie|accetta e chiudi|acconsento
accepteren|alles accepteren|alle cookies accepteren|akkoord|ik ga akkoord|accepteer alles
acceptera|acceptera alla|godkänn alla|accepter alle|acceptér alle|godta alle|godta|hyväksy|hyväksy kaikki|samþykkja allt
akceptuję|akceptuj|akceptuj wszystkie|zaakceptuj wszystkie|přijmout|přijmout vše|prijať všetky|elfogadom|összes elfogadása|acceptă toate|accept toate
tümünü kabul et|kabul et|tüm çerezleri kabul et|αποδοχή|αποδοχή όλων|принять|принять все|прийняти всі|погоджуюсь
接受|全部接受|接受全部|接受所有|接受所有cookie|接受所有 cookies|同意|全部同意|同意并继续|同意並繼續|すべて同意|すべて同意する|同意する|모두 동의|동의|모두 허용
قبول|قبول الكل|أوافق|موافق|מסכים|אישור הכל|ยอมรับ|ยอมรับทั้งหมด|स्वीकार करें|सभी स्वीकार करें|chấp nhận|chấp nhận tất cả|terima|terima semua|setuju
"""
ESSENTIAL_LABELS = "accept essential|accept essential cookies|necessary only|essential only"
DENY = re.compile(
    r"subscrib|abonn|s'abonner|pay\b|payment|purchase|buy\b|sign.?in|log.?in|register|"
    r"connecter|inscri|suscri|susscri|registr|acheter|payer|compra|zahlung|anmelden|"
    r"download|notification|订阅|訂閱|登录|登入|购买|付款|구독|로그인|"
    r"اشتراك|دفع|הרשמ|תשלום", re.I)


def normalized(value):
    text = unicodedata.normalize("NFKD", str(value or "").casefold().replace("’", "'"))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.split()).strip(" .!✓✔→»")


ACCEPT_LABELS = {normalized(x) for line in LABELS.strip().splitlines() for x in line.split("|")}
ESSENTIAL = {normalized(x) for x in ESSENTIAL_LABELS.split("|")}


def acceptance_method(label, known=False):
    if DENY.search(str(label or "")):
        return None
    if known:
        return "cmp_control"
    if normalized(label) in ACCEPT_LABELS:
        return "translated_accept"
    if normalized(label) in ESSENTIAL:
        return "essential_accept"
    return None


# Third-party documents/data are limited to established consent providers.
# This list does not grant access to article content on other publishers.
CMP_DOMAINS = (
    "privacy-mgmt.com", "consentmanager.net", "consentmanager.de", "didomi.io",
    "cookielaw.org", "onetrust.com", "cookiebot.com", "quantcast.mgr.consensu.org",
    "cmp.quantcast.com", "trustarc.com", "axept.io", "usercentrics.eu",
    "usercentrics.com", "cookiepro.com", "consent.trustarc.com",
)


def cmp_url(url):
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    return p.scheme in {"https", "http"} and not p.username and any(
        host == domain or host.endswith("." + domain) for domain in CMP_DOMAINS)


def consent_endpoint(url):
    return bool(re.search(r"(?:^|[/_.-])(?:consent|cookies?|privacy)(?:[/_.-]|$)", urlsplit(url).path, re.I))


def needs_consent_browser(html):
    """Detect consent UI/bootstrap before static text is labelled complete.

    Hidden templates are not visible consent. A known external CMP script or
    frame needs a browser because its dialog is absent from the initial HTML.
    """
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select("script[src], iframe[src]"):
        if cmp_url(node.get("src", "")):
            return True
    for node in soup.select("script, style, template, noscript, [hidden], [aria-hidden='true']"):
        node.decompose()
    for node in reversed(soup.select("[style]")):
        if re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", node.get("style", ""), re.I):
            node.decompose()
    if soup.select_one(KNOWN_ACCEPT):
        return True
    for node in soup.select(CONTAINERS):
        if node.select_one(BUTTONS) and re.search(COOKIE_WORDS, node.get_text(" ", strip=True), re.I):
            return True
    return False


# Read-only DOM inspection. Playwright locators pierce open shadow roots; walking
# through host ancestors also recognizes dialogs inside web components.
INSPECT_BUTTONS = r"""(nodes, cfg) => {
  const words = new RegExp(cfg.words, 'i');
  const parent = n => n.parentElement || n.getRootNode().host;
  const visible = n => {
    if (!n.getClientRects().length) return false;
    for (let p=n; p; p=parent(p)) {
      const s=getComputedStyle(p);
      if (p.hidden || p.getAttribute('aria-hidden')==='true' || s.display==='none' ||
          s.visibility==='hidden' || s.opacity==='0') return false;
    }
    return true;
  };
  return nodes.map((n, index) => {
    const known = n.matches(cfg.known);
    let context = known;
    for (let p=parent(n); p && !context; p=parent(p)) {
      if (p.matches('article, nav, footer')) break;
      if (p.matches(cfg.containers) && words.test(p.innerText || p.textContent || '')) context=true;
    }
    return {index, known, context, visible:context && visible(n),
      label: (n.getAttribute('aria-label') || n.innerText || n.value || '').trim().slice(0,160),
      disabled: n.disabled || n.getAttribute('aria-disabled')==='true'};
  }).filter(n => n.visible && n.context).slice(0,100);
}"""
INSPECT_CONFIG = {"words": COOKIE_WORDS, "known": KNOWN_ACCEPT, "containers": CONTAINERS}


def inspect_frame(frame):
    return frame.locator(BUTTONS).evaluate_all(INSPECT_BUTTONS, INSPECT_CONFIG)


def accept_cookie_consent(page, *, timeout_ms=10000, initial_wait_ms=3500, settled_ms=1200, before_click=None, max_clicks=3):
    """Accept at most three dialogs and verify that the consent controls close.

    No force clicks, JS clicks, preference-form filling or modal deletion. The
    caller can use before_click to authorize only consent network submissions.
    Diagnostics contain action types, never cookie values or page prose.
    """
    from playwright.sync_api import Error as BrowserError
    started = time.monotonic()
    deadline = started + timeout_ms / 1000
    quiet_since = None
    actions = []
    attempted = set()
    seen = False
    pending = False
    while time.monotonic() < deadline:
        pending = False
        clicked = False
        frames = sorted(list(page.frames), key=lambda f: 0 if f == page.main_frame else 1 if cmp_url(f.url) else 2)
        for frame in frames[:50]:
            try:
                # Detached/hidden consent frames are not an unresolved dialog.
                if frame != page.main_frame and not frame.frame_element().is_visible():
                    continue
                rows = inspect_frame(frame)
                pending = pending or bool(rows)
                seen = seen or bool(rows)
                candidates = [(acceptance_method(r['label'], r['known']), r) for r in rows if not r['disabled']]
                candidates = sorted((c for c in candidates if c[0]), key=lambda c: c[0] == 'essential_accept')
                for method, row in candidates:
                    key = (id(frame), row['index'], row['label'])
                    if key in attempted or len(actions) >= max_clicks:
                        continue
                    attempted.add(key)
                    control = frame.locator(BUTTONS).nth(row['index'])
                    # Re-read the selected control after discovery/re-rendering.
                    fresh = control.evaluate_all(INSPECT_BUTTONS, INSPECT_CONFIG)
                    if not fresh or fresh[0]['label'] != row['label'] or not fresh[0]['visible']:
                        continue
                    if before_click:
                        before_click(frame)
                    control.click(timeout=min(1500, max(1, int((deadline-time.monotonic())*1000))))
                    actions.append({'method': method, 'context': 'main' if frame == page.main_frame else 'frame'})
                    clicked = True
                    break
            except BrowserError:
                # A failed click/removed frame is re-inspected; never force it.
                pending = True
            if clicked:
                break
        now = time.monotonic()
        if pending or clicked:
            quiet_since = None
        else:
            quiet_since = quiet_since or now
            if now-started >= initial_wait_ms/1000 and now-quiet_since >= settled_ms/1000:
                break
        page.wait_for_timeout(min(200, max(1, int((deadline-now)*1000))))
    return {'status': 'unresolved' if pending else ('accepted' if actions else 'not_present'),
            'detected': seen, 'click_count': len(actions), 'actions': actions}
