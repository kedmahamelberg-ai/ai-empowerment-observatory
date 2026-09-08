# Article body recovery, 8 September 2026

This update repairs article acquisition. It keeps the existing classification
model and research codebook. Recovering evidence can change a subsequent
reading; a missing source must never be converted to a neutral classification.

## What the W36 investigation established

The collection job in run 34083747595 inspected 148 candidate source pages.
It reported 72 usable bodies and 76 missing bodies before publication-date
filtering. The published edition contains 138 pages and 132 developments.
The public relationship report identifies 71 developments with no complete
written source. Those are different denominators: developments and pages must
not be confused.

The collection log recorded 27 `blocked_bot_challenge` results. The old detector
searched all HTML for the word `captcha`, including comment widgets and script
URLs. Similarly, generic subscription-widget strings could trigger a paywall
result. These patterns could block accessible stories, and a previous block
was then skipped forever in the default retry mode. The update tests the
actual page-level challenge or article-specific access gate.

Public-page checks found readable stories among previously flagged URLs,
including [ASU News](https://news.asu.edu/b/20260903-asu-professor-finds-term-artificial-intelligence-1588-book)
and [Trethowans](https://www.trethowans.com/insights/understanding-ai-a-practical-guide-for-businesses-2/).
That does not prove every challenge was a false positive: responses can differ
by client and time. The [Arizona research landing page](https://research.arizona.edu/news/study-generative-ai-succumbs-conversational-misinformed-pressure-and-argument)
explicitly links to the full article on [University of Arizona News](https://news.arizona.edu/news/study-generative-ai-succumbs-conversational-misinformed-pressure-and-argument).
The new collector can follow this kind of publisher link and verify the story's
identity instead of treating the landing page as an empty article.

## Retrieval sequence

1. Fetch the original publisher URL with bounded transport retries, checked
   redirects and original-language charset decoding. Charset declarations now
   match actual whitespace instead of a literal backslash followed by `s`.
2. Extract the article from its semantic HTML, matched article metadata, named
   application-data body fields, and Trafilatura's precision/recall cascade.
   Arbitrary long analytics, recommendations and application `text` fields
   cannot become article evidence.
3. For an accessible page without a body, try explicitly linked canonical,
   AMP, full-story, feed, CMS and PDF representations on the same publisher's
   registered domain. The public-suffix list also separates private hosts such
   as different `github.io` sites. There is no guessed mirror or archive URL.
4. Accept a feed's full-content field or a public WordPress post only for the
   exact article URL. Excerpts, feed descriptions and unrelated recommendations
   are rejected. Scholarly abstracts are not full papers; linked PDFs must have
   usable text on every page. Scanned PDFs require OCR/manual review and stay
   explicitly unresolved in this version.
5. When a cookie dialog or known consent system is detected, render the original
   page first and accept its actual visible consent control. Also use Chromium
   for an accessible page that still has no body. The renderer gets at most 65
   seconds. Private addresses and unrelated article-data origins are blocked.
   A failed renderer is reported.

## Cookie acceptance (8 September follow-up)

The owner explicitly requested automatic cookie acceptance. The collector now
uses known consent controls independently of their displayed language, followed
by exact translated acceptance labels in a cookie/privacy dialog. It supports
the supplied “Accepter & fermer”, “Accepter” and “I Accept” examples, as well as
English, French, Chinese and many other language variants. This is not a claim
that every custom banner or language is recognized. Unrecognized/stuck dialogs
receive `consent_unresolved` instead of contributing banner prose to evidence.

The browser inspects the main document, embedded consent frames and open shadow
roots. It waits for delayed dialogs, clicks at most three consent controls,
checks that they close and waits for article rendering. The publisher's actual
click handler creates any consent cookie; the collector never fabricates one
or removes a modal. Browser cookies are discarded with the isolated source
context and are not exported, logged or reused as owner login credentials.

Established consent-provider frames and data endpoints can load. Consent
submissions are allowed after a recognized acceptance click; provider message
and configuration requests can initialize the dialog before it. Same-article
reloads are supported after acceptance, with fresh robots/TDM checks. Other
navigation, login, subscription/payment submission and challenge solving remain
outside this collector. Original article access checks and every post-consent
paywall, publisher reservation and source-quality check still apply.

The Mediapart screenshot shows a subscriber-only preview even after consent.
Its inclusive French wording is now explicitly recognized. Such a preview stays
excluded from complete-content totals and from the eligible Brief export.

The latest supplied recovery artifact reported 90 usable bodies from 139 target
sources, with 49 unresolved. It recorded L'Alsace and La Dépêche as
`blocked_tdm_reserved`; a consent click does not clear that independent publisher
restriction. These are baseline findings, not measured results of this update.
The new report includes `consent_status` and `consent_clicks` per source plus a
body-free action trace, so the next run can measure actual gains.

Robots and explicit TDM restrictions remain in effect. An actual paywall or
access-control response ends that source's attempt. This patch does not claim
that every source can be collected. Missing bodies and real restrictions stay
in the report, with their source links.

## Optional declaration discovery (8 September, v8)

Recovery run 34263872439 completed with zero new bodies: 90 of 139 targets
still had usable bodies, and 49 remained unresolved. Its 36 acquisition tests
and 24 consent tests passed in GitHub, including the real Chromium cases.
Cookie handling therefore passed its regression gate, but did not increase
coverage in that run. The collector needs to reach an article before its
cookie handling can help.

The per-source diagnostic report from run 34220178757 identified these earlier
failures; the latest run repeated the same 15 TDM and four robots outcomes:

| Discovery response | Sources | v8 behavior |
| --- | ---: | --- |
| Optional `/.well-known/tdmrep.json` returned 403 | 9 | Record `not_implemented` / reservation `unset`, then check the article |
| Optional declaration returned 400 | 1 | Same handling for a definitive client-error response |
| Optional declaration returned 200 but could not be parsed as JSON | 5 | HTML/plain-text fallback pages mean no declaration; malformed declared JSON still defers |
| `robots.txt` returned 406 | 3 | Record `unavailable_4xx`, then check the article |
| Robots request failed TLS verification | 1 | Keep deferred; certificate verification stays enabled |

The first 18 sources are candidates for additional attempts, not 18 confirmed
recoveries. Once reached, a source may still return a real access refusal,
reservation, subscription preview, incomplete paper or insufficient body.

[TDMRep section 6.1](https://www.w3.org/community/reports/tdmrep/CG-FINAL-tdmrep-20240510/)
treats failure to provide a machine-readable site-wide declaration as absence
of that protocol implementation. The previous collector conflated this optional
discovery file with the separate linked licensing policy in section 5.2.
It stopped before requesting the article on any discovery 403 or non-JSON
200 response. v8 keeps an absent declaration as `unset`, never as an affirmative
licence. Article HTTP responses and TDM header/meta declarations are still
checked, including after consent and on redirects. Explicit reservations still
stop collection. This change does not fetch or assume terms for a linked
`tdm-policy` licensing document.

[RFC 9309 section 2.3.1.3](https://www.rfc-editor.org/rfc/rfc9309.html#section-2.3.1.3)
allows retrieval after a robots 4xx, which includes the observed Nature 406s.
The collector retains its conservative refusal on robots 401/403. Retryable
408/425/429, server errors, TLS/network failures, invalid JSON declarations and
unresolved redirects still defer. No request identity, proxy, credentials,
certificate setting or publisher access rule is changed.

The existing report fields preserve each discovery HTTP status, check state,
JSON error class and body-free recovery trace. The new strategy version retries
older failures while reusing saved usable bodies. These changes are in the
shared collector used by both current-week recovery and future weekly runs.
No new API service or paid model is needed. Complete-content denominators and
the Brief's eligible source export still require complete evidence.

## Persistence, timing and recovery

Every saved body stays in the existing private Supabase evidence table with
the article ID, original-language text, content hash, publisher URL and method.
The source text is not copied into public reports or GitHub artifacts. New bytes
are staged before the old current snapshot is cleared; write failures cause a
failed job, not a fabricated successful recovery. No database migration is
required.

The collector skips usable saved bodies. An older collector's failure is
rechecked once under the new strategy. Thereafter technical failures have a
six-hour retry cooldown and access/policy blocks a seven-day cooldown. Manual
`all` rechecks every unresolved source while still enforcing current access
decisions. Within one workflow run, three bounded passes each get 40 minutes
and never repeatedly spend their budget on the same attempted source. One
source gets at most 150 seconds. Successful progress survives an interrupted
job. A remaining unattempted backlog fails the job with a continuation message.

`enrich-new-brief-article-bodies.yml` is used before the weekly classification.
`recover-article-bodies.yml` repairs the current published edition manually and
on Tuesday, Wednesday and Friday at 03:37 UTC. The entire repair is serialized
with the weekly pipeline. It then uses the existing classifier with
`replace: false`: unchanged successful input fingerprints remain reusable,
while newly recovered evidence receives an updated reading. The existing
publication gates must pass before the refreshed Observatory is deployed.

The scheduled and manual repair do not switch to a paid model. They do not
recollect the news, change publication dates, edit accepted human labels or
delete prior evidence. The Brief can import the updated public edition after
the Observatory publishes it.

Each recovery artifact contains JSON and CSV records for every targeted page:
availability, recovered-this-pass flag, latest attempt, extraction method,
final URL, error reason and a body-free trace. A green collection job means
the acquisition pass completed and persisted its results; the report, not the
job colour, establishes how many bodies are available.

## Research used

- [Trafilatura extraction cascade, precision and recall](https://trafilatura.readthedocs.io/en/latest/usage-python.html)
  informed the extraction fallbacks and article-container checks. The repository
  already used Trafilatura; installing a different model would not fix fetches.
- [Playwright Python network controls](https://playwright.dev/python/docs/network)
  informed the bounded renderer and request interception.
- [Playwright frames](https://playwright.dev/python/docs/frames),
  [locators and shadow DOM](https://playwright.dev/python/docs/locators), and
  [actionability checks](https://playwright.dev/python/docs/actionability)
  informed consent-frame discovery, real locator clicks and visibility checks.
- [WordPress Posts REST API](https://developer.wordpress.org/rest-api/reference/posts/)
  documents the public post URL, content, excerpt and publication-status fields.
- [Schema.org NewsArticle](https://schema.org/NewsArticle) documents articleBody
  and article-level metadata used to select the matching source.
- [W3C TDM Reservation Protocol](https://www.w3.org/community/reports/tdmrep/CG-FINAL-tdmrep-20240510/)
  sections 6.1 and 5.2 distinguish optional declaration discovery from a linked
  licensing policy. v8 corrects the earlier overbroad failure handling.
- [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html#section-2.3.1.3)
  distinguishes unavailable robots resources from unreachable servers.
- [Unpaywall API](https://unpaywall.org/products/api) was considered for research
  papers. It is not enabled in this patch: replacing a news article with the
  underlying paper would not recover the same source, and authorized scholarly
  copies need separate DOI/version and license provenance.

## Validation and limits

The package's VALIDATION.json records the final local test results. Regression
fixtures cover false blockers, genuine gates, languages, identity, full feeds,
public CMS content, abstracts/PDF gaps, deadlines, retries and database failures.
They do not constitute a live recovery count. The GitHub enrichment workflow
installs Chromium and requires a real JavaScript-rendering smoke test before
any production snapshot writes. It also now requires the multilingual consent,
iframe, shadow-root, delayed/stuck banner and subscriber-preview browser tests.
The package records local skips separately; browser tests are not represented
as passed when no usable test browser is available. Live recovery and deployment must run
in the owner's GitHub account using its existing Supabase secrets.
