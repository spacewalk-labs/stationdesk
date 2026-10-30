"""Read-only Linux observations; no installation or service mutations."""

import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time


_cpu_lock = threading.Lock()
_previous_cpu = None


def cpu_capacity():
    """Available cores, including fractional cgroup v2 quotas."""
    cores = min(os.cpu_count() or 1, len(os.sched_getaffinity(0)))
    cgroup = Path('/sys/fs/cgroup')
    cpuset = cgroup / 'cpuset.cpus.effective'
    if cpuset.is_file():
        ranges = cpuset.read_text().strip()
        if ranges:
            count = 0
            for group in ranges.split(','):
                bounds = group.split('-')
                count += int(bounds[-1]) - int(bounds[0]) + 1
            cores = min(cores, count)
    quota_file = cgroup / 'cpu.max'
    if quota_file.is_file():
        quota, period = quota_file.read_text().split()
        if quota != 'max':
            cores = min(cores, int(quota) / int(period))
    return cores


def cpu_info():
    """CPU delta: container cgroup when available, otherwise host /proc/stat."""
    global _previous_cpu
    cores = cpu_capacity()
    cgroup = Path('/sys/fs/cgroup/cpu.stat')
    if cgroup.is_file():
        values = dict(line.split() for line in cgroup.read_text().splitlines())
        # The mount root is the container cgroup, including child user units;
        # using /proc/self/cgroup would measure only this monitor's service.
        sample = ('cgroup', time.monotonic(), int(values['usage_usec']))
    else:
        fields = [int(value) for value in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]
        # guest times are already included in user/nice.
        sample = ('proc-stat-host', sum(fields[:8]), fields[3] + fields[4])
    percent = None
    with _cpu_lock:
        previous = _previous_cpu
        if previous and sample[0] == previous[0]:
            elapsed = sample[1] - previous[1]
            delta = sample[2] - previous[2]
            if elapsed > 0 and delta >= 0:
                fraction = (delta / elapsed / cores / 1_000_000
                            if sample[0] == 'cgroup' else 1 - delta / elapsed)
                percent = round(max(0, min(100, fraction * 100)), 1)
        _previous_cpu = sample
    return {'percent': percent, 'cores': cores, 'source': sample[0],
            'loadavg': list(os.getloadavg())}


def memory_info():
    values = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        values[key] = int(value.split()[0]) * 1024
    total = values['MemTotal']
    used = total - values.get('MemAvailable', values['MemFree'])
    return {'total_bytes': total, 'used_bytes': used,
            'percent': round(used * 100 / total, 1) if total else None,
            'swap_total_bytes': values['SwapTotal'],
            'swap_used_bytes': values['SwapTotal'] - values['SwapFree']}


def disk_info(root):
    stat = os.statvfs(root)
    total = stat.f_blocks * stat.f_frsize
    used = (stat.f_blocks - stat.f_bfree) * stat.f_frsize
    return {'path': str(root), 'total_bytes': total, 'used_bytes': used,
            'available_bytes': stat.f_bavail * stat.f_frsize,
            'percent': round(used * 100 / total, 1) if total else None}


def host_info():
    release = {}
    for line in Path('/etc/os-release').read_text().splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            release[key] = value.strip('"')
    return {'hostname': socket.gethostname(), 'os': release.get('PRETTY_NAME', ''),
            'kernel': os.uname().release,
            'uptime_seconds': int(float(Path('/proc/uptime').read_text().split()[0]))}


def network_info():
    interfaces = []
    for line in Path('/proc/net/dev').read_text().splitlines()[2:]:
        name, counters = line.split(':', 1)
        fields = counters.split()
        interfaces.append({'name': name.strip(), 'received_bytes': int(fields[0]),
                           'sent_bytes': int(fields[8])})
    listeners = set()
    for filename, family in [('tcp', socket.AF_INET), ('tcp6', socket.AF_INET6)]:
        path = Path('/proc/net') / filename
        if not path.exists():
            continue
        for line in path.read_text().splitlines()[1:]:
            fields = line.split()
            if fields[3] != '0A':
                continue
            address, port = fields[1].split(':')
            packed = bytes.fromhex(address)
            # /proc represents each address word in native byte order.
            if os.sys.byteorder == 'little':
                packed = b''.join(packed[i:i + 4][::-1] for i in range(0, len(packed), 4))
            listeners.add((socket.inet_ntop(family, packed), int(port, 16)))
    return {'interfaces': interfaces,
            'listen_ports': [{'ip': ip, 'port': port} for ip, port in sorted(listeners)]}


def services_info(root):
    ledger_path = root / 'state/installed.json'
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {'apps': {}}
    names = sorted({artifact['name'] for app in ledger['apps'].values()
                    for artifact in app['artifacts'] if artifact['kind'] == 'unit'})
    if not names:
        return []
    properties = ['Id', 'LoadState', 'ActiveState', 'SubState', 'Result', 'NRestarts']
    result = subprocess.run(['systemctl', '--user', 'show', '--no-pager',
                             '--property=' + ','.join(properties), '--', *names],
                            capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f'systemctl exited {result.returncode}')
    services = []
    for block in result.stdout.strip().split('\n\n'):
        values = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        if values:
            services.append({'name': values.get('Id', ''),
                             'load': values.get('LoadState', ''),
                             'active': values.get('ActiveState', ''),
                             'sub': values.get('SubState', ''),
                             'result': values.get('Result', ''),
                             'restarts': int(values.get('NRestarts', '0'))})
    return services


def collect(root: Path) -> dict:
    """A failed source remains visible as an error while other sections load."""
    payload = {'collected_at': time.time(), 'errors': {}}
    collectors = {'host': host_info, 'cpu': cpu_info, 'memory': memory_info,
                  'disk': lambda: disk_info(root), 'network': network_info,
                  'services': lambda: services_info(root)}
    for name, collector in collectors.items():
        try:
            payload[name] = collector()
        except (OSError, ValueError, KeyError, IndexError, RuntimeError,
                subprocess.TimeoutExpired) as error:
            payload[name] = None
            payload['errors'][name] = str(error)
    return payload
