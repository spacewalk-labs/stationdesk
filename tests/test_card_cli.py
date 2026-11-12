"""Verify actual CLI spool publication and native systemd exit handling."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


CLI = Path(__file__).parents[1] / 'bin/stationdesk-card'


class CardCliTests(unittest.TestCase):
    def test_spool_payload_and_unit_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {k: v for k, v in os.environ.items() if k != 'INVOCATION_ID'} | {'STATIONDESK_ROOT': directory}
            subprocess.run([str(CLI), '--group', 'test', '--level', 'urgent', '--title', 'Ready',
                            '--peek', '--cwd', directory, '--prompt', 'Inspect files'],
                           env=environment, check=True, capture_output=True)
            spool = root / 'state/cards-spool'
            paths = list((spool / 'new').glob('*.json'))
            self.assertEqual(1, len(paths))
            self.assertEqual(0o644, paths[0].stat().st_mode & 0o777)  # a writer account's card is readable by the box user
            card = json.loads(paths[0].read_text())
            self.assertEqual('urgent', card['level'])
            self.assertTrue(card['peek'])
            self.assertEqual({'cwd': directory, 'prompt': 'Inspect files'}, card['run'])
            self.assertEqual([], list((spool / 'tmp').iterdir()))
            for result in ('success', 'signal'):
                subprocess.run([str(CLI), '--unit-result', 'stationdesk-example.service'],
                               env=environment | {'SERVICE_RESULT': result}, check=True,
                               capture_output=True)
            cards = [json.loads(path.read_text()) for path in (spool / 'new').glob('*.json')]
            self.assertEqual(2, len(cards))
            failure = next(card for card in cards if card['source'] == 'systemd')
            self.assertEqual('unit:stationdesk-example.service', failure['group'])
            self.assertEqual('urgent', failure['level'])
            self.assertEqual('systemd result: signal', failure['body'])
            subprocess.run([str(CLI), '--group', failure['group'], '--title', 'Recovered', '--resolves'],
                           env=environment, check=True, capture_output=True)
            cards = [json.loads(path.read_text()) for path in (spool / 'new').glob('*.json')]
            recovery = next(card for card in cards if card.get('resolves'))
            self.assertEqual(failure['group'], recovery['resolves'])

    def test_unit_failure_body_is_the_last_short_plain_journal_line(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / 'bin'
            fake.mkdir()
            (fake / 'journalctl').write_text("#!/bin/sh\nprintf 'starting\\nboard unreachable: timeout\\n{\"json\":1}\\n'\n")
            (fake / 'journalctl').chmod(0o755)
            environment = os.environ | {'STATIONDESK_ROOT': directory, 'SERVICE_RESULT': 'exit-code',
                                        'INVOCATION_ID': 'abc', 'PATH': str(fake) + ':' + os.environ['PATH']}
            subprocess.run([str(CLI), '--unit-result', 'u.service'], env=environment, check=True, capture_output=True)
            card = json.loads(next((Path(directory) / 'state/cards-spool/new').glob('*.json')).read_text())
            self.assertEqual('board unreachable: timeout', card['body'])


if __name__ == '__main__':
    unittest.main()
