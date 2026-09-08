"""Version bumps must pass integrity; missing recovery safeguards must not."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from validate_repository_integrity import validate_body_recovery_contract


class BodyRecoveryIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "scripts" / "brief_backfill_article_content.py").read_text(encoding="utf-8")

    def with_version(self, assignment):
        source, count = re.subn(r"^RECOVERY_STRATEGY_VERSION\s*=.*$", lambda _: assignment, self.source, count=1, flags=re.MULTILINE)
        self.assertEqual(count, 1)
        return source

    def test_current_collector_passes(self):
        validate_body_recovery_contract(self.source)

    def test_version_updates_do_not_require_an_integrity_edit(self):
        for value in ("safe_public_recovery_v4", "publisher_body_recovery_v6_2026_09_08", "publisher_body_recovery_v7", "article_recovery_2.0"):
            with self.subTest(version=value):
                validate_body_recovery_contract(self.with_version(f"RECOVERY_STRATEGY_VERSION = {value!r}"))

    def test_missing_empty_or_non_string_versions_are_rejected(self):
        for assignment in ("", 'RECOVERY_STRATEGY_VERSION = ""', 'RECOVERY_STRATEGY_VERSION = "  "', "RECOVERY_STRATEGY_VERSION = None", "RECOVERY_STRATEGY_VERSION = 7", '# RECOVERY_STRATEGY_VERSION = "comment_only"'):
            with self.subTest(assignment=assignment), self.assertRaisesRegex(SystemExit, "non-empty RECOVERY_STRATEGY_VERSION"):
                validate_body_recovery_contract(self.with_version(assignment))

    def test_version_must_still_be_used(self):
        source = self.source.replace("RECOVERY_STRATEGY_VERSION", "REMOVED_VERSION_PROVENANCE")
        source = 'RECOVERY_STRATEGY_VERSION = "future_v7"\n' + source
        with self.assertRaisesRegex(SystemExit, "must use RECOVERY_STRATEGY_VERSION"):
            validate_body_recovery_contract(source)

    def test_missing_recovery_guards_are_rejected(self):
        for marker in ('detail["policy_state"] = "absent"', "ROBOTS_TIMEOUT", "TDM_TIMEOUT", "ARTICLE_TIMEOUT", "same_publisher_site", "MAX_REDIRECTS", "detect_access_challenge", "evidence_unit_count"):
            with self.subTest(marker=marker), self.assertRaisesRegex(SystemExit, "article recovery contract is missing"):
                validate_body_recovery_contract(self.source.replace(marker, "REMOVED_GUARD"))

    def test_unbounded_robots_request_is_rejected(self):
        with self.assertRaisesRegex(SystemExit, "unbounded RobotFileParser"):
            validate_body_recovery_contract(self.source + "\nrp.read()\n")

    def test_english_only_request_is_rejected(self):
        with self.assertRaisesRegex(SystemExit, "English publisher variant"):
            validate_body_recovery_contract(self.source + '\nheaders = {"Accept-Language": "en"}\n')


if __name__ == "__main__":
    unittest.main()
