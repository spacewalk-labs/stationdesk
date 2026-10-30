"""Feedback delivery uses fake HTTP responses; no real mail or intake requests."""
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('feedback', Path(__file__).parents[1] / 'apps/dev-monitor/backend/feedback.py')
feedback = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feedback)


class FeedbackTest(unittest.TestCase):
    def test_unconfigured_is_hidden_and_cannot_submit(self):
        with patch.multiple(feedback, FEEDBACK_ENABLED=False, INTAKE_ENABLED=False, MAIL_ENABLED=False):
            self.assertFalse(feedback.health()['enabled'])
            self.assertEqual(feedback.submit_feedback('Hello', 'owner@example.test'),
                             (False, 'feedback not configured (STATIONDESK_FEEDBACK_*)'))

    def test_intake_receives_server_owner_and_returns_link(self):
        with patch.multiple(feedback, FEEDBACK_ENABLED=True, INTAKE_ENABLED=True, MAIL_ENABLED=False,
                            INTAKE_URL='http://127.0.0.1:9999', TOKEN='fake-test-token'):
            with patch.object(feedback.urllib.request, 'urlopen', return_value=io.BytesIO(b'{"ok":true,"issue_url":"https://tracker.example.test/1"}')) as send:
                self.assertEqual(feedback.submit_feedback('  A suggestion  ', 'owner@example.test'),
                                 (True, {'issue_url': 'https://tracker.example.test/1'}))
                request = send.call_args.args[0]
                self.assertEqual(request.full_url, 'http://127.0.0.1:9999/submit')
                self.assertEqual(json.loads(request.data), {'owner': 'owner@example.test', 'text': 'A suggestion'})
                self.assertEqual(request.get_header('X-stationdesk-feedback-token'), 'fake-test-token')

    def test_both_targets_required_and_mail_failure_does_not_disclose_provider_error(self):
        with patch.multiple(feedback, FEEDBACK_ENABLED=True, INTAKE_ENABLED=True, MAIL_ENABLED=True):
            with patch.object(feedback, '_relay_intake', return_value=(True, {'issue_url':'https://tracker.example.test/1'})):
                with patch.object(feedback.urllib.request, 'urlopen', side_effect=OSError('sensitive provider response')):
                    self.assertEqual(feedback.submit_feedback('Hello', 'owner@example.test'),
                                     (False, 'mail: send failed: OSError'))

    def test_mail_subject_reply_and_body(self):
        with patch.multiple(feedback, FEEDBACK_ENABLED=True, INTAKE_ENABLED=False, MAIL_ENABLED=True,
                            MAIL_FROM='from@example.test', MAIL_TO='to@example.test', MAIL_API='http://127.0.0.1:9999/mail', MAIL_KEY='fake-test-key'):
            with patch.object(feedback.urllib.request, 'urlopen', return_value=io.BytesIO(b'{}')) as send:
                self.assertEqual(feedback.submit_feedback('Line one\nLine two', 'owner@example.test'), (True, {}))
                payload = json.loads(send.call_args.args[0].data)
                self.assertEqual(payload['subject'], '[StationDesk] owner@example.test: Line one Line two')
                self.assertEqual(payload['reply_to'], 'owner@example.test')
                self.assertEqual(payload['to'], ['to@example.test'])

    def test_invalid_text_does_not_deliver(self):
        with patch.object(feedback, 'FEEDBACK_ENABLED', True):
            for value in ('', '   ', 'x' * 8001, ['unexpected']):
                with self.subTest(value_type=type(value).__name__):
                    self.assertFalse(feedback.submit_feedback(value, 'owner@example.test')[0])


if __name__ == '__main__':
    unittest.main()
