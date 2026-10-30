"""Refresh the tracked repository refs and the company index daily without blocking owner HTTP reads."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import threading

from store import Catalog


def refresh(root):
    root = Path(root)
    path = root / 'state/updates-fetch.json'
    previous = json.loads(path.read_text()) if path.exists() else {}
    snapshot = dict(previous, attemptedAt=datetime.now(timezone.utc).isoformat())
    error = None
    try:
        ledger = root / 'state/installed.json'
        apps = json.loads(ledger.read_text())['apps'] if ledger.exists() else {}
        repos = {str(root), *(entry['repo'] for entry in apps.values() if entry.get('repo'))}
        for repo in sorted(repos):
            result = subprocess.run(['git', 'fetch', '--quiet'], cwd=repo,
                                env=os.environ | {'GIT_TERMINAL_PROMPT': '0'},
                                capture_output=True, timeout=30)
            if result.returncode:
                error = 'git fetch failed: %s (exit %s)' % (Path(repo).name, result.returncode)
                break
    except (OSError, subprocess.TimeoutExpired) as failure:
        error = 'git fetch timed out' if isinstance(failure, subprocess.TimeoutExpired) else 'git fetch unavailable'
    # The company place is a separate source: a git failure must not hide its updates, nor the reverse.
    catalog = Catalog(root)
    if catalog.enabled:
        try:
            catalog.fetch()
        except (OSError, ValueError, KeyError) as failure:
            error = error or 'company index unavailable: %s' % failure
    if error:
        snapshot['error'] = error
    else:
        snapshot.update(checkedAt=snapshot['attemptedAt'], error=None)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(snapshot) + '\n')
    temporary.replace(path)
    return snapshot


def start(root):
    stop = threading.Event()

    def run():
        while not stop.is_set():
            snapshot = refresh(root)
            # A failed check (network, company place offline) retries within the hour.
            if stop.wait(60 * 60 if snapshot.get('error') else 24 * 60 * 60):
                break

    threading.Thread(target=run, name='stationdesk-updates', daemon=True).start()
    return stop
