"""The publication audit must preserve uncertainty independently of body access."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from audit_public_signal_denominators import build_audit, primary_outcome
from independent_axes import make_axes, signals_from_axes, summarize_axes
from test_complete_content import fixture as content_fixture


def fixture():
    release, relationship = content_fixture()
    for payload in (release, relationship):
        payload.update(release_id="2027-W03", period_start="2027-01-18", period_end="2027-01-24")
    # A complete French source with an unresolved human reading, a resolved
    # Chinese source, and an unavailable English source are distinct states.
    for row, human in zip(relationship["evidence"], ("unresolved", "gain", "unresolved")):
        complete = row["axes"]["evidence_complete"]
        row["axes"] = make_axes(human, "none" if complete else "unresolved",
                                human_evidence="Source reading", complete_evidence=complete)
        row["evidence_status"] = "sufficient" if complete else "insufficient"
        row["public_signals"] = signals_from_axes(row["axes"])
        row["relationship_patterns"] = {}
        row["evidence_basis_summary"]["full_text_sources"] = int(complete)
    relationship["directional_summary"] = summarize_axes(relationship["evidence"])
    relationship["people_signals"] = {
        "not_clear_breakdown": {"not_enough_evidence": 2, "no_directional_people_change": 0},
        "relationship_pattern_counts": {},
    }
    return release, relationship


class PublicSignalAuditTests(unittest.TestCase):
    def test_complete_source_with_unresolved_direction_stays_unresolved(self):
        release, relationship = fixture()
        audit = build_audit(release, relationship)
        inventory = audit["collection_inventory"]
        self.assertEqual(inventory["people_outcomes"]["too_little_evidence"], 2)
        self.assertEqual(inventory["people_outcomes"]["no_clear_people_change"], 0)
        self.assertEqual(inventory["source_evidence"]["developments_without_a_full_article"], 1)
        self.assertEqual(audit["event_denominator"], 1)
        self.assertEqual(audit["people_outcomes"]["benefit_shown"], 1)
        self.assertEqual(audit["people_outcomes"]["too_little_evidence"], 0)

    def test_every_human_direction_uses_its_axis_not_legacy_status(self):
        expected = dict(gain="benefit_shown", loss="downside_shown", mixed="benefit_and_downside",
                        none="no_clear_people_change", unresolved="too_little_evidence")
        for human, outcome in expected.items():
            for ai in expected:
                with self.subTest(human=human, ai=ai):
                    axes = make_axes(human, ai, human_evidence="Human evidence", ai_evidence="AI evidence")
                    self.assertEqual(primary_outcome({"axes": axes, "evidence_status": "sufficient"}), outcome)

    def test_legacy_records_keep_their_existing_interpretation(self):
        for gain, loss, status, expected in (
            (True, False, "sufficient", "benefit_shown"),
            (False, True, "sufficient", "downside_shown"),
            (True, True, "sufficient", "benefit_and_downside"),
            (False, False, "sufficient", "no_clear_people_change"),
            (False, False, "insufficient", "too_little_evidence"),
        ):
            self.assertEqual(primary_outcome({"public_signals": {"people_gaining": gain,
                             "people_losing_ground": loss}, "evidence_status": status}), expected)

    def test_wrong_breakdown_is_still_rejected_with_actual_counts(self):
        release, relationship = fixture()
        relationship["people_signals"]["not_clear_breakdown"] = {
            "not_enough_evidence": 1, "no_directional_people_change": 1}
        with self.assertRaisesRegex(SystemExit, "not_enough_evidence stored=1, rows=2"):
            build_audit(release, relationship)

    def test_invalid_or_inconsistent_directional_artifacts_are_rejected(self):
        for mutation in ("missing_axes", "invalid_axis", "summary", "signals", "revision", "pattern"):
            with self.subTest(mutation=mutation):
                release, relationship = fixture()
                row = relationship["evidence"][0]
                if mutation == "missing_axes": del row["axes"]
                if mutation == "invalid_axis": row["axes"]["human"]["direction"] = "unknown"
                if mutation == "summary": relationship["directional_summary"]["human"]["unresolved"] = 1
                if mutation == "signals": row["public_signals"]["people_gaining"] = True
                if mutation == "revision": relationship["source_release_sha256"] = "old"
                if mutation == "pattern": relationship["people_signals"]["relationship_pattern_counts"]["mutualism"] = 1
                with self.assertRaises(SystemExit): build_audit(release, relationship)

    def test_current_published_input_reconciles_when_ready(self):
        release = json.loads((ROOT / "data/releases/current.json").read_text())
        relationship = json.loads((ROOT / "data/symbiosis/current.json").read_text())
        if not relationship.get("directional_summary") or relationship.get("source_release_sha256") != release.get("content_sha256"):
            self.skipTest("The next weekly reading is still in progress.")
        audit = build_audit(release, relationship)
        inventory = audit["collection_inventory"]
        self.assertEqual(inventory["people_outcomes"]["too_little_evidence"], relationship["directional_summary"]["human"]["unresolved"])
        self.assertEqual(sum(audit["people_outcomes"].values()), audit["event_denominator"])


if __name__ == "__main__":
    unittest.main()
