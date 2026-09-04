# Complete repository handoff — 4 September 2026

This folder is a complete AI Empowerment Observatory source repository. It is
not an overlay, a one-week patch, or a list of files to drag into an existing
checkout.

## Use this delivery

1. Replace the contents of the existing repository with this complete folder,
   while preserving the existing `.git` directory if working from a local
   clone.
2. Commit and push the complete change set to `main` in one commit.
3. Wait for **Observatory Repository Integrity** to pass.
4. Run **Publish Observatory Release**. That workflow builds the public Pages
   artifact from the same commit and rejects private files, stale wording, and
   mismatched public data before deployment.
5. Hard-refresh `https://observatory.hamelberg-ai.com/` and `/edu/` after the
   Pages deployment completes.

Do not use `aieo-multilingual-full-body-pipeline-2026-09-04(1).zip` as an
installation source. It was an incomplete overlay and cannot safely update the
site or its pipeline by itself.

## What is included for every future week

- `scripts/language_routing.py` is the single language-routing policy used by
  the translation pipeline. It never treats uncertain text as English.
- French, Chinese, and other non-English sources are retained, normalized, and
  audited; the original headline and source language remain in the record.
- A French story found through an English-language Canadian search is still
  routed as French. A Chinese story is routed through the Chinese primary path
  and an independent audit. No language is silently excluded because a search
  pass or language detector said English.
- A bilingual Canadian headline does not take the English passthrough when the
  detector reports a material competing French or other non-English signal; it
  is routed through multilingual normalization and flagged for review.
- `scripts/validate_multilingual_pipeline.py` includes functional regression
  checks for French, Chinese, multilingual Canadian, and uncertain-language
  routing. The repository workflow runs them on every relevant change.
- `scripts/audit_public_signal_denominators.py` computes, rather than
  hard-codes, the people-card denominator and its evidence breakdown for any
  weekly release.
- `scripts/normalize_public_release_copy.py` updates every public JSON
  snapshot, including archives, so retired internal wording cannot return when
  an older release is opened.
- `scripts/validate_public_site_artifact.py` checks the built Pages directory
  immediately before deployment. It rejects private paths, retired wording,
  and release-ID mismatches.
- `validation/qc/` contains the current whole-week review workbook and audit
  record. It is excluded from the public Pages build.

## Current W35 facts verified from the release data

The release contains 110 distinct developments. The public people cards are
mutually exclusive: 21 benefit shown, 10 downside shown, 0 both, 2 uneven
benefit, 70 no clear people change, and 9 too little evidence. The old
combined “not clear” total of 79 is therefore 70 + 9, not an unexplained
bucket. Forty-seven developments have at least one collected full article, and
17 have an explicit two-sided people-and-AI relationship pattern.

Those figures are not constants in the code. Re-run the audit for any release:

```bash
python scripts/audit_public_signal_denominators.py --release-id 2026-W35
```

## Whole-week quality review now; lighter QC later

`validation/qc/2026-W35-event-qc.csv` is a 110-row workbook for the current
full-week review. Complete only the `HUMAN_*` columns. Start with the rows
marked `HIGH`; each row carries its source links and a plain-language review
prompt. Follow `FIX-AND-QC-INSTRUCTIONS-2026-09-04.md` to validate it first,
then apply it. Future weeks can use the same export tool in reproducible
stratified-sample mode, so the quality process grows a human gold set without
requiring a full manual relabel every week.
