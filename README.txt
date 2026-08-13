AI Empowerment Observatory — Stage 7B.3a COMPLETE PACKAGE
Precision-first Article → Event Assignment
=============================================================

THIS PACKAGE IS SELF-CONTAINED FOR STAGE 7B.3a.

It includes:
- original Stage 7B.3 Supabase schema migration
- corrected Stage 7B.3a resolver
- one-time reset for the first 7B.3 pilot
- event-review application script
- both GitHub Actions workflows
- complete event-assignment review UI
- data/events placeholder folder

WHY 7B.3a
---------
The first Stage 7B.3 production run produced:

  130 coverage articles
  66 active events
  1 auto merge
  66 new events
  63 review cases
  63 pending events

The resolver itself ran successfully, but its HUMAN-REVIEW trigger was too
broad. Generic AI semantic similarity and competing retrieval candidates were
creating human review work even when there was no credible same-event signal.

7B.3a fixes ONLY that decision policy.

FINAL 7B.3a POLICY
------------------
AUTO MERGE
  Requires a strong Qwen SAME decision plus strong independent evidence.

REVIEW
  Only if:
  - Qwen says SAME but auto-merge is not safe; OR
  - Qwen says UNCLEAR and there is meaningful supporting evidence; OR
  - Qwen says NOT-SAME but exceptionally strong independent evidence conflicts
    with that conclusion.

NEW EVENT
  - Qwen says NOT-SAME under normal conditions
  - Qwen is not called
  - embedding/event similarity alone
  - competing candidate events alone
  - ordinary ModernBERT positive signal alone

Core rule:
  RETRIEVAL FINDS CANDIDATES.
  RETRIEVAL DOES NOT CREATE HUMAN WORK.

FILES
-----
.github/workflows/
  resolve-events.yml
  apply-event-reviews.yml

scripts/
  resolve_events.py                    <-- corrected 7B.3a resolver
  apply_event_assignment_reviews.py

supabase/migrations/
  009_event_assignment.sql             <-- persistent schema migration

supabase/one_time/
  reset_first_7b3_pilot.sql            <-- run ONCE before rerunning 7B.3a

review/events/assignments/
  index.html
  assignments.css
  assignments.js

data/events/
  placeholder.txt

FIRST-TIME SCHEMA NOTE
----------------------
If you have ALREADY successfully run 009_event_assignment.sql, DO NOT run it
again just for 7B.3a.

If you have NOT run it, run:
  supabase/migrations/009_event_assignment.sql

YOUR CURRENT NEXT STEPS
-----------------------
Because the first Stage 7B.3 pilot already ran:

1. Supabase -> SQL Editor -> New query

   Run ONCE:
   supabase/one_time/reset_first_7b3_pilot.sql

   This:
   - deletes only event_articles links belonging to article_to_event_v1 events
   - retires only the first production pilot's active/pending events
   - DOES NOT delete raw articles
   - DOES NOT delete collection history
   - DOES NOT delete translation history
   - DOES NOT delete Stage 7B.2 legacy_provisional clusters
   - DOES NOT delete old resolution decisions/runs (provenance remains)

2. GitHub -> scripts/

   Replace:
   resolve_events.py

   The other Stage 7B.3 files you already uploaded do not need changing.

3. Commit:
   Reduce Stage 7B.3 event-review queue

4. Start a NEW workflow run:

   Actions
   -> Resolve AI News Into Events
   -> Run workflow
   -> main

   Do NOT use "Re-run jobs" on the old commit.

5. When green, open:

   https://observatory.hamelberg-ai.com/review/events/assignments/

6. DO NOT REVIEW CASES YET.

   Send ChatGPT only:
   - coverage articles
   - active events
   - auto merges
   - new events
   - review cases
   - verifier calls
   - pending events

HARD STOP RULE
--------------
This is the final resolver-policy rerun.

PASS:
  Review queue is small and cases are genuinely plausible same-event matches.
  Then human-review those few cases and advance to Event/Coverage classification.

FAIL:
  Review queue is still bloated or obviously nonsensical.
  Do NOT tune again.
  Permanently simplify to:
    strong verified SAME -> merge
    everything else -> separate event
  and proceed to classification.

No more model benchmarking or positive-sample hunting after this rerun.
