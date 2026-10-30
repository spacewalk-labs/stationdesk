#!/usr/bin/env bash
set -euo pipefail
web="$STATIONDESK_ROOT/web/bench"
mkdir -p "$STATIONDESK_ROOT/web"
work=$(mktemp -d "$STATIONDESK_ROOT/web/.bench.XXXXXX")
trap 'rm -rf "$work"' EXIT
python3 - "$STATIONDESK_APP_DIR" "$work" "${STATIONDESK_BENCH_APPS:-}" <<'PY'
import hashlib
import html
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

source, work = map(Path, sys.argv[1:3])
selection = sys.argv[3]
if selection:
    selected = list(dict.fromkeys(selection.split(',')))
catalog = {}
for line in (source / 'VERSION').read_text().splitlines():
    if line and not line.startswith('#'):
        app_id, version, url, sha = line.split()
        catalog[app_id] = version, url, sha
if not selection:
    selected = list(catalog)
descriptions = {}
for line in (source / 'apps.tsv').read_text().splitlines():
    if line and not line.startswith('#'):
        app_id, name, desc = line.split('\t')
        descriptions[app_id] = name, desc
unknown = set(selected) - catalog.keys()
if unknown:
    raise SystemExit('Unknown Bench app: ' + ', '.join(sorted(unknown)))
apps = []
for app_id in selected:
    version, url, sha = catalog[app_id]
    archive = work / 'download.zip'
    subprocess.run(['curl', '-fLsS', '--retry', '2', '--max-time', '180',
                    '-o', str(archive), url], check=True)
    actual = hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest()
    if actual != sha:
        raise SystemExit(f'{app_id}: SHA256 mismatch ({actual})')
    print(f'{app_id} {version}: sha256 OK', flush=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(work / 'extract')
    shutil.move(work / 'extract' / f'{app_id}-web-{version}', work / app_id)
    shutil.rmtree(work / 'extract')
    archive.unlink()
    name, desc = descriptions[app_id]
    apps.append(dict(id=app_id, name=name, desc=desc,
                     version=version, sha256=sha))
(work / 'apps.json').write_text(json.dumps({'apps': apps}, ensure_ascii=False, indent=2) + '\n')
page = (source / 'index.html').read_text()
page = page.replace('@@ATTRIBUTION@@', html.escape((source / 'README.md').read_text().splitlines()[-1]))
(work / 'index.html').write_text(page)
shutil.copyfile(source / 'icon.png', work / 'icon.png')
shutil.copytree(source / 'icons', work / 'icons')
PY
# The staged bundle may be identical even though the installer ran again.
if [[ -d "$web" ]] && diff -qr "$work" "$web" >/dev/null; then exit 0; fi
rm -rf "$web"
mv "$work" "$web"
trap - EXIT
