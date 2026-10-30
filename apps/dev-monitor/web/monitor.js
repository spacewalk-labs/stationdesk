'use strict';

const t = (en, ko) => window.StationDeskI18n ? window.StationDeskI18n.t(en, ko) : en;

const byId = id => document.getElementById(id);
const text = (id, value) => { byId(id).textContent = value; };
const percent = value => value == null ? '—' : `${value.toFixed(1)}%`;
function bytes(value) {
  if (value == null) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
  return `${value.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}
function rows(id, values, empty) {
  const body = byId(id);
  body.replaceChildren();
  for (const valuesInRow of values.length ? values : [[empty]]) {
    const row = document.createElement('tr');
    for (const value of valuesInRow) {
      const cell = document.createElement('td');
      cell.textContent = value;
      row.append(cell);
    }
    body.append(row);
  }
}
function render(data) {
  const {host, cpu, memory, disk, network, services} = data;
  if (host) {
    const minutes = Math.floor(host.uptime_seconds / 60);
    text('host', `${host.hostname} · ${host.os} · ${host.kernel} · ${t('uptime', '가동')} ${Math.floor(minutes / 1440)}${t('d', '일')} ${Math.floor(minutes / 60) % 24}${t('h', '시간')} ${minutes % 60}${t('m', '분')}`);
  } else text('host', t('Could not collect system information', '시스템 정보 수집 실패'));
  text('cpu', percent(cpu?.percent));
  text('cpu-detail', cpu ? `${cpu.cores}${t(' cores', '코어')} · ${cpu.source === 'cgroup' ? t('Container CPU', '컨테이너 CPU') : t('Host CPU', '호스트 CPU')} · ${t('load', '부하')} ${cpu.loadavg.map(v => v.toFixed(2)).join(' / ')}${cpu.percent == null ? t(' · usage available on next refresh', ' · 다음 갱신부터 사용률 표시') : ''}` : t('Collection failed', '수집 실패'));
  text('memory', percent(memory?.percent));
  text('memory-detail', memory ? `${bytes(memory.used_bytes)} / ${bytes(memory.total_bytes)} · ${t('swap', '스왑')} ${bytes(memory.swap_used_bytes)} / ${bytes(memory.swap_total_bytes)}` : t('Collection failed', '수집 실패'));
  text('disk', percent(disk?.percent));
  text('disk-detail', disk ? `${bytes(disk.used_bytes)} / ${bytes(disk.total_bytes)} · ${disk.path}` : t('Collection failed', '수집 실패'));
  rows('services', (services || []).map(s => [s.name, `${s.active} (${s.sub}) · ${s.load}`, s.result || '—', s.restarts]), services ? t('No registered units.', '등록된 유닛이 없습니다.') : t('Could not collect services', '서비스 수집 실패'));
  rows('interfaces', (network?.interfaces || []).map(n => [n.name, bytes(n.received_bytes), bytes(n.sent_bytes)]), network ? t('No interfaces.', '인터페이스가 없습니다.') : t('Could not collect network information', '네트워크 수집 실패'));
  text('listeners', network ? `${t('TCP listeners:', 'TCP 수신 대기:')} ${network.listen_ports.map(p => `${p.ip.includes(':') ? `[${p.ip}]` : p.ip}:${p.port}`).join(', ') || t('none', '없음')}` : '');
  const errors = Object.entries(data.errors || {}).map(([name, error]) => `${name}: ${error}`).join('\n');
  text('errors', errors);
  byId('errors').hidden = !errors;
  text('status', `${new Date(data.collected_at * 1000).toLocaleTimeString()} ${t('updated · refreshes every 5 seconds', '갱신 · 5초마다 갱신')}${errors ? t(' · some collection failed', ' · 일부 수집 실패') : ''}`);
}
async function refresh() {
  try {
    const response = await fetch('/monitor/api/system', {cache: 'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch (error) {
    text('status', `${t('Refresh failed:', '갱신 실패:')} ${error.message} · ${t('Keeping the last display. Retrying in 5 seconds.', '마지막 표시를 유지합니다. 5초 후 다시 시도합니다.')}`);
  } finally {
    setTimeout(refresh, 5000);
  }
}
refresh();
