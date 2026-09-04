# Relationship quality-control files

This directory holds owner-facing audit records and completed-review workbooks.
It is intentionally excluded from the built public Pages artifact.

- `2026-W35-event-qc.csv` is the initial full-week workbook: one row per
  resolved development, source links included, with blank `HUMAN_*` fields.
- `2026-W35-event-qc.meta.json` records how that workbook was generated.
- `2026-W35-public-signal-audit.json` explains the public denominator and
  evidence split without copying private article body text.

Generate a new full-week file when needed:

```bash
python3 scripts/export_symbiosis_qc.py --release-id 2026-W35 --mode all \
  --output validation/qc/2026-W35-event-qc.csv
```

For routine future checks, use `--mode stratified_random --sample-size 24`.
Follow `FIX-AND-QC-INSTRUCTIONS-2026-09-04.md` before importing a completed
workbook.
