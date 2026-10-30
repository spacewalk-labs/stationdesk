"""Company catalog: index.json and icons fetched from the company distribution place.

Reading a catalog never touches the network; `fetch` is the one download of the index.
"""
import json
import os
from pathlib import Path
import tempfile
import tomllib
import urllib.request


def download(url):
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        stream.write(data)
    os.chmod(stream.name, 0o644)
    os.replace(stream.name, path)


class Catalog:
    def __init__(self, root):
        self.root = Path(root)
        self.directory = self.root / 'company'

    @staticmethod
    def read(path):
        return tomllib.loads(path.read_text()) if path.exists() else {}

    @property
    def url(self):
        return self.read(self.root / 'state/box.toml').get('company', '')

    @property
    def enabled(self):
        return bool(self.url)

    def fetch(self, url=None):
        """Download index.json and the icons it names. Installed bundles are untouched."""
        base = url or self.url
        index = json.loads(download(base + 'index.json'))
        for row in [*index.get('apps', []), *index.get('links', [])]:
            if row.get('icon'):
                path = (self.directory / row['icon']).resolve()
                if not path.is_relative_to(self.directory.resolve()):
                    raise ValueError(f'{row["id"]}: icon outside the catalog: {row["icon"]}')
                write(path, download(base + row['icon']))
        write(self.directory / 'index.json', (json.dumps(index, ensure_ascii=False, indent=2) + '\n').encode())

    def index(self):
        path = self.directory / 'index.json'
        return json.loads(path.read_text()) if self.enabled and path.exists() else {}

    def apps(self):
        return {row['id']: row for row in self.index().get('apps', [])}

    def links(self):
        links = {row['id']: dict(row) for row in self.index().get('links', [])}
        for app_id, row in links.items():
            icon = self.link_icon(row)
            if icon:
                row['icon'] = '/assets/company-links/' + app_id + icon.suffix
        return links

    def link_icon(self, row):
        value = row.get('icon', '')
        if not value:
            return None
        path = (self.directory / value).resolve()
        return path if path.is_relative_to(self.directory.resolve()) and path.is_file() else None

    def link_icons(self):
        return {row['id']: path for row in self.index().get('links', [])
                if (path := self.link_icon(row)) is not None}

    def visible_links(self):
        state = self.root / 'state/company-links.json'
        shown = json.loads(state.read_text()) if state.exists() else []
        return {key: row for key, row in self.links().items() if key in shown}
