# Observatory release and relationship QC instructions

Read `FULL-REPOSITORY-HANDOFF.md` first. This is a complete repository
delivery; do not apply individual files from an old patch archive.

## 1. Verify the repository before publishing

Run these commands from the repository root:

```bash
python3 -m compileall -q scripts
python3 scripts/validate_multilingual_pipeline.py
python3 scripts/normalize_public_release_copy.py --check
python3 scripts/audit_public_signal_denominators.py --release-id 2026-W35 \
  --output validation/qc/2026-W35-public-signal-audit.json
python3 scripts/validate_repository_integrity.py
python3 scripts/build_public_site.py
python3 scripts/validate_public_site_artifact.py
```

The same checks are in the GitHub workflows. The final Pages validator is the
last gate before deployment, so an incomplete package cannot leave the source
repository looking correct while the public site stays old.

## 2. Current W35 whole-week quality review

The package already includes `validation/qc/2026-W35-event-qc.csv`, generated
from all 110 developments. If it needs to be regenerated from a refreshed W35
artifact, run:

```bash
python3 scripts/export_symbiosis_qc.py --release-id 2026-W35 --mode all \
  --output validation/qc/2026-W35-event-qc.csv
```

For each row, assess the linked source(s) and complete these columns:

- `HUMAN_enough_to_judge`
- `HUMAN_people_gaining`
- `HUMAN_people_losing_ground`
- `HUMAN_ai_advancing`
- `HUMAN_ai_limited`
- `HUMAN_unequal_benefits`
- `HUMAN_reasoning`
- `HUMAN_reviewer_name` and `HUMAN_reviewed_at`

Use `Yes`, `No`, or `Not sure` for the six questions. Resolve `Not sure`
before import; blank rows remain unreviewed. Leave
`HUMAN_include_in_gold=yes` only for rows personally adjudicated. Start with
the `HIGH` priority rows, then complete the rest of the W35 workbook.

Run **Apply Owner Symbiosis QC** in GitHub Actions twice:

1. First with `dry_run=true` and the exact repository path to the completed
   CSV. This validates every completed row without changing public data.
2. Then with `dry_run=false` only after the dry run succeeds. The workflow
   stores owner adjudications in `validation/symbiosis-owner-gold.json`, keeps
   its batch history, rebuilds the release data, and republishes only when the
   reviewed release is current.

The source workbook is deliberately separate from the public site. It is a
review tool, not content sent to Pages.

## 3. Future weeks: repeatable, not a W35-only fix

For regular QC, use **Build Optional Symbiosis QC File** with
`mode=stratified_random` and `sample_size=24`. The selection is reproducible
from the release ID unless a seed is supplied, and covers two-sided,
one-sided, and boundary/insufficient cases. Importing completed rows grows the
owner gold set over time; it does not convert unreviewed rows into reviewed
ones.

For an exceptional week, use `mode=all` again. The pipeline, checks, language
policy, public-denominator audit, and review workflow all accept any weekly
release ID; no step contains a W35-only count or article list.

## 4. Multilingual rule that must not regress

Collection preserves every configured market/search pass. Translation then
decides language from visible text, detected language, confidence, and the
search context:

- reliable English can pass through;
- Chinese uses its primary normalization route plus an independent audit;
- French and every other non-English or uncertain item uses multilingual
  normalization;
- a French or Chinese article discovered via an English Canadian search never
  becomes English merely because that search pass was English;
- unsupported or uncertain language is queued for normalization/review, not
  excluded.

The regression suite must remain green before a release is published.
