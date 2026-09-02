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
    if stage7c_workflow.count("uses: ./.github/actions/run-stage7c-pass") < 2:
        fail("Stage 7C must have more than one bounded, resumable job pass")
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
    ]
    missing_resume_guards = [
        value for value in required_resume_guards if value not in stage7c_script
    ]
    if missing_resume_guards:
        fail(
            "Stage 7C resumability guard is incomplete: "
            + ", ".join(missing_resume_guards)
        )

    print("Repository integrity checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
