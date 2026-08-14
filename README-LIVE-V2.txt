AI Empowerment Observatory — Live v2 UX + history + source transparency
===========================================================================

WHY 129 ARTICLES VS 124 UNIQUE DEVELOPMENTS?
--------------------------------------------
That is a real empirical result for the current release:
  129 AI-relevant articles
  124 unique AI developments
  5 extra article instances above the unique-development count

It means repeated coverage was low in this one observation window.
The value of Coverage vs Event becomes much clearer through time, so this
package adds a weekly Google-Trends-style history rather than forcing a large
difference into a week where one does not exist.

WHAT THIS PACKAGE ADDS
----------------------
1. WEEKLY TREND
   data/history/releases.json accumulates one point per classification release.
   It is NOT cumulative article volume.
   Public buttons show the last 4 releases, last 12, or all history.

2. SOURCE / PUBLICATION LIST
   data/insights/latest.json dynamically lists every publication or
   organisation represented in the latest AI-relevant Coverage Lens, with:
   - article count
   - number of unique developments

   Important:
   This is NOT a fixed journal whitelist. Google News discovery is dynamic.

3. NON-EMPOWERMENT BREAKDOWN
   Shows what non-empowerment developments are about by topic, separately for:
   - unique developments
   - news articles

   Public copy explicitly explains that non-empowerment does not mean
   "no AI impact"; it means the available evidence does not establish a
   direct human-empowerment change.

4. GLOBE RESTORED
   The interactive MapLibre globe returns.
   It no longer contains fictional Netherlands/Brazil/USA scores.
   Hollow markers show the five NEWS DISCOVERY markets only.
   Evidence-ready country scores remain threshold-gated.

5. HIGH-SCHOOL-LEVEL LANGUAGE
   The public hierarchy now leads with:
   - News volume
   - Unique developments
   - Extra coverage
   - Media amplification gap

   Formal terms (Coverage Lens / Event Lens) remain secondary labels.

6. STRONGER CTAs
   Adds:
   - About Kedma / research link
   - current 3-page Pulse
   - quarterly report signup
   - executive briefing / training CTA

7. PRO POSITIONING
   Public stays free:
   - current signal
   - recent trend
   - source list
   - evidence
   - methodology
   - quarterly public reports

   Pro sells:
   - full historical archive
   - saved/custom comparisons
   - monitoring/watchlists
   - alerts
   - exports/API
   - organisation-specific briefs/workflows

8. WEEKLY AUTONOMY
   Adds "Generate Public Observatory Insights" to the automated weekly chain.
   Adds "Generate Public Observatory Brief" before publication.
   Also fixes the workflow_run recursion risk: push-triggered Pages rebuilds
   no longer launch another weekly processing chain.

INSTALL
-------
Upload/replace these files at their exact paths:

  edu/index.html
  edu/dashboard.js
  edu/map.js
  edu/observatory.css

  index.html
  site.css

  pro/index.html
  report/index.html

  scripts/generate_public_insights.py
  scripts/build_public_site.py
  scripts/orchestrate_weekly.py

  .github/workflows/generate-public-insights.yml
  .github/workflows/weekly-observatory.yml

  data/insights/latest.json
  data/history/releases.json

Commit:
  Add public trends, globe, sources and plain-language UX

FIRST RUN
---------
1. Actions
   -> Generate Public Observatory Insights
   -> Run workflow
   -> main

This populates:
  data/insights/latest.json
  data/history/releases.json

2. Actions
   -> Generate Public Observatory Brief
   -> Run workflow
   -> main

3. Actions
   -> Publish Observatory Release
   -> Run workflow
   -> main

AFTER THAT
----------
The existing scheduled Update AI News Collection starts the autonomous weekly
pipeline. Each successful weekly release appends/replaces its own history point.

The site therefore shows:
- the CURRENT week as the main snapshot
- selectable RECENT WEEKLY HISTORY as a trend
- not an ever-growing cumulative article total

A true rolling 30/90/365-day unique-event aggregation should be added only
after enough cross-week history exists and event identity across windows is
validated. Do not fake it by summing weekly event counts.
