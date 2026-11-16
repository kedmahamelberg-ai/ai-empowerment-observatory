"""The weekly watchdog must distinguish a built week from a deployed week."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from ensure_weekly_pipeline import decision
from plan_weekly_collection import needs_collection


class WeeklyWatchdogTests(unittest.TestCase):
    def test_deployed_week_is_complete(self):
        self.assertEqual(decision('2026-W44', '2026-W44', '2026-W44', [], [], []),
                         'already_published')

    def test_built_but_unpublished_week_recovers_saved_work(self):
        self.assertEqual(decision('2026-W44', '2026-W44', '2026-W43', [], [], []),
                         'dispatch_saved_work_recovery')

    def test_active_release_work_is_not_duplicated(self):
        for status in ('queued', 'in_progress', 'waiting', 'requested', 'pending'):
            for position in range(3):
                with self.subTest(status=status, position=position):
                    runs = [[], [], []]
                    runs[position] = [{'status': status}]
                    self.assertEqual(decision('2026-W44', '2026-W44', '2026-W43', *runs),
                                     'release_work_active')

    def test_failed_week_can_retry(self):
        self.assertEqual(decision('2026-W43', '2026-W44', '2026-W43',
                                  [{'status': 'completed', 'conclusion': 'failure'}], [], []),
                         'dispatch_missing_week')

    def test_delayed_duplicate_skips_collection_after_release_is_current(self):
        self.assertFalse(needs_collection('2026-W44', '2026-W44'))
        self.assertTrue(needs_collection('2026-W44', '2026-W43'))
        self.assertTrue(needs_collection('2026-W44', '2026-W44', force=True))


if __name__ == '__main__':
    unittest.main()
