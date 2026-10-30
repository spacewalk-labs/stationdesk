"""StationDesk observability, ported from the current Airlock monitor."""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from collectors import cpu_capacity

HOME = os.path.expanduser('~')
_STATE_DIR = str(Path(os.environ.get('STATIONDESK_ROOT', HOME + '/.local/share/stationdesk')) / 'state/dev-monitor')
HISTORY_CSV = os.path.join(_STATE_DIR, 'history.csv')
HISTORY_MAX_DAYS = 7

# ---- helpers ----
def read_proc(path, default=''):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def run(cmd, timeout=3):
    try:
        return subprocess.check_output(cmd, timeout=timeout, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ''


# ---- overview ----
def host_info():
    name = socket.gethostname()
    os_pretty = ''
    for line in read_proc('/etc/os-release').splitlines():
        if line.startswith('PRETTY_NAME='):
            os_pretty = line.split('=', 1)[1].strip().strip('"')
    kernel = read_proc('/proc/sys/kernel/osrelease')
    uptime_s = float(read_proc('/proc/uptime', '0').split()[0] or 0)
    return {
        'hostname': name,
        'os': os_pretty,
        'kernel': kernel,
        'uptime_seconds': int(uptime_s),
        'uptime_human': humanize_seconds(uptime_s),
    }


def humanize_seconds(s):
    s = int(s)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    parts = []
    if d: parts.append(f'{d}d')
    if h: parts.append(f'{h}h')
    if m or not parts: parts.append(f'{m}m')
    return ' '.join(parts)


_prev_cpu = {'usage_usec': 0, 'ts': 0.0, 'fallback_total': 0, 'fallback_idle': 0}


def _read_cgroup_cpu_usage_usec():
    """cgroup v2 cpu.stat usage_usec — this container's own cumulative CPU time (microsec)."""
    txt = read_proc('/sys/fs/cgroup/cpu.stat')
    for line in txt.splitlines():
        if line.startswith('usage_usec '):
            try:
                return int(line.split()[1])
            except (ValueError, IndexError):
                return None
    return None


def cpu_info():
    """This container's cpu % = cgroup cpu.stat delta / (wall_clock_delta x cores).

    100% = the available cgroup/affinity CPU capacity fully used. Falls back to
    /proc/stat (host-wide) when the cgroup v2 cpu.stat is unavailable.
    """
    global _prev_cpu
    cores = cpu_capacity()
    now_ts = time.time()
    usage_usec = _read_cgroup_cpu_usage_usec()
    pct = 0.0
    source = 'cgroup'
    if usage_usec is not None:
        if _prev_cpu['usage_usec'] > 0:
            wall_dt = now_ts - _prev_cpu['ts']
            usage_dt = usage_usec - _prev_cpu['usage_usec']
            if wall_dt > 0:
                # denominator = wall_clock(sec) x cores x 1e6 microsec/core/sec
                max_usec = wall_dt * cores * 1_000_000
                pct = round((usage_dt / max_usec) * 100, 1) if max_usec > 0 else 0
        _prev_cpu = {'usage_usec': usage_usec, 'ts': now_ts,
                     'fallback_total': _prev_cpu.get('fallback_total', 0),
                     'fallback_idle': _prev_cpu.get('fallback_idle', 0)}
    else:
        # fallback — host /proc/stat (host-wide when the container has no cpu quota)
        source = 'proc-stat-host'
        fields = read_proc('/proc/stat').splitlines()[0].split()[1:]
        user, nice, system, idle, iowait = (int(x) for x in fields[:5])
        total = sum(int(x) for x in fields)
        if _prev_cpu.get('fallback_total', 0) > 0:
            dt = total - _prev_cpu['fallback_total']
            di = (idle + iowait) - _prev_cpu['fallback_idle']
            if dt > 0:
                pct = round((1 - di / dt) * 100, 1)
        _prev_cpu['fallback_total'] = total
        _prev_cpu['fallback_idle'] = idle + iowait
        _prev_cpu['ts'] = now_ts
    loadavg = read_proc('/proc/loadavg').split()[:3]
    # cgroup quota (cpu.max) — the per-container CPU cap, if any.
    quota_str = read_proc('/sys/fs/cgroup/cpu.max').strip()
    quota_pct = None    # None = no quota (all cores available)
    if quota_str and not quota_str.startswith('max '):
        try:
            quota_us, period_us = quota_str.split()
            quota_us, period_us = int(quota_us), int(period_us)
            # quota = N% of one core. As a fraction of all cores: quota/period/cores x 100
            quota_pct = round(quota_us / period_us / cores * 100, 1) if period_us > 0 and cores > 0 else None
        except (ValueError, IndexError):
            pass
    return {
        'percent': pct,
        'loadavg': loadavg,
        'cores': cores,
        'source': source,           # 'cgroup' (this container) or 'proc-stat-host' (fallback)
        'quota_pct': quota_pct,     # None = unlimited / number = this container's cap (% of cores)
    }


def mem_info():
    info = {}
    for line in read_proc('/proc/meminfo').splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            info[k.strip()] = int(v.strip().split()[0])  # kB
    total = info.get('MemTotal', 0) * 1024
    avail = info.get('MemAvailable', info.get('MemFree', 0)) * 1024
    used = total - avail
    cache = info.get('Cached', 0) * 1024
    swap_total = info.get('SwapTotal', 0) * 1024
    swap_used = swap_total - info.get('SwapFree', 0) * 1024
    return {
        'used_bytes': used,
        'total_bytes': total,
        'cache_bytes': cache,
        'swap_used_bytes': swap_used,
        'swap_total_bytes': swap_total,
        'percent': round(used * 100 / total, 1) if total else 0,
    }


def disk_info(path='/'):
    try:
        st = os.statvfs(path)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        return {
            'path': path,
            'used_bytes': used,
            'total_bytes': total,
            'percent': round(used * 100 / total, 1) if total else 0,
        }
    except OSError:
        return {'path': path, 'used_bytes': 0, 'total_bytes': 0, 'percent': 0}


# ---- services ----
# System services are queried by fixed name (they need sudo to change, so they
# are shown read-only). User observation is broader than restart authority: private
# apps and timer jobs must remain visible, while writes stay restricted to StationDesk.
SYSTEM_SERVICES = ['ssh', 'tailscaled']
SELF_SERVICE = 'stationdesk-dev-monitor'
SERVICE_SHOW_PROPERTIES = (
    'Id', 'Type', 'LoadState', 'ActiveState', 'SubState', 'Result', 'NRestarts',
    'ExecMainStatus', 'UnitFileState', 'ActiveEnterTimestamp',
)
SERVICE_COMMAND_TIMEOUT = 1


def _service_command(cmd, allowed_nonzero=()):
    """Bound a service-panel probe and retain stdout even with a non-zero status."""
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=SERVICE_COMMAND_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return '', 'collection failed'
    if proc.returncode != 0:
        if proc.returncode in allowed_nonzero and proc.stdout.strip():
            return proc.stdout, None
        return proc.stdout, 'collection failed'
    return proc.stdout, None


def _observed_user_inventory():
    """Meaningful concrete user services plus collection health."""
    commands = [
        ['systemctl', '--user', 'list-unit-files', '--no-legend', '--type=service'],
        ['systemctl', '--user', 'list-units', '--all', '--no-legend', '--plain',
         '--type=service'],
        ['systemctl', '--user', 'list-timers', '--all', '--no-legend', '--plain'],
    ]
    units = []
    failed = False
    for index, command in enumerate(commands):
        output, error = _service_command(command)
        failed = failed or bool(error)
        for line in output.splitlines():
            parts = line.split()
            if not parts:
                continue
            if index == 2:
                # list-timers' final column is the activated service unit.
                name = parts[-1]
                include = name.endswith('.service')
            else:
                # Older systemctl can still prefix a failed unit with a bullet despite
                # --plain/--no-legend. Do not lose a failed template instance here.
                if parts[0] == '●' and len(parts) > 1:
                    parts = parts[1:]
                name = parts[0]
                state = parts[1] if index == 0 and len(parts) > 1 else ''
                active = parts[2] if index == 1 and len(parts) > 2 else ''
                include = (name.startswith('stationdesk-')
                           or state in ('enabled', 'enabled-runtime')
                           or active == 'failed')
            if include and name.endswith('.service') and '@.' not in name:
                units.append(name[:-len('.service')])
    return sorted(set(units)), ('inventory collection failed' if failed else None)


def _stationdesk_user_units():
    root = Path(os.environ['STATIONDESK_ROOT'])
    try:
        apps = json.loads((root / 'state/installed.json').read_text())['apps']
        return sorted({artifact['name'].removesuffix('.service')
                       for app in apps.values() for artifact in app['artifacts']
                       if artifact['kind'] == 'unit' and artifact['name'].endswith('.service')})
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _systemctl_show(names, scope):
    if isinstance(names, str):
        names = [names]
    if not names:
        return '', None
    cmd = ['systemctl']
    if scope == 'user':
        cmd.append('--user')
    cmd.extend([
        'show', '--no-pager',
        '--property=' + ','.join(SERVICE_SHOW_PROPERTIES), '--', *names,
    ])
    # `systemctl show` returns rc=3 for useful inactive-unit output. Inventory commands
    # do not: partial stdout with non-zero must remain fail-visible.
    return _service_command(cmd, allowed_nonzero=(3,))


def _show_blocks(raw):
    blocks = []
    current = []
    for line in raw.splitlines():
        if not line.strip():
            if current:
                blocks.append('\n'.join(current))
                current = []
        else:
            current.append(line)
    if current:
        blocks.append('\n'.join(current))
    return blocks


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _service_from_show(name, scope, raw, collection_error=None):
    props = {}
    for line in raw.splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            props[key] = value

    service_type = props.get('Type', '')
    load_state = props.get('LoadState', '')
    active_state = props.get('ActiveState', '')
    sub_state = props.get('SubState', '')
    result = props.get('Result', '')
    n_restarts = _as_int(props.get('NRestarts'))
    exec_main_status = _as_int(props.get('ExecMainStatus'))
    unit_file_state = props.get('UnitFileState', '')

    attention = False
    reason = ''
    required = ('LoadState', 'ActiveState', 'SubState', 'Result', 'NRestarts')
    if collection_error or any(key not in props for key in required):
        attention, reason = True, 'health collection failed'
    elif load_state != 'loaded':
        attention, reason = True, 'LoadState=' + (load_state or 'unknown')
    elif active_state == 'failed' or sub_state == 'failed':
        reason = 'failed'
        if result:
            reason += ': Result=' + result
        if exec_main_status not in (None, 0):
            reason += ', ExecMainStatus=' + str(exec_main_status)
        attention = True
    elif result and result != 'success':
        attention, reason = True, 'Result=' + result
    elif sub_state == 'auto-restart':
        attention, reason = True, 'SubState=auto-restart'
    elif n_restarts is None:
        attention, reason = True, 'NRestarts unavailable'
    elif n_restarts > 0:
        attention, reason = True, 'NRestarts=' + str(n_restarts)
    elif (service_type != 'oneshot' and active_state == 'inactive'
          and unit_file_state in ('enabled', 'enabled-runtime')):
        attention, reason = True, 'enabled service is inactive/' + (sub_state or 'unknown')

    return {
        'name': name,
        'scope': scope,
        # Compatibility for clients deployed before typed systemd health.
        'state': active_state or 'unknown',
        'uptime': uptime_from_timestamp(props.get('ActiveEnterTimestamp', '')),
        'type': service_type,
        'load_state': load_state or 'unknown',
        'active_state': active_state or 'unknown',
        'sub_state': sub_state or 'unknown',
        'result': result or 'unknown',
        'n_restarts': n_restarts,
        'exec_main_status': exec_main_status,
        'unit_file_state': unit_file_state or 'unknown',
        'attention': attention,
        'attention_reason': reason,
        # A synchronous restart from inside this service kills both the request
        # handler and its systemctl child before either can report success.
        # Observation never grants mutation. svc_info adds the explicit StationDesk-only
        # allowlist after classification; direct/failed/system rows remain read-only.
        'action_allowed': False,
    }


def _service_group_info(names, scope):
    """Resolve a scope in one bounded systemctl call, keyed by systemd's own Id."""
    raw, error = _systemctl_show(names, scope)
    if error:
        return [_service_from_show(name, scope, '', error) for name in names]
    by_name = {}
    for block in _show_blocks(raw):
        props = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        unit_id = props.get('Id', '')
        if unit_id.endswith('.service'):
            by_name[unit_id[:-len('.service')]] = block
    return [
        _service_from_show(
            name, scope, by_name.get(name, ''),
            None if name in by_name else 'collection failed')
        for name in names
    ]


def svc_info():
    user_units, inventory_error = _observed_user_inventory()
    out = _service_group_info(user_units, 'user')
    restartable = set(_stationdesk_user_units())
    for item in out:
        item['action_allowed'] = (item['name'] in restartable
                                  and item['name'] != SELF_SERVICE)
    if inventory_error:
        item = _service_from_show(
            'stationdesk-user-inventory', 'user', '', inventory_error)
        item['action_allowed'] = False
        out.append(item)
    out.extend(_service_group_info(SYSTEM_SERVICES, 'system'))
    return out


def _services_payload(services=None):
    services = svc_info() if services is None else services
    return {
        'services': services,
        'attention_count': sum(1 for item in services if item.get('attention')),
    }


def restart_svc(name):
    """Restart exactly one installed stationdesk user unit, never a system service."""
    if (not isinstance(name, str) or name == SELF_SERVICE
            or name not in set(_stationdesk_user_units())):
        return False, 'service restart is not allowed'
    try:
        subprocess.check_call(
            ['systemctl', '--user', 'restart', '--', name], timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, f'restarted: {name}'
    except subprocess.TimeoutExpired:
        return False, 'restart timeout'
    except (OSError, subprocess.CalledProcessError):
        return False, 'restart failed'


def uptime_from_timestamp(ts):
    if not ts:
        return ''
    try:
        # systemd format, e.g. 'Mon 2026-05-21 09:25:53 UTC'
        for fmt in ('%a %Y-%m-%d %H:%M:%S %Z', '%a %Y-%m-%d %H:%M:%S'):
            try:
                dt = datetime.strptime(ts.rsplit(' ', 1)[0] + ' ' + ts.rsplit(' ', 1)[1], fmt)
                seconds = (datetime.now() - dt.replace(tzinfo=None)).total_seconds()
                return humanize_seconds(seconds)
            except ValueError:
                continue
    except Exception:
        pass
    return ''


# ---- network ----
def network_info():
    ts_json = run(['tailscale', 'status', '--json'])
    self_ip = ''
    self_dns = ''
    peers = []
    if ts_json:
        try:
            d = json.loads(ts_json)
            self_ip = (d.get('Self', {}).get('TailscaleIPs') or [''])[0]
            self_dns = d.get('Self', {}).get('DNSName', '').rstrip('.')
            for p in d.get('Peer', {}).values():
                peers.append({
                    'name': p.get('HostName', ''),
                    'ip': (p.get('TailscaleIPs') or [''])[0],
                    'online': p.get('Online', False),
                })
        except Exception:
            pass

    listen = []
    # /proc/net/tcp parse — minimal subset (IPv4 LISTEN sockets)
    try:
        with open('/proc/net/tcp') as f:
            for line in f.readlines()[1:30]:
                fields = line.split()
                if len(fields) < 4:
                    continue
                local = fields[1]
                state = fields[3]
                if state != '0A':   # LISTEN
                    continue
                ip_hex, port_hex = local.split(':')
                port = int(port_hex, 16)
                ip = '.'.join(str(int(ip_hex[i:i+2], 16)) for i in (6, 4, 2, 0))
                listen.append({'port': port, 'ip': ip})
    except OSError:
        pass
    # dedupe
    seen = set()
    listen_uniq = []
    for it in sorted(listen, key=lambda x: x['port']):
        key = (it['port'], it['ip'])
        if key in seen: continue
        seen.add(key)
        listen_uniq.append(it)

    return {
        'tailscale': {
            'ip': self_ip,
            'dns': self_dns,
            'peer_count': len(peers),
            'peers': peers[:10],
        },
        'listen_ports': listen_uniq,
    }


# ---- storage ----
def du_quick(path):
    if not os.path.isdir(path):
        return None
    out = run(['du', '-sh', '--apparent-size', path], timeout=15)
    if not out:
        return None
    return out.split()[0]


def storage_info():
    items = []
    root = disk_info('/')
    items.append({'path': '/', 'bytes': root['used_bytes'], 'total_bytes': root['total_bytes'], 'human': du_quick('/') or ''})
    # Common per-user directories, if present (no assumptions about which exist).
    for sub in ['code', 'workspace', 'public_html', 'uploads', '.cache']:
        full = os.path.join(HOME, sub)
        if os.path.isdir(full):
            items.append({'path': f'~/{sub}', 'human': du_quick(full) or '(scan timeout)'})
    return items


# ---- history sampling (1-minute thread) ----
def history_sample_once():
    """Append one cpu/mem sample to the CSV."""
    cpu_info()   # delta sampling — the second call is accurate; the first primes _prev_cpu
    time.sleep(0.5)
    c = cpu_info()
    m = mem_info()
    ts = int(time.time())
    line = f'{ts},{c["percent"]},{m["percent"]}\n'
    try:
        with open(HISTORY_CSV, 'a') as f:
            f.write(line)
    except OSError as e:
        sys.stderr.write(f'[history] write fail: {e}\n')


def history_sampler():
    """1-minute sampling thread, started at boot."""
    while True:
        try:
            history_sample_once()
        except Exception as e:
            sys.stderr.write(f'[history] sample err: {e}\n')
        time.sleep(60)


def history_load(seconds_ago):
    """(ts, cpu, mem) tuples within the last seconds_ago .. now."""
    cutoff = int(time.time()) - seconds_ago
    rows = []
    if not os.path.exists(HISTORY_CSV):
        return rows
    try:
        with open(HISTORY_CSV) as f:
            for line in f:
                try:
                    ts, c, m = line.strip().split(',')
                    ts = int(ts)
                    if ts >= cutoff:
                        rows.append((ts, float(c), float(m)))
                except (ValueError, IndexError):
                    continue
    except OSError:
        pass
    return rows


def history_summary():
    """1h / 1d / 7d averages + peak cpu%/mem%."""
    def stats(rows):
        if not rows:
            return {'samples': 0}
        cpus = [r[1] for r in rows]
        mems = [r[2] for r in rows]
        return {
            'samples': len(rows),
            'cpu_avg': round(sum(cpus) / len(cpus), 1),
            'cpu_max': round(max(cpus), 1),
            'mem_avg': round(sum(mems) / len(mems), 1),
            'mem_max': round(max(mems), 1),
        }
    one_h = history_load(3600)
    one_d = history_load(86400)
    seven_d = history_load(86400 * 7)
    return {
        '1h': stats(one_h),
        '1d': stats(one_d),
        '7d': stats(seven_d),
    }


def history_trim():
    """Drop the oldest rows when the CSV grows past the retention window."""
    if not os.path.exists(HISTORY_CSV):
        return
    max_lines = HISTORY_MAX_DAYS * 1440 + 100
    try:
        with open(HISTORY_CSV) as f:
            lines = f.readlines()
        if len(lines) > max_lines:
            with open(HISTORY_CSV, 'w') as f:
                f.writelines(lines[-max_lines:])
    except OSError:
        pass


# ---- top processes (5-second live sampling, grouped by comm) ----
_TOP_LOCK = threading.Lock()
_TOP_CACHE = {'ts': 0, 'cpu': [], 'mem': [], 'total_mem_kb': 1, 'cores': 1}
_CLOCK_TICKS = os.sysconf('SC_CLK_TCK') if hasattr(os, 'sysconf') else 100


def _scan_procs():
    """/proc/*/stat utime+stime + /proc/*/status VmRSS + comm."""
    import pwd
    procs = {}
    try:
        pids = [d for d in os.listdir('/proc') if d.isdigit()]
    except OSError:
        return procs
    for pid in pids:
        try:
            with open(f'/proc/{pid}/stat') as f:
                line = f.read()
            rb = line.rfind(')')
            if rb < 0:
                continue
            fields = line[rb + 2:].split()
            # after ')': state(0), ppid(1), pgrp(2), session(3), tty_nr(4), tpgid(5), flags(6),
            # minflt(7), cminflt(8), majflt(9), cmajflt(10), utime(11), stime(12), ...
            if len(fields) < 13:
                continue
            utime = int(fields[11])
            stime = int(fields[12])
        except (OSError, ValueError):
            continue
        comm = line[line.find('(') + 1:rb]
        rss = 0
        try:
            with open(f'/proc/{pid}/status') as f:
                for line2 in f:
                    if line2.startswith('VmRSS:'):
                        rss = int(line2.split()[1])
                        break
        except (OSError, ValueError):
            pass
        user = 'unknown'
        try:
            uid = os.stat(f'/proc/{pid}').st_uid
            user = pwd.getpwuid(uid).pw_name
        except (OSError, KeyError):
            try:
                user = str(uid)
            except Exception:
                pass
        procs[int(pid)] = {'comm': comm, 'ticks': utime + stime, 'rss_kb': rss, 'user': user}
    return procs


def _top_sampler():
    """5-second sampling. cpu = delta jiffies / 5s / cores. mem = sum RSS. Grouped by comm."""
    global _TOP_CACHE
    prev = _scan_procs()
    cores = cpu_capacity()
    while True:
        time.sleep(5)
        try:
            cur = _scan_procs()
            mem_total_kb = 1
            try:
                with open('/proc/meminfo') as f:
                    for line in f:
                        if line.startswith('MemTotal:'):
                            mem_total_kb = int(line.split()[1])
                            break
            except OSError:
                pass
            groups = {}
            for pid, info in cur.items():
                prev_info = prev.get(pid)
                if not prev_info or prev_info['comm'] != info['comm']:
                    # new process — count mem now, cpu delta starts at 0
                    tick_delta = 0
                else:
                    tick_delta = info['ticks'] - prev_info['ticks']
                    if tick_delta < 0:
                        tick_delta = 0
                comm = info['comm']
                g = groups.setdefault(comm, {
                    'ticks_delta': 0, 'rss_kb_sum': 0, 'count': 0,
                    'user': info['user'], 'pid_sample': pid,
                })
                g['ticks_delta'] += tick_delta
                g['rss_kb_sum'] += info['rss_kb']
                g['count'] += 1
            sample_seconds = 5.0
            cpu_list = []
            mem_list = []
            for comm, g in groups.items():
                cpu_seconds = g['ticks_delta'] / _CLOCK_TICKS
                cpu_cores = round(cpu_seconds / sample_seconds, 3)
                cpu_pct = round(cpu_cores / cores * 100, 1) if cores > 0 else 0
                entry = {
                    'comm': comm, 'count': g['count'], 'user': g['user'],
                    'cpu_cores': cpu_cores, 'cpu_pct': cpu_pct,
                    'mem_bytes': g['rss_kb_sum'] * 1024,
                    'mem_pct': round(g['rss_kb_sum'] / mem_total_kb * 100, 1) if mem_total_kb else 0,
                    'pid_sample': g['pid_sample'],
                }
                if cpu_cores > 0.001:
                    cpu_list.append(entry)
                if g['rss_kb_sum'] > 0:
                    mem_list.append(entry)
            cpu_list.sort(key=lambda x: -x['cpu_cores'])
            mem_list.sort(key=lambda x: -x['mem_bytes'])
            with _TOP_LOCK:
                _TOP_CACHE = {
                    'ts': time.time(), 'cpu': cpu_list[:30], 'mem': mem_list[:30],
                    'total_mem_kb': mem_total_kb, 'cores': cores,
                }
            prev = cur
        except Exception as e:
            sys.stderr.write(f'[top_sampler] err: {e}\n')


def top_processes(n=10, sort_by='cpu'):
    """Return the 5-second sampling cache. Empty until the first window elapses."""
    with _TOP_LOCK:
        cache = dict(_TOP_CACHE)
    key = 'cpu' if sort_by == 'cpu' else 'mem'
    return cache.get(key, [])[:n]


# ---- recent logs (user units only — no sudo) ----
def recent_logs(unit='stationdesk-dev-monitor', n=10):
    out = run(['journalctl', '--user', '-u', unit, '-n', str(n), '--no-pager', '-o', 'short-iso'])
    lines = []
    for line in out.splitlines()[-n:]:
        lines.append(line.strip())
    return lines




def start():
    Path(_STATE_DIR).mkdir(parents=True, exist_ok=True)
    history_trim()
    for target in (history_sampler, _top_sampler):
        threading.Thread(target=target, daemon=True).start()
