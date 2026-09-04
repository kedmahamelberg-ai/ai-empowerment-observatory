# W35 relationship and public-signal audit

This audit is generated from the published W35 release and relationship
artifact. It is not a manually entered dashboard explanation and it does not
contain source-body text.

Run it for the current release or any future weekly release:

```bash
python3 scripts/audit_public_signal_denominators.py --release-id 2026-W35 \
  --output validation/qc/2026-W35-public-signal-audit.json
```

## W35 result

| Check | Result |
|---|---:|
| Distinct developments / people-card denominator | 110 |
| Benefit shown | 21 |
| Downside shown | 10 |
| Benefit and downside | 0 |
| Uneven benefit | 2 |
| No clear people change | 70 |
| Too little evidence | 9 |
| Old combined “not clear” total | 79 (= 70 + 9) |
| Developments with at least one full article | 47 |
| Developments with an explicit two-sided people-and-AI relationship pattern | 17 |
| Outside the narrower two-sided subset | 93 |

The first six people outcomes are mutually exclusive and sum to 110. The
public site now presents the 17/110 relationship subset as a subset, not as a
second unexplained total. The detailed machine-readable result is stored at
`validation/qc/2026-W35-public-signal-audit.json`.

## Why this is a future-proof control

The audit fails if the public relationship artifact does not match the weekly
release, if its evidence rows do not equal the event denominator, if the people
outcomes do not sum to that denominator, or if the stored “not clear” split
does not match the evidence rows. It therefore protects future weeks even when
their counts, languages, and full-article availability are different.

The separate full-week review file is
`validation/qc/2026-W35-event-qc.csv`. It is the starting point for owner
adjudication; it does not claim that the entire week is already human-reviewed.
