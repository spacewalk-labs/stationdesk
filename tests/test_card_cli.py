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
            environment = os.environ | {'STATIONDESK_ROOT': directory}
            subprocess.run([str(CLI), '--group', 'test', '--level', 'urgent', '--title', 'Ready',
                            '--peek', '--cwd', directory, '--prompt', 'Inspect files'],
                           env=environment, check=True, capture_output=True)
            spool = root / 'state/cards-spool'
            paths = list((spool / 'new').glob('*.json'))
            self.assertEqual(1, len(paths))
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


if __name__ == '__main__':
    unittest.main()
