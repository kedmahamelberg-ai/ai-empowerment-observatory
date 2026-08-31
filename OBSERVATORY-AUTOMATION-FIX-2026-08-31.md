# AI Empowerment Observatory automation fix

**Prepared:** 31 August 2026  
**Current repository snapshot in this package:** 2026-W34, 17–23 August 2026  
**Release that the first live run must create:** **2026-W35, 24–30 August 2026**

## What this fix changes

The Observatory now has one weekly pipeline instead of one long Python process that launches and waits for many other GitHub Actions workflows.

The weekly chain is:

1. Collect Google News discovery results.
2. Translate/normalize headlines.
3. Resolve articles into distinct developments.
4. Reconcile developments with the longitudinal event history.
5. Classify the Coverage and Event lenses.
6. Apply the Stage 7C residual rule.
7. Build the immutable weekly release.
8. Create a relationship-lens artifact tied to the same release. If human review is not complete, it is marked `review_in_progress` and old relationship numbers are not shown as current.
9. Rebuild monthly, quarterly and annual summaries.
10. Generate public source/theme insights and the saved weekly history.
11. Generate the current PDF Pulse from that same weekly release.
12. Validate every public derivative against the same release ID and counts.
13. Deploy GitHub Pages **once**, only after the validation gate passes.

The older benchmark, calibration, QA and repair workflows have also had their Pages deployment steps removed. They can still generate/commit their internal outputs, but **only `Publish Observatory Release` can deploy the live Observatory**. This prevents a manual QA run from redeploying a stale site.

The canonical public source of truth is now:

`data/releases/current.json`

Every weekly snapshot is preserved at:

`data/releases/weekly/YYYY-Www.json`

The standardized trend history used later for reports/newsletters is rebuilt from the immutable release index at:

`data/history/releases.json`

The period rollups are preserved at:

- `data/releases/monthly/`
- `data/releases/quarterly/`
- `data/releases/annual/`
- `data/releases/period-index.json`

The private source/news history in Supabase remains intact. Nothing in this fix couples the Observatory to the separate News Brief repository.

---

# Do this today

## 1. Put the fixed files into the repository

Use the **full fixed package** supplied with this document, or copy the files from the patch package over the repository root. Preserve the existing `.git` folder if you are replacing files in a local clone.

Commit all changes to `main` with a message such as:

`Fix weekly Observatory automation and release consistency`

Then push `main` to GitHub.

## 2. Confirm the required GitHub Actions secrets

In GitHub open:

**Repository → Settings → Secrets and variables → Actions**

Confirm these existing repository secrets are present:

