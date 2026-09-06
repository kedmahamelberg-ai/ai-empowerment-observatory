# Complete Observatory update — 6 September 2026

This repository includes the redesigned website, the 24–30 August source readings, the public classification CSV, the regenerated PDF and the updated weekly pipeline.

## Install once

Use the `INSTALL-ON-MAC.command` in the downloaded complete package. It copies every changed file into its correct location and saves a backup.

1. In GitHub Desktop, open **ai-empowerment-observatory** and choose **Repository → Show in Finder**. This is the existing repository folder to select in the installer.
2. Double-click **INSTALL-ON-MAC.command**, then select that existing repository folder.
3. Return to GitHub Desktop. Enter **Complete Observatory update** in Summary, click **Commit to main**, then **Push origin**.

The push starts **Publish Observatory Release** automatically. Its `deploy` job must finish green for the live website to change. The separate Repository Integrity result verifies code and data; it is not a deployment confirmation. A browser refresh after deployment loads the versioned assets.

The full `Repository` folder is also included for inspection or installation through another Git client. The installer preserves folder paths, including the workflow directory. It does not require files to be placed individually through GitHub's website.

## Current edition: 24–30 August 2026

There are **111 source pages grouped into 110 developments**. The complete package accounts for every development. **82 have completed written-source readings; 28 remain unresolved because complete usable evidence was unavailable.** The individual reasons and source links are in the JSON and CSV. This is not a claim that all 110 sources were available in full.

| Exclusive category | People | AI and operators |
| --- | ---: | ---: |
| Gains only | 45 | 36 |
| Losses / limitations only | 3 | 2 |
| Gains and losses / limitations | 26 | 38 |
| No direction stated | 8 | 6 |
| Unresolved evidence | 28 | 28 |
| Total | 110 | 110 |

Each column is a separate reading of the same 110 developments. Mixed is one category. A source does not have to establish both dimensions. The old paired-pattern subtotal is no longer used as the headline AI count.

The readings cover source claims, anticipated benefits, risks and recommendations, with their context retained. They are not independently verified measures of causal impact. Cultural and fictional material is identified in the relevant record. These are source-audit corrections, not owner-adjudicated gold decisions.

## What updates automatically

The existing **Weekly Observatory Pipeline** runs each Monday at **00:17 UTC**, collecting and processing the previous completed week. It collects bodies before classification, groups the sources, publishes the new readings, rebuilds the report and deploys the site. Existing Supabase secrets and GitHub Pages settings are used. No database migration or new API key is required by this update.

- Human and AI directions are independent; simultaneous gains and losses remain mixed.
- Long sources are read in overlapping segments covering every character. The middle is retained.
- Saved bodies are checked for access previews, broken encoding and mismatched stored hashes. Invalid cached bodies become eligible for retrieval again; access restrictions remain respected.
- Three resumable passes retain completed units and retry failed units. A failed generation is an execution failure and cannot become a research finding.
- The release builder, classifier and finalizer use matching versions.
- This week's corrections are bound to the exact W35 source release and cannot spill into another week. Accepted owner decisions take priority.
- Optional owner QC preserves independent and mixed directions. Its retained examples inform later automatic runs; QC is not a weekly publishing requirement.
- The website, CSV, brief metadata and PDF use one versioned record-level dataset. Publication checks reject missing records, incorrect totals, stale derivatives and placeholders.
- Public website files exclude execution prompts and private decision provenance. Archived source-release indices retain their original policy and are not used to calculate these new direction counts.

## Where the principal files live

| Purpose | Repository location |
| --- | --- |
| Main design and moving globe | `index.html`, `editorial.css`, `site.js`, `globe.js`, `vendor/`, `data/geography/` |
| Shared counting and labels | `public-data.js` |
| News, reports and methodology pages | `edu/`, `report/`, `reports/`, `methodology/` |
| Current published readings | `data/symbiosis/current.json` |
| Full classification download | `data/symbiosis/current.csv` |
| Frozen weekly readings | `data/symbiosis/weekly/2026-W35.json` and `.csv` |
| Durable W35 correction manifest | `validation/corrections/2026-W35-independent-directions.json` |
| Independent axes and source checks | `scripts/independent_axes.py`, `scripts/source_evidence_quality.py` |
| Canonical materialization and validation | `scripts/public_directional_release.py`, `scripts/publish_symbiosis_release.py` |
| Regenerated public brief | `reports/ai-empowerment-pulse-latest.pdf` |
| Automatic schedule and deployment | `.github/workflows/weekly-observatory.yml`, `.github/workflows/publish-observatory-release.yml` |
| Regression checks | `tests/test_independent_directions.py`, `tests/test_relationship_recovery.py`, `tests/test_public_display.mjs` |

The package's `FILES-TO-INSTALL.tsv` lists every installed file and its SHA-256 checksum. `VALIDATION-RESULTS.txt` records the completed checks and their limits.
