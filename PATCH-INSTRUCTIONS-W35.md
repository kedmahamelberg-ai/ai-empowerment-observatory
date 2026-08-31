# AIEO W35 semantic + relationship automation repair

## What the supplied W35 artifact actually contains

The successful core release is `2026-W35`, covering 24–30 August 2026.

Current artifact before this patch:

- 112 AI-relevant source pages
- 111 distinct developments
- 1 additional source page beyond one source per development
- 102 labelled first-time/new
- 6 labelled recurring/seen-before
- 3 possible historical matches still awaiting validation

The arithmetic is therefore `102 + 6 + 3 = 111` developments and `111 + 1 = 112` source pages.

However, the six “seen before” records are not true prior-release recurrences. They were turned into recurring records by collection/retry bookkeeping (`same_article_rediscovered` / `same_event_new_coverage`) even though those event IDs do not occur in an earlier standardized weekly release. That is why the public meaning was confusing and unstable.

With recurrence defined against prior standardized AIEO releases, the supplied W35 artifact resolves to:

- 108 first-time developments
- 0 recurring developments
- 3 history-match cases under validation

So the corrected W35 equation is `108 + 0 + 3 = 111` developments.

The same bug also contaminated the already-saved standardized history. The repair workflow restates all saved weekly releases using the corrected rule rather than changing only W35.

## Meaning of the four top-line numbers after the patch

- **Coverage** = AI-news source pages published in the weekly period.
- **Developments** = distinct real-world occurrences after pages about the same occurrence are grouped.
- **Additional coverage** = source pages beyond one page per distinct development. It is `Coverage - Developments` for the current release.
- **First-time developments** = developments not present in an earlier standardized AIEO weekly release.

A separate evidence filter shows **Recurring** developments. Another filter shows **History match review** when the longitudinal resolver has found a plausible cross-week link but has not accepted it yet.

## Why “Relationship review pending” appeared everywhere

That was a separate issue. The core weekly workflow intentionally published a same-week relationship placeholder, and a post-run relationship classifier was supposed to fill it. Two problems remained:

1. the post-run workflow updated the relationship JSON but did not redeploy GitHub Pages; and
2. the public renderer refused to display any model-coded relationship signal until every event and coverage item had completed human review.

That would leave a scalable weekly Observatory looking permanently unfinished.

This patch changes the policy to:

- core weekly release can publish immediately;
- relationship classification runs automatically after the core workflow;
- while classification is actually running, one global status message is shown rather than a “pending” badge on every evidence card;
- when classification finishes, the site publishes a **model-coded provisional** relationship signal automatically;
- accepted human corrections replace model outputs as review progresses;
- a fully completed human review automatically becomes `human_reviewed`;
- the post-run relationship workflow now redeploys Pages after updating the relationship artifact.

The existing primary weekly empowerment lens on `/edu/` is also rendered from the canonical weekly release rather than being blocked by the separate relationship-review process.

## Install this patch

1. Unzip the patch at the root of your current repository and overwrite files with the same paths.
2. Do **not** replace your `data/` directory. This patch intentionally contains no current release data.
3. Commit and push all patch files to `main`.
   Suggested commit message: `Fix weekly novelty semantics and relationship publication`

## Repair W35 and the saved history today

After the patch is on `main`:

1. Open GitHub **Actions**.
2. If an older **Prepare Weekly Symbiosis Review** run from the previous code is still running, cancel that old run first so you do not run two relationship classifiers at once.
3. Open **Repair Current Observatory Release**.
4. Click **Run workflow** → `main` → **Run workflow**.
5. Let the workflow finish. It performs, in order:
   - restate standardized release history using prior public weekly releases rather than collection retries;
   - commit revisioned W33/W34/W35 release files;
   - reset the relationship artifact to the corrected W35 release hash;
   - rebuild monthly/quarterly/annual summaries;
   - rebuild public insights and the public brief;
   - classify the current relationship lens;
   - publish a model-coded provisional relationship artifact (or human-reviewed artifact if review is already complete);
   - validate every public derivative and deploy GitHub Pages once.
6. Do not separately rerun collection, translation, resolution, or the main Weekly Observatory Pipeline for this repair. The W35 news collection is already valid.

## Verify after the repair

Open `data/releases/current.json`. For the supplied artifact, the expected W35 novelty counts are:

```text
release_id: 2026-W35
period_start: 2026-08-24
period_end: 2026-08-30
ai_relevant_articles: 112
ai_relevant_event_records: 111
first_time_event_records: 108
follow_on_event_records: 0
recurring_event_records: 0
possible_historical_match_event_records: 3
extra_coverage: 1
```

The three history-match cases remain explicitly separate until their longitudinal match is accepted or rejected. They are not silently forced into either first-time or recurring.

On the website you should therefore be able to reconcile the visible numbers directly:

```text
112 source pages = 111 developments + 1 additional coverage page
111 developments = 108 first recorded + 0 recurring + 3 history matches under validation
```

The evidence page should no longer show `Relationship review pending` on every card. The pagination control will say `Show next 6 developments` rather than `Show 6 more`, and it disappears instead of displaying `Show 0 more`.

## Automatic behavior from the next week onward

No repair workflow is required in normal weeks.

`Weekly Observatory Pipeline` continues to run every Monday. The corrected release builder determines recurrence by asking whether the effective event already exists in an earlier standardized AIEO weekly release. Collection retries and rediscovered articles remain diagnostic metadata only.

After the core weekly workflow succeeds, `Prepare Weekly Symbiosis Review` automatically:

1. classifies the current relationship lens;
2. publishes the same-release model-coded provisional signal with any accepted human corrections;
3. validates the Observatory again; and
4. redeploys Pages so the relationship layer is visible.

All standardized weekly releases remain saved under `data/releases/weekly/`, with revisions archived when history is corrected. The release index, weekly history, period summaries, current report metadata, and website continue to derive from those saved canonical releases. This keeps the historical series usable for the future newsletter/report layer.
