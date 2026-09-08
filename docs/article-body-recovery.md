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
5. If the public page still has no body, render it in isolated Chromium for up
   to 65 seconds. No login, cookie-consent clicks, challenge solving, proxy,
   identity rotation or paid retrieval service is used. Private addresses and
   unrelated article-data origins are blocked. A failed renderer is reported.

Robots and explicit TDM restrictions remain in effect. An actual paywall or
access-control response ends that source's attempt. This patch does not claim
that every source can be collected. Unavailable policy checks stay recorded;
they are not treated as permission. Missing bodies and real restrictions stay
in the report, with their source links.

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
- [WordPress Posts REST API](https://developer.wordpress.org/rest-api/reference/posts/)
  documents the public post URL, content, excerpt and publication-status fields.
- [Schema.org NewsArticle](https://schema.org/NewsArticle) documents articleBody
  and article-level metadata used to select the matching source.
- [W3C TDM Reservation Protocol](https://www.w3.org/community/reports/tdmrep/CG-FINAL-tdmrep-20240510/)
  was checked when retaining the publisher-reservation handling.
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
any production snapshot writes. Live article recovery and deployment must run
in the owner's GitHub account using its existing Supabase secrets.
