# Recovery follow-up for the 31 August–6 September edition

The recovery workflow [34198662538](https://github.com/kedmahamelberg-ai/ai-empowerment-observatory/actions/runs/34198662538)
finished successfully. The uploaded pass-1 report records 17 newly stored
bodies: 64 previously available bodies became 81 of 139 sources in the repair
scope. All 75 missing sources received an attempt; 58 remained unavailable.

The refreshed [relationship release](https://github.com/kedmahamelberg-ai/ai-empowerment-observatory/blob/main/data/symbiosis/current.json)
was generated on 8 September and records 76 complete development readings
and 56 incomplete readings, compared with 61 and 71 in the earlier screenshot.
The 132 developments now comprise 14 benefits-only, 1 downsides-only,
3 mixed and 58 no-direction-stated readings, plus the 56 incomplete readings.
The 58 no-direction-stated readings are completed classifications, not missing
article bodies. They should be assessed separately when model quality is discussed.

The recovery scope includes one additional College of the Atlantic source
that is absent from the 138 sources listed in the public relationship release.
It already had a body. Source pages and distinct developments have different
denominators; do not compare 81 bodies directly with 76 development readings.
All 17 newly recovered source IDs appear in the refreshed public evidence.

## What the remaining 58 results actually show

| Report outcome | Sources | Next action |
| --- | ---: | --- |
| exception | 9 | Retry with the corrected HTML cleaner; retain a function/line diagnostic if another error occurs. |
| tdm_unavailable | 15 | Record the policy endpoint's HTTP status, parse error and retry history. This report omitted them. |
| robots_unavailable | 5 | Record the robots endpoint's status/error; retain normal bounded retries. |
| browser_data_unavailable | 1 | Radio-Canada video page; a complete written article/transcript has not been established. |
| blocked_paywall_or_login | 10 | A login/subscription requirement was detected. |
| blocked_robots | 8 | A robots restriction was reported. |
| blocked_tdm_reserved | 9 | A publisher text-mining reservation was detected. |
| blocked_access_control | 1 | The article request was refused. |

These are observed collector outcomes, not a guarantee that every detector
decision is correct. In particular, the original report does not establish
why the 20 policy checks were unavailable. A fresh test in this workspace
could not establish their GitHub-runner behavior. Do not label these 20 as
confirmed paywalls or count them as recoverable bodies yet.

## The reproduced defect and correction

`visible_text()` selected every element with an inline style, then removed a
hidden parent before inspecting its styled children. Beautiful Soup destroys
the child attributes when the parent is decomposed. Accessing the child then
raised `AttributeError: 'NoneType' object has no attribute 'get'`.

This exact exception was reproduced locally with nested hidden HTML. All nine
report exceptions have the same class and message, but the old report did not
retain their stack locations, so it cannot prove that every one came from this
function. The patch processes styled children before their parents and adds
regression coverage. Hidden elements remain excluded from visible-text checks;
visible paywalls still stop collection.

The library documents the destruction of removed elements and their contents
in its [decompose documentation](https://www.crummy.com/software/BeautifulSoup/bs4/doc/#decompose).

## Better diagnostics without changing publisher-access decisions

The collector now preserves each prerequisite's URL, HTTP status, check state,
error class/message and request attempts. The JSON recovery trace keeps the
retry history; the CSV exposes the key status/error columns. Unexpected parser
errors also retain the collector filename, function and line number, without
stack locals or article text. There is no database migration.

The [TDM Reservation Protocol](https://www.w3.org/community/reports/tdmrep/CG-FINAL-tdmrep-20240510/)
distinguishes site-wide files, response headers and document metadata. This
patch supplies the missing operational evidence; it does not convert a failed
check into permission or bypass publisher restrictions.

## How to apply and measure it

Install the five files from the follow-up package, commit and push main, then
start a new **Recover Missing Article Bodies and Refresh Observatory** run
with `release_id` set to **2026-W36**. Use **Run workflow** after the push.
The existing 81 bodies are reused; only missing sources are fetched. The
classifier reuses unchanged readings and processes newly recovered evidence.

The new strategy identifier also makes older failed attempts eligible for the
scheduled recovery jobs. The Monday pipeline and existing Tuesday, Wednesday
and Friday follow-ups use the corrected collector. No new paid service is used.

Success means the next report shows actual new bodies saved and the refreshed
published data shows additional complete readings. Zero parser exceptions is
a separate technical check. More bodies are not guaranteed: some of the nine
exceptions concern audio pages that may lack complete written content.

Keep the Brief and paid-model trial on hold until this Observatory follow-up
has been assessed. Do not restart the whole weekly collection to repair these
existing sources, and do not interpret a green workflow as 100% body coverage.
