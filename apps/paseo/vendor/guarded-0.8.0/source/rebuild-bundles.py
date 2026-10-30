#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Recreate the seven archives from a built, patched upstream checkout."""
import gzip
import hashlib
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

here = Path(__file__).resolve().parent
build = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()
out.mkdir(parents=True, exist_ok=True)
names = ['cli', 'client', 'highlight', 'plugin', 'protocol', 'relay', 'server']
original_hashes = {name: digest for digest, name in (line.split() for line in (here / 'ORIGINAL_SHA256SUMS').read_text().splitlines())}
overlays = (here / 'compiled-files.txt').read_text().splitlines()
with tempfile.TemporaryDirectory(prefix='paseo-rebuild-', dir=out) as directory:
    stage = Path(directory)
    for name in names:
        filename = f'getpaseo-{name}-0.8.0.tgz'
        url = f'https://registry.npmjs.org/@getpaseo/{name}/-/{name}-0.8.0.tgz'
        data = urllib.request.urlopen(url, timeout=120).read()
        assert hashlib.sha256(data).hexdigest() == original_hashes[filename], filename
        (out / filename).write_bytes(data)
        if name in ['client', 'protocol', 'server']:
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
                archive.extractall(stage / name, filter='data')
    for line in overlays:
        name, member = line.split(' ', 1)
        target = stage / name / 'package' / member
        shutil.copyfile(build / 'packages' / name / member, target)
    subprocess.run(['git', 'apply', '--no-index', str(here / 'distribution.patch')], cwd=stage, check=True)
    for name in ['client', 'protocol', 'server']:
        package = stage / name / 'package'
        shutil.copyfile(here.parent / 'UPSTREAM-LICENSE', package / 'LICENSE-Paseo-Apache-2.0')
        shutil.copyfile(here.parent / 'LICENSE', package / 'LICENSE-StationDesk-AGPL-3.0')
        shutil.copyfile(here / 'PACKAGE-NOTICE', package / 'STATIONDESK-NOTICE')
        if name == 'server':
            shutil.copyfile(here.parent / 'WEB-THIRD-PARTY-LICENSES.txt', package / 'WEB-THIRD-PARTY-LICENSES.txt')
        with (out / f'getpaseo-{name}-0.8.0.tgz').open('wb') as raw:
            with gzip.GzipFile(fileobj=raw, filename='', mode='wb', mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as archive:
                    for path in sorted((stage / name / 'package').rglob('*')):
                        if path.is_file():
                            info = archive.gettarinfo(str(path), str(path.relative_to(stage / name)))
                            info.uid = info.gid = 0
                            info.uname = info.gname = ''
                            info.mtime = 0
                            with path.open('rb') as stream:
                                archive.addfile(info, stream)
    installed = []
    for line in (here.parent / 'INSTALLED_SHA256SUMS').read_text().splitlines():
        _, path = line.split(maxsplit=1)
        _, name, member = path.split('/', 2)
        digest = hashlib.sha256((stage / name / 'package' / member).read_bytes()).hexdigest()
        installed.append(f'{digest}  {path}\n')
    (out / 'INSTALLED_SHA256SUMS').write_text(''.join(installed))
checksums = []
for name in names:
    filename = f'getpaseo-{name}-0.8.0.tgz'
    checksums.append(f'{hashlib.sha256((out / filename).read_bytes()).hexdigest()}  {filename}\n')
(out / 'SHA256SUMS').write_text(''.join(checksums))
print(f'Rebuilt archives and checksum manifests: {out}')
