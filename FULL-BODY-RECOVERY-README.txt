AI Empowerment Observatory — full-body relationship recovery
=============================================================

What this package fixes
-----------------------
The current public relationship artifact reused successful headline-era rows
after the full-body replacement run was interrupted. This package makes input
evidence provenance part of reuse and publication decisions, so a headline-only
classification cannot hide or replace a classification for which a full article
body is available.

This package does not hard-code public counts or classifications.

Apply and run
-------------
1. Extract this ZIP and replace the repository contents on the main branch.
   Preserve the hidden .github directory; it contains the recovery workflow.
2. Commit the replacement to main and wait for "Observatory Repository
   Integrity" to pass.
3. In GitHub Actions, open "Resume Full-Body Relationship Results" and choose
   "Run workflow" once.

Do not manually run body collection, Stage 7C, the ordinary relationship
classifier, or the publisher for this recovery.

Safety behavior
---------------
The recovery workflow first locates the interrupted relationship run and checks
every saved row against the evidence currently required by release 2026-W35.
It prints the exact saved and remaining unit counts. If that saved run cannot be
reused safely, the workflow fails before model setup and will not start a new
multi-hour classification.

After the remaining units finish, publication runs automatically. Publication
is blocked unless all developments with available full article bodies were
actually classified from those bodies.
