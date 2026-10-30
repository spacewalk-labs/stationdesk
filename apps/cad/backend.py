#!/usr/bin/env python3
"""Box-local drawing storage and synchronous DWG conversion (standard library only)."""
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import signal
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from urllib.parse import parse_qs, quote, urlsplit

# CadApp::MAX_OPEN_BYTES, CADCraft e707ab4 crates/ui-egui/src/lib.rs.
MAX_OPEN_BYTES = 256 << 20


class RequestError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def atomic_write(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.cad-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class Storage:
    def __init__(self, root=None, cli=None):
        self.root = Path(root if root is not None else Path.home() / 'cad').resolve()
        self.cli = Path(cli if cli is not None else Path(os.environ.get('STATIONDESK_ROOT', '/opt/stationdesk')) / 'runtime/cad/cadcraft-cli')
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def path(self, value, folder=False, upload=False):
        if not isinstance(value, str):
            raise RequestError('Use a project folder and drawing filename.')
        parts = value.split('/')
        if (len(parts) != (1 if folder else 2) or any(p in ('', '.', '..') for p in parts)
                or '\\' in value or '\x00' in value):
            raise RequestError('Use a project folder and drawing filename.')
        if parts[0] == '.trash':
            raise RequestError('The trash folder is not available through drawing paths.')
        path = self.root.joinpath(*parts)
        try:
            resolved = path.resolve()
            resolved.relative_to(self.root)
        except (ValueError, RuntimeError):
            raise RequestError('Path must remain inside the drawing folder.')
        try:
            resolved.relative_to((self.root / '.trash').resolve())
        except ValueError:
            pass
        else:
            raise RequestError('The trash folder is not available through drawing paths.')
        if not folder and path.suffix.lower() != '.dxf' and not (upload and path.suffix.lower() == '.dwg'):
            raise RequestError('Only DXF drawings are supported.')
        return path

    def listing(self):
        folders = []
        with self.lock:
            for folder in sorted(self.root.iterdir()):
                if not folder.is_dir():
                    continue
                try:
                    self.path(folder.name, folder=True)
                except RequestError:
                    continue
                files = []
                for item in sorted(folder.iterdir()):
                    try:
                        self.path(folder.name + '/' + item.name)
                    except RequestError:
                        continue
                    if item.is_file():
                        files.append({'name': item.name, 'kind': item.suffix.lower()[1:]})
                folders.append({'name': folder.name, 'files': files})
        return {'folders': folders}

    def folder(self, value):
        with self.lock:
            path = self.path(value, folder=True)
            path.mkdir(exist_ok=True)
            return {'created': value}

    @staticmethod
    def _entry(value):
        if not isinstance(value, str):
            raise RequestError('Use a project folder or drawing path.')
        folder = '/' not in value
        return folder, value

    @staticmethod
    def _exists(path):
        return path.exists() or path.is_symlink()

    @staticmethod
    def _require_entry(path, folder):
        if not Storage._exists(path):
            raise RequestError('Drawing or project folder not found.', 404)
        if folder and not path.is_dir() and not path.is_symlink():
            raise RequestError('Project folder not found.', 404)
        if not folder and not path.is_file() and not path.is_symlink():
            raise RequestError('Drawing not found.', 404)

    def rename(self, from_value, to_value):
        with self.lock:
            from_folder, from_value = self._entry(from_value)
            to_folder, to_value = self._entry(to_value)
            source = self.path(from_value, folder=from_folder)
            target = self.path(to_value, folder=to_folder)
            if from_folder != to_folder:
                raise RequestError('Rename must keep the same path type.')
            if not from_folder:
                source_parent, source_name = from_value.rsplit('/', 1)
                target_parent, target_name = to_value.rsplit('/', 1)
                if source_parent != target_parent:
                    raise RequestError('Drawing rename must stay in the same project folder.')
                if Path(source_name).suffix != Path(target_name).suffix:
                    raise RequestError('Drawing rename must keep its extension.')
            self._require_entry(source, from_folder)
            if from_value == to_value:
                return {'renamed': to_value}
            if self._exists(target):
                raise RequestError(f'{target.name} already exists', 409)

            os.rename(source, target)
            return {'renamed': to_value}

    def _trash_root(self, create=False):
        trash = self.root / '.trash'
        if trash.is_symlink():
            raise RequestError('The trash folder is not available.')
        if create and not trash.exists():
            trash.mkdir()
        if trash.exists() and not trash.is_dir():
            raise RequestError('The trash folder is not available.')
        try:
            trash.resolve().relative_to(self.root)
        except (ValueError, RuntimeError):
            raise RequestError('The trash folder is not available.')
        return trash

    def delete(self, value):
        with self.lock:
            folder, value = self._entry(value)
            source = self.path(value, folder=folder)
            self._require_entry(source, folder)
            trash = self._trash_root(create=True)
            stamp = datetime.now(timezone.utc)
            while True:
                timestamp = stamp.strftime('%Y%m%dT%H%M%S.%fZ')
                bucket = trash / timestamp
                try:
                    bucket.mkdir()
                    break
                except FileExistsError:
                    stamp += timedelta(microseconds=1)
            if folder:
                target = bucket / source.name
            else:
                project = bucket / source.parent.name
                project.mkdir()
                target = project / source.name
            os.rename(source, target)
            return {'trashed': target.relative_to(self.root).as_posix(), 'from': value}

    def restore(self, trashed_value, to_value):
        with self.lock:
            if not isinstance(trashed_value, str):
                raise RequestError('Use a trash path returned by delete.')
            parts = trashed_value.split('/')
            if (len(parts) not in (3, 4) or parts[0] != '.trash'
                    or any(part in ('', '.', '..') for part in parts)
                    or '\\' in trashed_value or '\x00' in trashed_value):
                raise RequestError('Use a trash path returned by delete.')
            trash = self._trash_root()
            bucket = trash / parts[1]
            if bucket.is_symlink():
                raise RequestError('The trash path is not available.')
            source = bucket.joinpath(*parts[2:])
            try:
                source.parent.resolve().relative_to(trash.resolve())
            except (ValueError, RuntimeError):
                raise RequestError('The trash path is not available.')
            if not bucket.is_dir():
                raise RequestError('Drawing or project folder not found.', 404)

            folder = len(parts) == 3
            if folder:
                self.path(parts[2], folder=True)
            else:
                self.path(parts[2] + '/' + parts[3])
            self._require_entry(source, folder)

            to_folder, to_value = self._entry(to_value)
            target = self.path(to_value, folder=to_folder)
            if to_value != '/'.join(parts[2:]):
                raise RequestError('Restore must use the original path returned by delete.')
            if self._exists(target):
                raise RequestError(f'{target.name} already exists', 409)
            if not target.parent.is_dir():
                raise RequestError('Project folder does not exist.', 404)
            os.rename(source, target)
            return {'restored': to_value}

    def _upload_target(self, value):
        target = self.path(value)
        if not self._exists(target):
            return value, target
        folder, name = value.rsplit('/', 1)
        path = Path(name)
        number = 1
        while True:
            candidate_name = f'{path.stem} ({number}){path.suffix}'
            candidate_value = folder + '/' + candidate_name
            candidate = self.path(candidate_value)
            if not self._exists(candidate):
                return candidate_value, candidate
            number += 1

    def save(self, value, data, upload=False):
        if len(data) > MAX_OPEN_BYTES:
            raise RequestError('Drawing exceeds CADCraft’s 256 MB opening limit.', 413)
        with self.lock:
            source = self.path(value, upload=True)
            if not source.parent.is_dir():
                raise RequestError('Project folder does not exist.', 404)
            if source.suffix.lower() == '.dxf' and not upload:
                atomic_write(source, data)
                return {'saved': value}
            target_value = str(Path(value).with_suffix('.dxf')) if source.suffix.lower() == '.dwg' else value
            with tempfile.TemporaryDirectory(prefix='.cad-convert-', dir=source.parent) as work:
                input_path = Path(work) / source.name
                input_path.write_bytes(data)
                output = input_path
                if source.suffix.lower() == '.dwg':
                    output = Path(work) / 'result.dxf'
                    subprocess.run([str(self.cli), 'convert', str(input_path), str(output)],
                                   timeout=60, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    if output.is_symlink() or not output.is_file() or output.stat().st_size == 0:
                        raise RequestError('Converter did not produce a DXF.', 502)
                    if output.stat().st_size > MAX_OPEN_BYTES:
                        raise RequestError('Converted drawing exceeds CADCraft’s 256 MB opening limit.', 413)
                while True:
                    saved, target = self._upload_target(target_value)
                    try:
                        os.link(output, target)
                    except FileExistsError:
                        continue
                    return {'saved': saved}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.handle_request()

    def do_PUT(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def handle_request(self):
        try:
            if self.command in ('POST', 'PUT'):
                origin = urlsplit(self.headers.get('Origin', ''))
                # nginx preserves Host; TLS may terminate before its loopback proxy.
                if self.headers.get('Origin') is not None and not (
                        origin.scheme in ('http', 'https') and not origin.path
                        and not origin.query and not origin.fragment and origin.netloc
                        and origin.netloc.lower() == self.headers.get('Host', '').lower()):
                    raise RequestError('Same-origin requests only.', 403)
            url = urlsplit(self.path)
            route = url.path.removeprefix('/cad/api').removeprefix('/api')
            query = parse_qs(url.query)
            value = query.get('path', [''])[0]
            storage = self.server.storage
            if self.command == 'GET' and route in ('/', '/list'):
                return self.respond(200, storage.listing())
            if self.command == 'GET' and route == '/file':
                with storage.lock:
                    path = storage.path(value)
                    data = path.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                if query.get('download') == ['1']:
                    self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(path.name))
                self.end_headers()
                self.wfile.write(data)
                return
            if self.command == 'PUT' and route == '/file':
                try:
                    length = int(self.headers.get('Content-Length', '-1'))
                except ValueError:
                    raise RequestError('Content-Length is required.', 411)
                if length < 0:
                    raise RequestError('Content-Length is required.', 411)
                if length > MAX_OPEN_BYTES:
                    raise RequestError('Drawing exceeds CADCraft’s 256 MB opening limit.', 413)
                data = self.rfile.read(length)
                if len(data) != length:
                    raise RequestError('Incomplete upload.')
                return self.respond(200, storage.save(value, data, upload=query.get('upload') == ['1']))
            if self.command == 'POST' and route == '/folder':
                return self.respond(200, storage.folder(value))
            if self.command == 'POST' and route == '/rename':
                payload = self.read_json()
                if not isinstance(payload.get('from'), str) or not isinstance(payload.get('to'), str):
                    raise RequestError('Expected from and to paths.')
                return self.respond(200, storage.rename(payload['from'], payload['to']))
            if self.command == 'POST' and route == '/delete':
                payload = self.read_json()
                if not isinstance(payload.get('path'), str):
                    raise RequestError('Expected a drawing or project path.')
                return self.respond(200, storage.delete(payload['path']))
            if self.command == 'POST' and route == '/restore':
                payload = self.read_json()
                if not isinstance(payload.get('trashed'), str) or not isinstance(payload.get('to'), str):
                    raise RequestError('Expected trashed and to paths.')
                return self.respond(200, storage.restore(payload['trashed'], payload['to']))
            raise RequestError('Unknown endpoint.', 404)
        except RequestError as error:
            self.respond(error.status, {'error': str(error)})
        except FileNotFoundError:
            self.respond(404, {'error': 'Drawing or converter not found.'})
        except (subprocess.SubprocessError, OSError, ValueError) as error:
            self.log_error('%s', error)
            self.respond(500, {'error': 'The drawing could not be saved or converted.'})

    def read_json(self):
        if self.headers.get_content_type() != 'application/json':
            raise RequestError('Content-Type must be application/json.', 415)
        length = int(self.headers.get('Content-Length', '0'))
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise RequestError('Expected a JSON object.')
        return payload

    def respond(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)


def main():
    server = ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('STATIONDESK_PORT', '19960'))), Handler)
    def terminate(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    server.storage = Storage()
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
