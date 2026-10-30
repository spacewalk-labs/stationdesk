"""Publish's default external path: Tailscale Funnel. No live tailscale or nginx here."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_engine import EngineTests as Base, engine

APP = Path(__file__).parents[1] / 'apps/publish'
spec = importlib.util.spec_from_file_location('publish_funnel', APP / 'server.py')
publish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish)


class FunnelEngineTests(unittest.TestCase):
    setUp, command, fixture = Base.setUp, Base.command, Base.fixture

    def endpoint_app(self):
        self.fixture('publish', 'endpoints = "publish/funnel-endpoints.json"\n')
        state = self.root / 'state/publish'
        state.mkdir()
        (state / 'funnel.conf').write_text('location / { root ${STATIONDESK_STATE}/publish/public; }\n')
        path = state / 'funnel-endpoints.json'
        path.write_text(json.dumps([dict(https_port=10000, gate_port=19926,
                                         fragment='publish/funnel.conf', funnel=True)]))
        return path

    def commands(self):
        return [args for args, _ in self.calls]

    def run_add(self, command=None, caps=('funnel', 'https')):
        status = {'Self': {'UserID': 42, 'DNSName': 'desk.example.test.', 'CapMap': dict.fromkeys(caps, [])},
                  'User': {'42': {'LoginName': 'owner@example.test'}}}
        base = self.command

        def dispatch(args, **kwargs):
            if args[:2] == ['tailscale', 'status']:
                return subprocess.CompletedProcess(args, 0, json.dumps(status), '')
            return (command or base)(args, **kwargs) if command else base(args, **kwargs)
        return patch.object(engine, 'run', side_effect=dispatch)

    def test_funnel_endpoint_is_public_and_removal_retires_it(self):
        self.endpoint_app()
        with patch.object(engine.shutil, 'which', return_value='/usr/bin/tailscale'), self.run_add():
            self.engine.add('publish')
            site = (self.root / 'nginx/site.conf').read_text()
            block = site[site.index('listen 127.0.0.1:19926;'):]
            self.assertNotIn('tailscale_user_login', block.split('server {')[0])
            self.assertIn(['tailscale', 'funnel', '--bg', '--https=10000', 'http://127.0.0.1:19926'], self.commands())
            self.assertNotIn(['tailscale', 'serve', '--bg', '--https=10000', 'http://127.0.0.1:19926'], self.commands())
            self.calls.clear()
            self.engine.remove('publish')
            self.assertIn(['tailscale', 'funnel', '--https=10000', 'off'], self.commands())
            self.assertNotIn(['tailscale', 'serve', '--https=10000', 'off'], self.commands())

    def test_policy_blocked_funnel_skips_the_cli_and_install_succeeds(self):
        self.endpoint_app()
        for caps in ((), ('https',), ('funnel',)):
            with self.subTest(caps=caps), \
                    patch.object(engine.shutil, 'which', return_value='/usr/bin/tailscale'), \
                    self.run_add(caps=caps), contextlib.redirect_stderr(io.StringIO()) as errors:
                self.calls.clear()
                self.engine.add('publish')
                self.assertIn('publish', self.engine.load()['apps'])
                self.assertFalse([a for a in self.commands() if a[:2] == ['tailscale', 'funnel']])
                self.assertIn('blocked by tailnet policy', errors.getvalue())
                self.engine.remove('publish')

    def test_funnel_cli_failure_with_capability_does_not_fail_install(self):
        self.endpoint_app()

        def command(args, **kwargs):
            if args[:2] == ['tailscale', 'funnel']:
                return subprocess.CompletedProcess(args, 1, '', 'Funnel not enabled')
            return self.command(args, **kwargs)
        with patch.object(engine.shutil, 'which', return_value='/usr/bin/tailscale'), self.run_add(command):
            self.engine.add('publish')
            self.assertIn('publish', self.engine.load()['apps'])
            self.engine.remove('publish')

    def test_doctor_probes_funnel_endpoint_as_public_not_owner_gated(self):
        self.endpoint_app()
        with patch.object(engine.shutil, 'which', return_value='/usr/bin/tailscale'), self.run_add():
            self.engine.add('publish')
        seen = []

        def probes(args, **kwargs):
            if args[0] == 'curl':
                seen.append(args)
                url = args[-1]
                if url == 'http://127.0.0.1:19926/robots.txt':
                    return subprocess.CompletedProcess(args, 0, '200', '')
                code = '200' if 'Tailscale-User-Login: owner@example.test' in args else '403'
                return subprocess.CompletedProcess(args, 0, code, '')
            return self.command(args, **kwargs)
        with patch.object(engine, 'run', side_effect=probes), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, self.engine.doctor())
        self.assertIn('publish endpoint-10000-public: OK', output.getvalue())
        self.assertFalse([a for a in seen if a[-1] == 'http://127.0.0.1:19926/'])

    def test_owner_gated_endpoint_is_unchanged(self):
        path = self.endpoint_app()
        path.write_text(json.dumps([dict(https_port=8448, gate_port=19926, fragment='publish/funnel.conf')]))
        with patch.object(engine.shutil, 'which', return_value='/usr/bin/tailscale'), self.run_add():
            self.engine.add('publish')
            site = (self.root / 'nginx/site.conf').read_text()
            self.assertIn('listen 127.0.0.1:19926;\n    if ($http_tailscale_user_login', site)
            self.assertIn(['tailscale', 'serve', '--bg', '--https=8448', 'http://127.0.0.1:19926'], self.commands())

    def test_missing_endpoints_file_means_none(self):
        self.fixture('publish', 'endpoints = "publish/funnel-endpoints.json"\n')
        self.assertEqual([], self.engine.endpoints('publish'))


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.tmp = Path(self.temporary.name)
        self.state = self.tmp / 'state'
        self.state.mkdir()
        (self.tmp / 'bin').mkdir()
        self.taken = []

    def install(self, fqdn='desk.example.test'):
        if True:
            tcp = {str(port): {'HTTPS': True} for port in self.taken}
            fake = self.tmp / 'bin/tailscale'
            fake.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps({'TCP': tcp})}\nEOF\n")
            fake.chmod(0o755)
        env = {'PATH': f'{self.tmp}/bin:/usr/bin:/bin', 'HOME': str(self.tmp),
               'STATIONDESK_APP_DIR': str(APP), 'STATIONDESK_STATE': str(self.state),
               'STATIONDESK_SHARE': str(self.tmp / 'share'), 'STATIONDESK_FQDN': fqdn,
               'STATIONDESK_PORT': '19924'}
        subprocess.run(['bash', str(APP / 'install.sh')], env=env, check=True, capture_output=True)
        endpoints = json.loads((self.state / 'publish/funnel-endpoints.json').read_text())
        funnel_env = self.state / 'publish/funnel.env'
        return endpoints, funnel_env.read_text() if funnel_env.exists() else None

    def test_existing_company_skills_do_not_abort_publish_installer(self):
        for parent in ('.claude', '.agents'):
            path = self.tmp / parent / 'skills/share-docs'
            path.parent.mkdir(parents=True)
            path.symlink_to('/company/wiki/share-docs')
        self.assertEqual(10000, self.install()[0][0]['https_port'])
        for parent in ('.claude', '.agents'):
            path = self.tmp / parent / 'skills/share-docs'
            self.assertEqual('/company/wiki/share-docs', str(path.readlink()))

    def test_unconfigured_uses_funnel_10000_with_dedicated_dirs(self):
        endpoints, env = self.install()
        self.assertEqual([dict(https_port=10000, gate_port=19926, fragment='publish/funnel.conf', funnel=True)], endpoints)
        self.assertIn('STATIONDESK_PUBLISH_BASE_URL=https://desk.example.test:10000\n', env)
        self.assertIn(f'STATIONDESK_PUBLISH_PUBLIC_DIR={self.state}/publish/public\n', env)
        self.assertNotIn(str(self.tmp / 'share'), env)
        self.assertTrue((self.state / 'publish/funnel.conf').is_file())

    def test_falls_back_to_8443_and_never_443(self):
        self.taken = [443, 10000]
        endpoints, _ = self.install()
        self.assertEqual(8443, endpoints[0]['https_port'])
        self.taken = [443, 10000, 8443]
        (self.state / 'publish/funnel-endpoints.json').unlink()
        endpoints, env = self.install()
        self.assertEqual(([], None), (endpoints, env))

    def test_rerun_keeps_its_own_port(self):
        self.install()
        self.taken = [10000]  # our own mapping now occupies it
        endpoints, _ = self.install()
        self.assertEqual(10000, endpoints[0]['https_port'])

    def test_configured_target_opens_no_funnel(self):
        for line in ('STATIONDESK_PUBLISH_INGEST_URL=https://ingest.example.test',
                     'STATIONDESK_PUBLISH_PUBLIC_MODE=local', 'STATIONDESK_PUBLISH_BASE_URL=https://docs.example.test'):
            self.install()  # a prior funnel install is retired by configuring a target
            (self.state / 'publish.env').write_text(line + '\n')
            self.assertEqual(([], None), self.install())
            (self.state / 'publish.env').unlink()

    def test_commented_example_is_not_configuration(self):
        (self.state / 'publish.env').write_text('# STATIONDESK_PUBLISH_PUBLIC_MODE=local\n')
        self.assertEqual(10000, self.install()[0][0]['https_port'])

    def test_no_tailnet_means_no_funnel(self):
        self.assertEqual(([], None), self.install(fqdn=''))


class SurfaceTests(unittest.TestCase):
    def test_fragment_serves_only_snapshot_dirs_and_blocks_indexing(self):
        text = (APP / 'funnel.conf').read_text()
        self.assertIn('X-Robots-Tag "noindex', text)
        self.assertIn('X-Robots-Tag $robots', text)
        self.assertIn('"noindex, nofollow"', text)
        self.assertIn('.indexable', text)
        self.assertNotIn('autoindex on', text)
        self.assertNotIn('STATIONDESK_SHARE', text)
        self.assertIn('=404;', text)

    def test_status_reports_only_our_port(self):
        funnel = {'AllowFunnel': {'desk.example.test:10000': True}}
        done = subprocess.CompletedProcess([], 0, json.dumps(funnel), '')
        with patch.object(publish, 'FUNNEL_PORT', '10000'), patch.object(publish, '_funnel_cache', (0.0, None)), \
                patch.object(publish.subprocess, 'run', return_value=done):
            self.assertTrue(publish.funnel_status()['active'])
        with patch.object(publish, 'FUNNEL_PORT', '8443'), patch.object(publish, '_funnel_cache', (0.0, None)), \
                patch.object(publish.subprocess, 'run', return_value=done):
            self.assertFalse(publish.funnel_status()['active'])
        with patch.object(publish, 'FUNNEL_PORT', ''):
            self.assertIsNone(publish.funnel_status())


if __name__ == '__main__':
    unittest.main()
