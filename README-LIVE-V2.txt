AI Empowerment Observatory — current live architecture
======================================================

PUBLIC SOURCE OF TRUTH
----------------------
The public Observatory is release-bound.

Canonical current week:
  data/releases/current.json

Immutable weekly snapshots:
  data/releases/weekly/YYYY-Www.json

Weekly index:
  data/releases/index.json

Saved reporting/history series:
  data/history/releases.json

Period summaries:
  data/releases/monthly/
  data/releases/quarterly/
  data/releases/annual/
  data/releases/period-index.json

The site no longer treats data/lenses/latest.json or data/events/latest.json as
independent public sources of truth.

WEEKLY AUTOMATION
-----------------
The single scheduled entry point is:
  .github/workflows/weekly-observatory.yml

Schedule:
  Monday 00:17 UTC

Order:
  collection
  -> translation
  -> event resolution
  -> longitudinal reconciliation
  -> dual-lens classification
  -> Stage 7C finalization
  -> weekly release
  -> same-release relationship boundary
  -> period summaries
  -> public insights/history
  -> public PDF brief
  -> final release validation
  -> one GitHub Pages deployment

Every heavy stage is a separate reusable GitHub Actions job. There is no longer
one parent runner waiting for a chain of dispatched workflows.

DATA INTEGRITY
--------------
- Discovery uses an 8-day query buffer.
- The weekly release itself is filtered to the previous complete Monday-Sunday.
- Transient SerpAPI failures are retried per market.
- A configured-market collection failure blocks publication rather than
  publishing a partial five-market release.
- The final gate rejects stale or mixed-release public derivatives.
- Each weekly release is retained for later trend/newsletter/report analysis.
- Monthly, quarterly and annual summaries rebuild after every accepted week.

RELATIONSHIP LENS
-----------------
The relationship/symbiosis layer remains human-governed.
A new weekly release immediately receives a same-release artifact marked
review_in_progress if human review is incomplete. The frontend suppresses stale
relationship numbers from older releases.

After the core weekly pipeline succeeds, Prepare Weekly Symbiosis Review runs
separately to create the model workbench. Reviewed relationship numbers can be
published later without blocking the core weekly update.

TODAY / RECOVERY
----------------
For the complete 31 August 2026 installation and W35 recovery instructions, see:
  OBSERVATORY-AUTOMATION-FIX-2026-08-31.md
