"""Repository candidates and the existing CLI, with systemd owning execution."""
import json
from pathlib import Path
import re
import subprocess
import tomllib
import uuid
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from stationdesk_company import Catalog
from stationdesk_diagnostics import diagnostic_tail

from state_write import WRITE_LOCK

REQUIRED = {'hub', 'devterm', 'fileview', 'publish', 'dev-monitor'}


def command(args):
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


class Store:
    def __init__(self, root):
        self.root = Path(root)

    def installed(self):
        ledger = self.root / 'state/installed.json'
        return json.loads(ledger.read_text())['apps'] if ledger.exists() else {}

    def candidates(self):
        # Match the engine: apps first, with examples as its fallback source.
        directories = {p.parent.name: p.parent for p in sorted((self.root / 'examples').glob('*/app.toml'))}
        directories.update({p.parent.name: p.parent for p in sorted((self.root / 'apps').glob('*/app.toml'))})
        # Uninstalled company apps exist only as index rows until their bundle is unpacked.
        directories.update({key: None for key in Catalog(self.root).apps() if key not in directories})
        directories['hub'] = self.root / 'hub'
        directories.update({app_id: Path(app['dir']) for app_id, app in self.installed().items()
                            if app.get('dir') and app.get('kind') != 'link'})
        return directories

    def manifest(self, app_id):
        if not isinstance(app_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', app_id):
            raise ValueError('Invalid app id')
        directory = self.candidates()[app_id]
        if directory is None:
            row = Catalog(self.root).apps()[app_id]
            return None, dict(label=row['label'], icon=row.get('icon', ''), port=0,
                              description=row.get('description', ''))
        return directory, tomllib.loads((directory / 'app.toml').read_text())

    def list(self):
        installed = self.installed()
        apps = []
        for app_id in self.candidates():
            _, manifest = self.manifest(app_id)
            apps.append(dict(id=app_id, label=manifest['label'],
                             icon=('/monitor/api/store/icon?id=' + app_id) if manifest['icon'] else '',
                             kind='app', origin=self.origin(app_id),
                             installed=app_id in installed, required=app_id in REQUIRED))
        for app_id, app in installed.items():
            if app.get('kind') == 'link':
                apps.append(dict(id=app_id, label=app['label'], url=app['url'],
                                 icon='', kind='link', origin=app['origin'],
                                 installed=True, required=False))
        for app_id, link in Catalog(self.root).links().items():
            apps.append(dict(id=app_id, label=link['name'], url=link['url'], icon=link.get('icon', ''),
                             kind='link', origin='company', installed=app_id in Catalog(self.root).visible_links(), required=False))
        return {'apps': apps, 'company_enabled': Catalog(self.root).enabled}

    def origin(self, app_id):
        installed = self.installed().get(app_id)
        return installed.get('origin', 'builtin') if installed else 'company' if app_id in Catalog(self.root).apps() else 'builtin'

    def start(self, action, app_id=None):
        if action not in ('add', 'remove', 'update'):
            raise ValueError('Use add, remove or update')
        if app_id is not None and (not isinstance(app_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', app_id)):
            raise ValueError('Invalid app id')
        if app_id in Catalog(self.root).links():
            if action not in ('add', 'remove'):
                raise ValueError('회사 링크는 홈 표시 또는 해제만 할 수 있습니다.')
            return self.enqueue(['company-link', app_id, '--show' if action == 'add' else '--hide'])
        if app_id is not None and self.installed().get(app_id, {}).get('kind') == 'link':
            if action != 'remove':
                raise ValueError('Links only support remove')
            return self.enqueue(['link', 'remove', app_id])
        if action != 'update' or app_id is not None:
            self.manifest(app_id)
        if action == 'add' and app_id not in self.installed() and self.origin(app_id) == 'company':
            return self.enqueue(['add', 'company:' + app_id])
        return self.enqueue([action, *([app_id] if app_id is not None else [])])

    def add_source(self, source):
        if not isinstance(source, str) or not source.strip() or '\0' in source:
            raise ValueError('App source required')
        return self.enqueue(['add', source])

    def enqueue(self, arguments):
        with WRITE_LOCK:
            # Keep discovery + enqueue atomic across request threads. A oneshot stays
            # activating until the CLI exits; native After queues the next CLI behind it.
            units = command(['systemctl', '--user', 'list-units', '--all', '--plain',
                             '--no-legend', 'stationdesk-store-*.service'])
            earlier = [line.split()[0] for line in units.splitlines() if line.split()]
            ordering = ['--property=After=' + ' '.join(earlier)] if earlier else []
            unit = 'stationdesk-store-' + uuid.uuid4().hex + '.service'
            command(['systemd-run', '--user', '--collect', '--property=Type=oneshot', '--no-block', '--unit=' + unit,
                     *ordering, '--setenv=STATIONDESK_ROOT=' + str(self.root),
                     str(self.root / 'bin/stationdesk'), *arguments])
        return {'unit': unit}

    def progress(self, unit):
        if not re.fullmatch(r'stationdesk-store-[a-f0-9]{32}\.service', unit):
            raise ValueError('Invalid store unit')
        # --collect removes completed units. Their CLI output and terminal result
        # remain in the user journal; no web-owned execution record is needed.
        status = command(['systemctl', '--user', 'show', unit,
                          '-p', 'ActiveState', '-p', 'Result'])
        properties = dict(line.split('=', 1) for line in status.splitlines() if '=' in line)
        entries = command(['journalctl', '--user', '-u', unit, '--no-pager', '-o', 'json'])
        records = [json.loads(line) for line in entries.splitlines()]
        result = ''
        for record in records:
            if record.get('JOB_TYPE') == 'start' and record.get('JOB_RESULT'):
                result = 'success' if record['JOB_RESULT'] == 'done' else record['JOB_RESULT']
            if record.get('UNIT_RESULT'):
                result = record['UNIT_RESULT']
        active = (properties.get('ActiveState') in ('activating', 'active', 'deactivating', 'reloading')
                  or not result)
        messages = [str(row.get('MESSAGE', '')) for row in records
                    if row.get('SYSLOG_IDENTIFIER') != 'systemd'
                    and not row.get('UNIT_RESULT') and not row.get('JOB_TYPE')]
        return {'active': active, 'result': result,
                'output': diagnostic_tail('\n'.join(messages))}
