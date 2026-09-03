#!/usr/bin/env python3
"""Fast repository-level guardrails for the Observatory automation."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
ACTIONS = ROOT / ".github" / "actions"

FORBIDDEN_PUBLIC = (
    "Model-coded provisional signal",
    "model-coded weekly lens",
    "AI-benefiting parasitism",
    "Human-benefiting parasitism",
    "Competition or co-constraint",
    "Who gained and who was constrained?",
)

REQUIRED_ACTION_MINIMUMS = {
    "actions/checkout@": 7,
    "actions/setup-python@": 7,
    "actions/upload-artifact@": 7,
    "actions/cache@": 5,
    "actions/upload-pages-artifact@": 5,
    "actions/deploy-pages@": 4,
}


def fail(message: str) -> None:
    raise SystemExit(f"REPOSITORY INTEGRITY ERROR: {message}")


def major_after(text: str, prefix: str) -> list[int]:
    values = []
    for match in re.finditer(re.escape(prefix) + r"v(\d+)", text):
        values.append(int(match.group(1)))
    return values


def main() -> int:
    # Never let an Action try to push workflow-file edits. GitHub App tokens can
    # reject that push unless given workflow-management permission, and source
    # workflow changes should be normal reviewed commits anyway.
    automation_files = sorted(WORKFLOWS.glob("*.yml")) + sorted(
        ACTIONS.glob("**/*.yml")
    )
    for path in automation_files:
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if "git add" in line and ".github/workflows" in line:
                fail(f"{path.relative_to(ROOT)}:{line_no} attempts to stage workflow files")

        for prefix, minimum in REQUIRED_ACTION_MINIMUMS.items():
            for major in major_after(text, prefix):
                if major < minimum:
                    fail(
                        f"{path.relative_to(ROOT)} uses {prefix}v{major}; minimum supported by this repository is v{minimum}"
                    )

    public_files = [ROOT / "index.html", ROOT / "site.js", ROOT / "edu" / "index.html", ROOT / "edu" / "dashboard.js"]
    for path in public_files:
        text = path.read_text(encoding="utf-8")
        for phrase in FORBIDDEN_PUBLIC:
            if phrase in text:
                fail(f"internal process wording remains in {path.relative_to(ROOT)}: {phrase}")

    publish = (WORKFLOWS / "publish-observatory-release.yml").read_text(encoding="utf-8")
    if "validate_public_relationship_consistency.py" not in publish:
        fail("publication workflow is missing the relationship arithmetic gate")

    sym_publish = (ROOT / "scripts" / "publish_symbiosis_release.py").read_text(encoding="utf-8")
    required_rollback_guards = [
        "canonical_current_release_id",
        "is_current_release",
        "if is_current_release:",
        "current_release_id=canonical_current",
    ]
    missing_guards = [value for value in required_rollback_guards if value not in sym_publish]
    if missing_guards:
        fail(
            "historical relationship QC rollback guard is incomplete: "
            + ", ".join(missing_guards)
        )

    owner_qc = (WORKFLOWS / "apply-owner-symbiosis-qc.yml").read_text(encoding="utf-8")
    if "needs.apply.outputs.is_current == 'true'" not in owner_qc:
        fail("owner QC workflow must publish Pages only when the audited release is current")

    weekly = (WORKFLOWS / "weekly-observatory.yml").read_text(encoding="utf-8")
    expected_jobs = [
        "collect:", "translate:", "resolve-events:", "reconcile-history:",
        "classify-dual-lenses:", "finalize-residual:", "build-weekly-release:",
        "relationship-release-boundary:", "period-summaries:", "public-insights:",
        "public-brief:", "publish-site:",
    ]
    missing = [value for value in expected_jobs if value not in weekly]
    if missing:
        fail(f"weekly pipeline is missing stages: {', '.join(missing)}")

    stage7c_workflow = (WORKFLOWS / "classify-dual-lenses.yml").read_text(
        encoding="utf-8"
    )
    if "./.github/actions/run-stage7c-pass" in stage7c_workflow:
        fail("Stage 7C must be self-contained and not depend on a separately uploaded local action")
    if stage7c_workflow.count('--time-budget-minutes "225"') < 3:
        fail("Stage 7C must have three bounded, resumable job passes")
    if stage7c_workflow.count("actions/cache@v6") < 3:
        fail("each Stage 7C pass must restore the shared model cache")
    if "Require complete classification" not in stage7c_workflow:
        fail("Stage 7C is missing its downstream completion gate")

    stage7c_script = (ROOT / "scripts" / "classify_dual_lens.py").read_text(
        encoding="utf-8"
    )
    required_resume_guards = [
        "resume_or_start_classification_run",
        "load_saved_classifications",
        "checkpoint_classification_run",
        "--time-budget-minutes",
        "TransientSupabaseError",
        "supabase_execute_with_retry",
        "transient_supabase_error",
    ]
    missing_resume_guards = [
        value for value in required_resume_guards if value not in stage7c_script
    ]
    if missing_resume_guards:
        fail(
            "Stage 7C resumability guard is incomplete: "
            + ", ".join(missing_resume_guards)
        )

    # Supabase stores an absent lens dimension as a boolean flag with every
    # measurement/detail column NULL.  Saved rows are deliberately converted
    # back to a display-friendly ``not_present`` shape when read, so require a
    # dedicated write-boundary translator before any resumed event can write
    # those display values back into the database.
    required_dimension_storage_guards = [
        "def dimension_row_for_storage(",
        '"degree": None',
        '"confidence": None',
        '"reasoning": None',
    ]
    missing_dimension_storage_guards = [
        value
        for value in required_dimension_storage_guards
        if value not in stage7c_script
    ]
    if missing_dimension_storage_guards:
        fail(
            "Stage 7C is missing the absent-dimension database adapter: "
            + ", ".join(missing_dimension_storage_guards)
        )

    audit_script = (ROOT / "scripts" / "apply_stage7c_audit.py").read_text(
        encoding="utf-8"
    )
    if '"degree": int(item["degree"]) if present else None' not in audit_script:
        fail("Stage 7C audit writes do not preserve the absent-dimension constraint")

    body_workflow = (
        WORKFLOWS / "enrich-new-brief-article-bodies.yml"
    ).read_text(encoding="utf-8")
    hard_match = re.search(r"timeout-minutes:\s*(\d+)", body_workflow)
    soft_match = re.search(r"--max-runtime-minutes\s+(\d+)", body_workflow)
    if not hard_match or not soft_match:
        fail("body enrichment is missing hard or soft runtime limits")
    hard_minutes = int(hard_match.group(1))
    soft_minutes = int(soft_match.group(1))
    if hard_minutes < soft_minutes + 10:
        fail("body enrichment needs at least 10 minutes between soft and hard stops")
    if "--per-source-timeout-seconds" not in body_workflow:
        fail("body enrichment is missing its per-source watchdog")

    body_fetcher = (
        ROOT / "scripts" / "brief_backfill_article_content.py"
    ).read_text(encoding="utf-8")
    if "rp.read()" in body_fetcher:
        fail("robots.txt must not be read through an unbounded RobotFileParser request")
    for required in ("ROBOTS_TIMEOUT", "TDM_TIMEOUT", "ARTICLE_TIMEOUT"):
        if required not in body_fetcher:
            fail(f"body fetcher is missing {required}")

    body_resumer = (
        ROOT / "scripts" / "brief_backfill_article_content_resumable.py"
    ).read_text(encoding="utf-8")
    for required in (
        "source_deadline",
        "source_timeout",
        "TERMINAL_PRIOR_OUTCOMES",
    ):
        if required not in body_resumer:
            fail(f"body enrichment resumability guard is missing {required}")

    print("Repository integrity checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
