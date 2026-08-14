AI Empowerment Observatory — Live Public Experience v1
=======================================================

WHY THIS PATCH EXISTS
---------------------
The current public pages still contain the legacy Stage 6 interface prototype:
- three hard-coded illustrative countries;
- promises of a five-country edition;
- prototype/fictitious scores;
- no real Coverage-vs-Event visual;
- no clear observation window;
- no report acquisition page;
- a Pro page that overpromises all-country coverage.

THE CORRECT LIVE SCOPE
----------------------
- Five SEARCH MARKETS: United States, China, United Kingdom, France, Canada.
- One current GLOBAL signal based on the real Coverage and Event Lens outputs.
- Search market is not event country.
- Country signals appear only when supported event geography reaches the
  evidence threshold.
- There is no public three-country or five-country league table in this release.

WHAT THIS PACKAGE REPLACES
--------------------------
Root:
  index.html
  site.css
  site.js

Public signal:
  edu/index.html
  edu/observatory.css
  edu/dashboard.js

Public governance pages:
  methodology/index.html
  methodology/methodology.css
  methodology/methodology.js
  status/index.html
  status/status.css
  status/status.js

Professional roadmap:
  pro/index.html

Report acquisition:
  report/index.html
  report/report.css
  report/report.js
  privacy/index.html

Public data configuration:
  data/site-config.json

Public build:
  scripts/build_public_site.py

WHAT THIS PACKAGE ADDS
----------------------
Three-page dynamic report:
  scripts/generate_public_brief.py
  .github/workflows/generate-public-brief.yml
  reports/placeholder.txt
  data/reports/placeholder.txt

Optional report/newsletter capture:
  supabase/migrations/013_public_report_requests.sql
  data/public-config.example.json

LIVE VISUALS
------------
The public signal page reads the real JSON produced by Stage 7C and shows:
- observation window;
- last successful update;
- five search-market discovery scope;
- Coverage article count vs unique Event count;
- Coverage Index vs Event Index;
- Directional Amplification Gap;
- Coverage/Event ratio;
- narrative distributions for both lenses;
- Event Lens empowerment-status distribution;
- four-dimension distribution;
- only evidence-supported country cards;
- source-linked recent events.

The old map and countries.json are no longer used. You may delete:
  edu/countries.json
  edu/map.js

INSTALL
-------
1. Replace/upload every file in this package at the same repository path.

2. Existing data folder:
   upload data/site-config.json.

3. Replace scripts/build_public_site.py.
   This public builder intentionally excludes review pages, raw data, scripts,
   migrations, validation sets, prompts, thresholds and private QA artifacts.

4. Commit:
   Replace prototype site with live data experience

5. Run:
   Actions -> Publish Observatory Release -> Run workflow -> main

6. Generate the current 3-page brief:
   Actions -> Generate Public Observatory Brief -> Run workflow -> main

7. Check:
   https://observatory.hamelberg-ai.com/
   https://observatory.hamelberg-ai.com/edu/
   https://observatory.hamelberg-ai.com/report/
   https://observatory.hamelberg-ai.com/pro/

OPTIONAL NEWSLETTER/REPORT-REQUEST STORAGE
------------------------------------------
The report downloads even when signup storage is not configured.

To store report requests and newsletter consent:
1. Run once in Supabase SQL Editor:
   supabase/migrations/013_public_report_requests.sql

2. In Supabase, copy the PUBLIC anon/publishable key (not the service-role key).

3. Create data/public-config.json from data/public-config.example.json:
   {
     "supabase_url": "https://YOUR-PROJECT.supabase.co",
     "supabase_anon_key": "YOUR-PUBLIC-KEY"
   }

4. Commit and redeploy.

GA4 already measures page views. The report page also emits:
  quarterly_report_download

The public report_requests table provides consent-based lead counts. Public
roles have INSERT-only access; they cannot read the table.

PRO POSITIONING
---------------
The public core remains free:
- current global signal;
- aggregate data;
- evidence examples;
- methodology;
- downloadable current-window brief and future quarterly editions.

Pro charges for workflow and depth:
- historical archive;
- evidence-threshold comparisons;
- approved exports/API;
- monitoring and alerts;
- evidence-linked executive briefs;
- in-company talks and training.

REPOSITORY PRIVACY
------------------
A public GitHub repository still exposes the implementation and commit history.
For genuine implementation protection, use one of these architectures:

A. Upgrade the organisation so GitHub Pages can run from a private repository.

B. Recommended on the free plan:
   - private processing repository: scripts, prompts, workflows, thresholds,
     migrations, raw data and QA;
   - clean public site repository: only the built _site artifact, aggregate JSON
     and public PDFs.

Changing the current repository's web navigation does not protect public source
code already visible on GitHub.
