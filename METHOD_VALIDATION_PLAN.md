# AIEO methodology and robustness plan — next controlled phase

This note is for implementation planning. It is not a public webpage and does not need to be uploaded to the repository.

## 1. What the supplied validation files currently support

### Stage 7C human audit

- 20 audited classifications.
- 6 accepted without correction.
- 14 corrected.
- The correction map contains exactly the same 14 classification IDs marked `needs_correction` in the audit CSV.

Interpretation: this is a useful launch diagnostic and correction set. It is **not** large or representative enough to estimate population accuracy. The public wording should therefore say “human-audited baseline,” not “validated model accuracy.”

### Event-resolution evidence

- `event_pair_gold_v1.csv`: 22 labelled pairs — 4 `same_event`, 17 `different_event`, 1 `related_topic`.
- `event_pair_hard_negatives_v1.csv`: 45 pairs, all `not_same_event`.
- `event_pair_story_coverage_v1.csv`: 8 pairs — 2 `same_event`, 6 `not_same_event`; all are French-language discovery from France and come from two story groups.
- `event_assignment_reviews.csv`: 9 reviewed decisions — 6 `keep_separate`, 3 `merge_candidate`.

Interpretation: the present evidence is strong enough to demonstrate careful attention to false merges, but it is heavily weighted toward negative pairs. It cannot yet estimate the false-split rate or recall of the event resolver across the five discovery markets.

## 2. Freeze the measurement contract before the next release

Create one versioned internal specification containing these definitions:

- **Article / Coverage unit:** one AI-relevant article in the monitored source set.
- **Resolved event record / Event unit:** one event record produced by the resolver after current automated and human rules. It is not yet a claim of a perfectly validated unique real-world development.
- **Extra coverage:** AI-relevant article units minus resolved event records for the same release window.
- **Coverage Empowerment Index:** arithmetic mean of article-level empowerment scores × 100.
- **Event Empowerment Index:** arithmetic mean of event-record empowerment scores × 100.
- **Directional Amplification Gap:** Coverage Empowerment Index minus Event Empowerment Index.
- **Narrative frame:** opportunity, threat, contested or descriptive/neutral; measured separately from empowerment.
- **Empowerment dimensions:** operational, creative, agentic and normative.

Also freeze the release window rule, source inclusion rule, language handling, treatment of recurring events and the status labels `provisional`, `human_audited_baseline` and `audited`.

## 3. Expand the event-resolution gold standard

Target a first defensible benchmark of at least 150 labelled pairs.

Stratify across:

- all five discovery markets;
- all monitored languages;
- same-language and cross-language pairs;
- same day, 1–3 day and longer time gaps;
- high, medium and low similarity bands;
- same-event positives, related-topic cases and different-event negatives;
- duplicate/syndicated coverage and genuinely distinct developments with similar wording.

Do not allow the benchmark to remain dominated by `not_same_event`. Aim for enough positive pairs to estimate false splits. A practical first target is roughly 50 same-event, 25 related-topic/ambiguous and 75 different-event pairs, adjusted after reviewing actual prevalence.

Report at least:

- pairwise precision, recall and F1 for `same_event`;
- confusion matrix across same, related and different;
- performance by market, language, cross-language status and similarity stratum;
- cluster-level B-cubed precision, recall and F1 once full gold clusters are available.

## 4. Expand the empowerment-classification audit

Build a 120–150-unit gold sample, stratified by:

- Coverage Lens versus Event Lens;
- five discovery markets and languages;
- expanding, contracting, mixed, non-empowerment and unclear outputs;
- the four empowerment dimensions;
- narrative frame;
- singleton versus multi-article events;
- routine versus high-risk cases.

Double-code at least 25–30% of the sample with an independent second human coder before resolving disagreements.

Report:

- macro-F1 and per-class precision/recall for empowerment status;
- agreement/error for empowerment degree;
- per-dimension precision/recall/F1;
- agreement for narrative frame and distribution breadth;
- human inter-coder agreement before adjudication;
- corrected versus model-only index differences.

Keep numeric model self-confidence diagnostic only until calibration is tested on a substantially larger labelled set.

## 5. Required robustness checks for every quarterly synthesis

Run and archive these sensitivity analyses:

1. **Human-corrected versus model-only:** recompute both indices and the amplification gap.
2. **Event-resolution sensitivity:** strict precision-first clustering versus a controlled, more permissive alternative.
3. **Market jackknife:** recompute after excluding each discovery market one at a time.
4. **Source sensitivity:** recompute after excluding the most prolific source or source group.
5. **High-risk exclusion:** recompute after excluding unresolved/unclear cases and compare direction and magnitude.
6. **Singleton sensitivity:** compare results with and without singleton event records.
7. **Coverage concentration:** report how much of article volume is generated by the largest events.
8. **Temporal stability:** compare weekly estimates with the accumulated monthly estimate and flag direction reversals.

The public release does not need every table, but the audit record should state whether the substantive conclusion survives each check.

## 6. Immediate implementation order

1. Apply the safe public-copy package.
2. Repair the newsletter “Other” field and confirmation callback using the missing source files.
3. Trace the visible counts and index values to their exact JSON and generating scripts.
4. Freeze the release schema and terminology.
5. Expand event-resolution validation.
6. Expand Stage 7C validation.
7. Run robustness checks on the next accumulated release.
8. Only then publish stronger claims about unique developments or measured model accuracy.
