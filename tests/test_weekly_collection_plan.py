import sys
import unittest
from datetime import date
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from plan_weekly_collection import plan
from release_common import date_for_article, previous_complete_week

class WeeklyCollectionPlan(unittest.TestCase):
    def test_next_monday(self):
        result=plan(date(2026,9,7))
        self.assertEqual((result['release_id'],result['period_start'],result['period_end']),('2026-W36','2026-08-31','2026-09-06'))
        self.assertEqual((result['markets'],result['searches']),(5,6))

    def test_year_boundary_and_sunday_do_not_make_partial_weeks(self):
        self.assertEqual(plan(date(2027,1,4))['release_id'],'2026-W53')
        self.assertEqual(plan(date(2026,9,6))['period_end'],'2026-08-30')

    def test_publication_dates_keep_buffer_articles_outside_week(self):
        period=previous_complete_week(date(2026,9,7))
        for stamp, expected in [('2026-08-30T12:00:00Z',False),('2026-08-31T12:00:00Z',True),('2026-09-06T21:59:59Z',True),('2026-09-06T22:00:00Z',False)]:
            self.assertEqual(period.contains(date_for_article({'published_at':stamp,'first_seen_at':'2026-09-07T01:00:00Z'})),expected)
