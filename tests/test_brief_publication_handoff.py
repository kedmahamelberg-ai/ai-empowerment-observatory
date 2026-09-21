import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import notify_brief_publication as handoff


class Response(io.BytesIO):
    status = 204


def export(week='2026-W38', digest='a' * 64):
    return Response(json.dumps({'release_id': week, 'public_content_sha256': digest}).encode())


class HandoffTests(unittest.TestCase):
    def test_waits_through_cache_and_network_failure(self):
        opener = Mock(side_effect=[URLError('offline'), export('2026-W37'), export()])
        sleep = Mock()
        handoff.wait_for_live(('2026-W38', 'a' * 64), attempts=3, opener=opener, sleep=sleep)
        self.assertEqual(sleep.call_count, 2)

    def test_same_week_with_old_revision_is_not_ready(self):
        with self.assertRaises(RuntimeError):
            handoff.wait_for_live(('2026-W38', 'b' * 64), attempts=1,
                                  opener=Mock(return_value=export()), sleep=Mock())

    def test_future_week_needs_no_code_change(self):
        handoff.wait_for_live(('2027-W01', 'a' * 64), attempts=1,
                              opener=Mock(return_value=export('2027-W01')))

    def test_missing_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            handoff.identity({'release_id': '2026-W38'})

    def test_dispatch_targets_only_brief_follow_workflow_main(self):
        opener = Mock(return_value=Response())
        handoff.dispatch('test-only', opener=opener)
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, handoff.DISPATCH_URL)
        self.assertEqual(json.loads(request.data), {'ref': 'main'})
        self.assertEqual(request.method, 'POST')

    def test_permission_failure_is_not_retried_or_leaked(self):
        opener = Mock(side_effect=HTTPError(handoff.DISPATCH_URL, 403, 'forbidden', {}, None))
        with self.assertRaisesRegex(RuntimeError, 'HTTP 403'):
            handoff.dispatch('test-only', opener=opener)
        self.assertEqual(opener.call_count, 1)

    def test_transient_dispatch_failure_retried(self):
        opener = Mock(side_effect=[URLError('offline'), Response()])
        handoff.dispatch('test-only', opener=opener, sleep=Mock())
        self.assertEqual(opener.call_count, 2)

    def test_missing_token_never_contacts_github(self):
        opener = Mock()
        with self.assertRaises(ValueError):
            handoff.dispatch('', opener=opener)
        opener.assert_not_called()

    def test_publication_requires_successful_deploy_before_notification(self):
        import yaml
        root = Path(__file__).resolve().parents[1]
        workflow = yaml.safe_load((root / '.github/workflows/publish-observatory-release.yml').read_text())
        job = workflow['jobs']['notify-brief']
        self.assertEqual(set(job['needs']), {'validate-and-build', 'deploy'})
        self.assertNotIn('if', job)  # Default success-only condition must remain.
        notifier = yaml.safe_load((root / '.github/workflows/notify-brief-publication.yml').read_text())
        token = next(s for s in notifier['jobs']['notify']['steps'] if s.get('id') == 'app-token')
        self.assertEqual(token['with']['repositories'], 'aieo-brief')
        self.assertEqual(token['with']['permission-actions'], 'write')


if __name__ == '__main__':
    unittest.main()
