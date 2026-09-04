# AIEO complete repository update

This delivery is the full `ai-empowerment-observatory` repository. It replaces
the earlier focused overlay, which was not sufficient to update the public
site and pipeline together.

## Upload

1. Unzip the package and use the inner repository folder as the repository
   root.
2. Replace the full repository contents in one commit; do not upload only a
   changed-files list. Preserve `.git` if working locally.
3. Commit directly to `main` with a message such as
   `Repair full multilingual Observatory pipeline and public release`.

Read `FULL-REPOSITORY-HANDOFF.md` before publishing. It names the required
checks and explains why the complete directory must move together.

## Verify and publish

1. Wait for **Observatory Repository Integrity** to turn green. Do not rerun an older failed run; the new push creates a fresh run.
2. Open **Actions → Publish Observatory Release → Run workflow → main**.
3. Do **not** rerun the Weekly Observatory Pipeline. The current W35 collection is already complete.
4. When publishing is green, hard-refresh the main page and `/edu/`.

## Current W35 signal

Use `W35_RELATIONSHIP_QC_AUDIT.md` and its generated JSON rather than a
hard-coded expected-count list. The audit currently verifies 110 distinct
developments, explains the 79 combined not-clear records as 70 + 9, and is
reusable for every future release.