- `SERPAPI_KEY`
- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`

The reconciliation workflow can also use these if already configured:

- `SUPABASE_SERVICE_ROLE_KEY`
- `HF_TOKEN`

Do not paste secret values into workflow files.

## 3. Start this week's run manually

In GitHub open:

**Actions → Weekly Observatory Pipeline → Run workflow → main → Run workflow**

This is the **only workflow you need to start for the core weekly update**. Do not manually launch Collect, Translate, Resolve, Classify, Build Release, Insights, Brief, or Publish while this pipeline is running.

Because today is Monday 31 August 2026, the build gate expects:

- `release_id`: `2026-W35`
- `period_start`: `2026-08-24`
- `period_end`: `2026-08-30`

The discovery query now uses an 8-day buffer. That matters for a manual Monday-afternoon recovery run because it still captures early Monday 24 August candidates. The public release builder then deterministically keeps only 24–30 August.

## 4. Watch the job chain

The GitHub Actions page should show these jobs in order:

`collect` → `translate` → `resolve-events` → `reconcile-history` → `classify-dual-lenses` → `finalize-residual` → `build-weekly-release` → `relationship-release-boundary` → `period-summaries` → `public-insights` → `public-brief` → `publish-site`

Heavy model stages have their own GitHub runner. They are no longer forced to finish inside one parent runner.

The collection stage retries transient SerpAPI failures up to three times per market. If a configured market still fails, the private partial run is retained but the public pipeline stops instead of publishing a misleading incomplete five-market week.

## 5. Verify W35 before trusting the website

After the green `publish-site` job finishes, open these files on GitHub:

### A. Canonical current release

`data/releases/current.json`

Confirm:

- `release_id` = `2026-W35`
- `period_start` = `2026-08-24`
- `period_end` = `2026-08-30`

Do **not** expect the same counts as W34. Coverage, developments, additional reports, new developments, indices, market/source summaries and period totals are all rebuilt from W35.

### B. Saved weekly release

Confirm this new file exists:

`data/releases/weekly/2026-W35.json`

W34 and W33 must still remain. The system builds history; it does not overwrite prior weeks.

### C. Weekly index/history

Check:

`data/releases/index.json`

Its `current_release_id` must be `2026-W35` and its weekly series must contain W33, W34 and W35.

Then check:

`data/history/releases.json`

Its last point must be W35. This file is rebuilt from the release index, so the future newsletter/reporting layer can use a stable week-by-week series instead of a transient model run.

### D. Period rollups

Check:

`data/releases/period-index.json`

The current monthly, quarterly and annual rows must have:

`observed_week_end = 2026-08-30`

### E. Report and status

Check:

- `data/reports/latest.json`
- `data/status/latest.json`

Both must say `release_id = 2026-W35` and use W35 counts.

### F. Public site

Open the Observatory and hard refresh once. The page must say **24–30 August 2026**, not 17–23 August.

---

# What now updates automatically every week

The only scheduled core workflow is:

`.github/workflows/weekly-observatory.yml`

It runs every Monday at:

`00:17 UTC`

That is approximately:

- 02:17 in the Netherlands during CEST
- 01:17 in the Netherlands during CET

The non-zero minute avoids the busiest top-of-hour scheduling window.

The release builder always calculates the **previous complete Monday–Sunday week**. You do not have to edit dates each week.

A separate freshness check runs Monday evening (18:17 UTC) after the heavy model stages have had time to finish:

`.github/workflows/verify-public-freshness.yml`

It fails visibly if the public release is still the previous week or if current release files disagree with one another.

---

# How the numbers stay consistent

The homepage and dashboards read the current release directly. The final publication gate checks that all release-bound public artifacts point to the same `release_id` and the same canonical denominators.

Before Pages can deploy, the gate checks at least:

- current release ID and Monday–Sunday period;
- versioned weekly file and hash;
- Coverage count;
- Event/development count;
- additional coverage arithmetic;
- novelty categories;
- Coverage and Event indices;
- release index counts;
- public insights release ID, window and counts;
- PDF metadata release ID, window, counts and indices;
- weekly history release IDs and current counts;
- monthly, quarterly and annual summary freshness;
- relationship artifact release ID and denominators;
- existence of the referenced PDF.

If any of those disagree, deployment stops. The website therefore cannot silently combine W35 homepage numbers with a W34 status/report/relationship artifact.

The old independent `data/lenses/latest.json` and `data/events/latest.json` files remain useful inside the processing repository, but they are no longer copied into the public Pages build as competing public sources of truth.

---

# Relationship / symbiosis numbers

The core weekly Observatory no longer displays last week's relationship percentages as if they belonged to the new week.

Immediately after a weekly release is built, a **same-release** relationship artifact is created. If the human relationship review has not yet been completed, its status is:

`review_in_progress`

The public interface can show that the review is pending, but it will not reuse W34 relationship numbers on W35.

After the core weekly pipeline succeeds, this workflow starts automatically:

`Prepare Weekly Symbiosis Review`

It prepares the model classifications and review workbench separately so this expensive/human-governed layer cannot prevent the main weekly Observatory from updating.

When the W35 human relationship review is complete, publish the reviewed W35 relationship artifact and rerun `Publish Observatory Release`. That updates the relationship panel without changing the already-saved W35 core release.

This is the only deliberate exception to “all displayed numbers update automatically”: **a relationship number is not displayed as current until it belongs to the current release and satisfies the existing human-review rule**. Stale numbers are suppressed rather than carried forward.

---

# If today's run turns red

## Failure before `build-weekly-release`

Fix the failing stage and rerun **Weekly Observatory Pipeline**. Collection and generated outputs are designed to be repeatable; private source observations are retained in Supabase.

## Failure after W35 already exists

First inspect:

`data/releases/current.json`

If it already says W35, rerunning the full pipeline is normally safe when the rebuilt measurement content is identical. The release builder keeps an identical weekly release stable rather than creating a duplicate.

If upstream corrections cause genuinely different W35 measurement content, do **not** silently overwrite it. Use the existing `Build Weekly Observatory Release` manual workflow with `replace = true` and a clear `revision_reason`, then rerun the downstream summaries/insights/brief/publication steps. That preserves the previous W35 revision in the archive.

## W35 was built but Pages did not deploy

Do not edit the homepage numbers manually. Run:

**Actions → Publish Observatory Release → Run workflow**

The publication gate will either deploy the coherent W35 package or tell you exactly which derivative is stale.

## A configured market fails collection

The workflow now refuses to publish a partial five-market week. Rerun the main pipeline after the temporary SerpAPI problem is resolved. This is safer than allowing the homepage totals to drop simply because one market was absent.

---

# Files that matter most in this fix

## New workflows

- `.github/workflows/prepare-symbiosis-review.yml`
- `.github/workflows/verify-public-freshness.yml`

## Rebuilt weekly orchestration

- `.github/workflows/weekly-observatory.yml`

## Reusable core stages

- `.github/workflows/update-observatory.yml`
- `.github/workflows/translate-news.yml`
- `.github/workflows/resolve-events.yml`
- `.github/workflows/reconcile-observatory-history.yml`
- `.github/workflows/classify-dual-lenses.yml`
- `.github/workflows/finalize-stage7c-residual.yml`
- `.github/workflows/build-weekly-release.yml`
- `.github/workflows/publish-symbiosis-release.yml`
- `.github/workflows/build-period-summaries.yml`
- `.github/workflows/generate-public-insights.yml`
- `.github/workflows/generate-public-brief.yml`
- `.github/workflows/publish-observatory-release.yml`

## Canonical-source / consistency code

- `scripts/generate_public_insights.py`
- `scripts/generate_public_brief.py`
- `scripts/validate_and_publish_release.py`
- `scripts/build_public_site.py`
- `scripts/release_common.py`
- `scripts/collect_news.py`

## Browser stale-data guards

- `site.js`
- `edu/dashboard.js`
- `report/report.js`
- `methodology/methodology.js`

## Discovery timing

- `config/edu_countries.json`

---

# What was tested in this package

The package was locally checked for:

- Python syntax;
- JSON validity;
- workflow YAML syntax;
- reusable-workflow wiring;
- no intermediate Pages deployments;
- JavaScript syntax;
- release-bound W34 regeneration of insights/history/PDF/status;
- monthly/quarterly/annual summary build;
- public Pages artifact build;
- absence of legacy `data/lenses/latest.json` and `data/events/latest.json` from the public artifact;
- correct detection that W34 is stale on 31 August and W35 is required;
- three-page PDF output.

The live W35 counts cannot be generated locally from this package because the actual news collection and database-backed model pipeline must run in your GitHub/Supabase environment. That is exactly what **Weekly Observatory Pipeline** now performs.
