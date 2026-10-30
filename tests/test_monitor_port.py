"""Preserved monitor actions use the shared StationDesk contracts."""
import io
import json
import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/dev-monitor/backend'))
import cards
import accounts
import handler
import main
import store as app_store
import observability
import settings


class PortContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_service_restart_authority_comes_from_ledger(self):
        (self.root / 'state').mkdir()
        (self.root / 'state/installed.json').write_text(json.dumps({'apps': {'sample': {'artifacts': [
            {'kind': 'unit', 'name': 'custom-sample.service'},
            {'kind': 'unit', 'name': 'sample.timer'}]}}}))
        with patch.dict(os.environ, STATIONDESK_ROOT=str(self.root)):
            self.assertEqual(['custom-sample'], observability._stationdesk_user_units())
            with patch.object(observability, 'run') as run:
                self.assertEqual((False, 'service restart is not allowed'), observability.restart_svc('unowned.service'))
                run.assert_not_called()

    def test_slack_editor_and_delivery_share_file_without_returning_secrets(self):
        with patch.object(Path, 'home', return_value=self.root):
            result = settings.save_slack({'channel': 'Ctest', 'token': 'test-value', 'grace_seconds': 5})
            self.assertTrue(result['configured'])
            self.assertTrue(result['token_set'])
            self.assertNotIn('test-value', json.dumps(result))
            result = settings.save_slack({'channel': 'Cnext'})
            self.assertTrue(result['token_set'])
            self.assertEqual('Cnext', result['channel'])
            self.assertEqual(0o600, (self.root / '.config/stationdesk/slack.env').stat().st_mode & 0o777)

    def test_delivery_status_reports_real_queue_and_failures(self):
        store = cards.Cards(self.root)
        store.ingest(dict(id='test', group='test', source='test', level='urgent', title='Test'))
        self.assertEqual(dict(pending_count=1, last_sent_at=None, failed_count=0), store.delivery_health())
        with store.db() as db:
            db.execute('UPDATE cards SET send_attempts=1')
        self.assertEqual(1, store.delivery_health()['failed_count'])
        store.transition('test', 'archive')
        self.assertEqual(0, store.delivery_health()['pending_count'])

    def test_run_note_and_reopen_existing_window_use_devterm(self):
        store = cards.Cards(self.root)
        store.ingest(dict(id='test', group='test', source='test', level='normal', title='Test',
                          run=dict(cwd='/tmp', prompt='hello')))
        with patch.dict(os.environ, STATIONDESK_PORT_DEVTERM='10000'):
            with patch.object(cards.urllib.request, 'urlopen', return_value=io.BytesIO(b'{"ok":true}')) as opening:
                result = store.run('test', {}, 'Owner instructions')
                payload = json.loads(opening.call_args.args[0].data)
                self.assertEqual('hello\n\nOwner note: Owner instructions', payload['prompt'])
                name = result['ran_window']
                self.assertEqual(name, result['session']['name'])
            for sessions, state in [([{'name': name}], 'active'), ([], 'ended')]:
                with patch.object(cards.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps({'sessions': sessions}).encode())):
                    self.assertEqual(state, store.window('test')['state'])


class ReviewRegressions(unittest.TestCase):
    def test_fractional_cpu_capacity_is_used_for_ui_and_history(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(observability, 'cpu_capacity', return_value=1.5), \
                patch.object(observability, '_read_cgroup_cpu_usage_usec', side_effect=[1_000_000, 8_500_000]), \
                patch.object(observability, '_prev_cpu', {'usage_usec': 0, 'ts': 0}), \
                patch.object(observability, 'read_proc', return_value=''), \
                patch.object(observability.time, 'time', side_effect=[1, 6, 6]), \
                patch.object(observability.time, 'sleep'), \
                patch.object(observability, 'mem_info', return_value={'percent': 20}), \
                patch.object(observability, 'HISTORY_CSV', str(Path(directory)/'history.csv')):
            observability.history_sample_once()
            self.assertEqual('6,100.0,20\n', (Path(directory)/'history.csv').read_text())
        with patch.object(observability, 'cpu_capacity', return_value=1.5), \
                patch.object(observability, '_read_cgroup_cpu_usage_usec', return_value=8_500_000), \
                patch.object(observability, '_prev_cpu', {'usage_usec': 1_000_000, 'ts': 1}), \
                patch.object(observability.time, 'time', return_value=6), \
                patch.object(observability, 'read_proc', return_value=''):
            self.assertEqual(100, observability.cpu_info()['percent'])

    def test_top_process_cpu_uses_fractional_capacity(self):
        class StopSampling(BaseException):
            pass
        before = {1: dict(comm='busy', ticks=1, rss_kb=10, user='sd')}
        after = {1: dict(comm='busy', ticks=751, rss_kb=10, user='sd')}
        with patch.object(observability, 'cpu_capacity', return_value=1.5), \
                patch.object(observability, '_scan_procs', side_effect=[before, after]), \
                patch.object(observability, '_CLOCK_TICKS', 100), \
                patch.object(observability.time, 'sleep', side_effect=[None, StopSampling]):
            with self.assertRaises(StopSampling):
                observability._top_sampler()
        self.assertEqual(1.5, observability._TOP_CACHE['cores'])
        self.assertEqual(100, observability.top_processes()[0]['cpu_pct'])

    def test_slow_storage_does_not_block_health_or_cards(self):
        # Socket pairs exercise the actual request dispatcher without starting a listener.
        entered, release = threading.Event(), threading.Event()
        def storage():
            entered.set()
            release.wait(2)
            return []
        server = main.ThreadingHTTPServer(('unused', 0), handler.Handler, bind_and_activate=False)
        clients = []
        def submit(path):
            client, connection = socket.socketpair()
            client.settimeout(1)
            clients.append(client)
            server.process_request(connection, ('127.0.0.1', 1))
            client.sendall(('GET '+path+' HTTP/1.0\r\nTailscale-User-Login: owner@example.test\r\n\r\n').encode())
            return client
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, STATIONDESK_OWNER='owner@example.test', STATIONDESK_ROOT=directory), \
                patch.object(handler.observe, 'storage_info', side_effect=storage), \
                patch.object(handler.Handler, 'log_message'):
            try:
                slow = submit('/api/storage')
                self.assertTrue(entered.wait(1))
                for path in ('/health', '/api/owner/messages'):
                    fast = submit(path)
                    self.assertIn(b'200 OK', fast.recv(4096))
                self.assertFalse(release.is_set())
            finally:
                release.set()
                for client in clients:
                    while client.recv(4096):
                        pass
                    client.close()
                server.server_close()

    def test_concurrent_store_starts_keep_native_after_order(self):
        entered, release = threading.Event(), threading.Event()
        second_started, second_discovery = threading.Event(), threading.Event()
        units, launches = [], []
        def command(args):
            if args[0] == 'systemctl':
                if not entered.is_set():
                    entered.set()
                    self.assertTrue(release.wait(2))
                else:
                    second_discovery.set()
                return '\n'.join(unit+' loaded activating start CLI' for unit in units)
            unit = next(arg.removeprefix('--unit=') for arg in args if arg.startswith('--unit='))
            units.append(unit)
            launches.append(args)
            return ''
        with patch.object(app_store.Store, 'manifest'), patch.object(app_store, 'command', side_effect=command):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(app_store.Store('/unused').start, 'add', 'hello')
                self.assertTrue(entered.wait(1))
                def start_second():
                    second_started.set()
                    return app_store.Store('/unused').start('add', 'hub')
                second = executor.submit(start_second)
                self.assertTrue(second_started.wait(1))
                self.assertFalse(second_discovery.wait(.05))
                release.set()
                first.result(timeout=2)
                second.result(timeout=2)
        self.assertEqual(2, len(launches))
        self.assertIn('--property=After='+units[0], launches[1])

    def test_concurrent_account_status_keeps_both_observed_accounts(self):
        entered, release, second_observed = threading.Event(), threading.Event(), threading.Event()
        paths, errors = [], []
        replace = os.replace
        class StatusPool:
            def __init__(self, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def map(self, function, values):
                account = threading.current_thread().name
                if account == 'beta':
                    second_observed.set()
                return [dict(id=account, logged_in=True)]
        def replacing(source, target):
            paths.append(source)
            if len(paths) == 1:
                entered.set()
                if not release.wait(2):
                    raise AssertionError('first writer not released')
            replace(source, target)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(accounts.concurrent.futures, 'ThreadPoolExecutor', StatusPool), \
                patch.object(accounts.os, 'replace', side_effect=replacing):
            def run():
                try:
                    accounts.status(directory)
                except Exception as error:
                    errors.append(error)
            first = threading.Thread(target=run, name='alpha')
            second = threading.Thread(target=run, name='beta')
            first.start()
            self.assertTrue(entered.wait(1))
            second.start()
            self.assertTrue(second_observed.wait(1))
            release.set()
            first.join(2)
            second.join(2)
            self.assertFalse(first.is_alive() or second.is_alive())
            self.assertEqual([], errors)
            self.assertEqual(2, len(set(paths)))
            self.assertEqual(['alpha','beta'], json.loads((Path(directory)/'state/accounts.json').read_text())['logged_in'])
            self.assertEqual([], list((Path(directory)/'state').glob('.accounts.*')))

    def test_concurrent_slack_edits_keep_new_token_and_new_channel(self):
        entered, release, second_read = threading.Event(), threading.Event(), threading.Event()
        errors = []
        read = settings.slack_config
        def reading():
            value = read()
            if threading.current_thread().name == 'token-editor' and not entered.is_set():
                entered.set()
                if not release.wait(2):
                    raise AssertionError('token writer not released')
            elif threading.current_thread().name == 'channel-editor':
                second_read.set()
            return value
        with tempfile.TemporaryDirectory() as directory, patch.object(Path, 'home', return_value=Path(directory)):
            settings.save_slack(dict(token='old', channel='old'))
            def edit(data):
                try:
                    settings.save_slack(data)
                except Exception as error:
                    errors.append(error)
            with patch.object(settings, 'slack_config', side_effect=reading):
                first = threading.Thread(target=edit, args=({'token':'new-token'},), name='token-editor')
                second = threading.Thread(target=edit, args=({'channel':'new-channel'},), name='channel-editor')
                first.start()
                self.assertTrue(entered.wait(1))
                second.start()
                self.assertFalse(second_read.wait(.05))
                release.set()
                first.join(2)
                second.join(2)
                self.assertFalse(first.is_alive() or second.is_alive())
            self.assertEqual([], errors)
            value = settings.slack_config()
            self.assertEqual('new-token', value['SLACK_BOT_TOKEN'])
            self.assertEqual('new-channel', value['SLACK_CHANNEL'])


if __name__ == '__main__':
    unittest.main()
