#!/usr/bin/env python3
"""Storage and HTTP regression tests; real conversion uses the installed CLI."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
from unittest import mock

spec = importlib.util.spec_from_file_location('cad_backend', Path(__file__).with_name('backend.py'))
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)

DXF = b'0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1015\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n0\nLINE\n8\n0\n10\n0\n20\n0\n30\n0\n11\n100\n21\n100\n31\n0\n0\nENDSEC\n0\nEOF\n'
CLI = Path(os.environ.get('CAD_TEST_CLI', str(Path(os.environ.get('STATIONDESK_ROOT', '/opt/stationdesk')) / 'runtime/cad/cadcraft-cli')))


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.storage = backend.Storage(self.base / 'cad', CLI)
        self.storage.folder('project')

    def test_save_overwrite_and_listing(self):
        self.assertEqual(self.storage.save('project/도면.dxf', DXF), {'saved': 'project/도면.dxf'})
        self.storage.save('project/도면.dxf', DXF + b'\n')
        self.assertEqual((self.storage.root / 'project/도면.dxf').read_bytes(), DXF + b'\n')
        self.assertEqual(self.storage.listing(), {'folders': [{'name': 'project', 'files': [{'name': '도면.dxf', 'kind': 'dxf'}]}]})
        self.assertEqual(list((self.storage.root / 'project').glob('.cad-*')), [])

    def test_paths_stay_inside_root_and_one_project_level(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (self.storage.root / 'escape').symlink_to(outside, target_is_directory=True)
        (self.storage.root / 'project/link.dxf').symlink_to(outside / 'secret.dxf')
        for value in ('../bad.dxf', '/tmp/bad.dxf', 'project/../bad.dxf',
                      'escape/bad.dxf', 'project/link.dxf', 'project/sub/bad.dxf'):
            with self.subTest(value=value), self.assertRaises(backend.RequestError):
                self.storage.save(value, DXF)
        self.assertFalse((outside / 'bad.dxf').exists())
        self.assertEqual([folder['name'] for folder in self.storage.listing()['folders']], ['project'])

    def test_http_save_download_and_error(self):
        server = backend.ThreadingHTTPServer(('127.0.0.1', 0), backend.Handler)
        server.storage = self.storage
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        origin = f'http://127.0.0.1:{server.server_port}/cad/api'
        path = quote('project/도면.dxf')
        with urlopen(Request(origin + '/file?path=' + path + '&upload=1', data=DXF, method='PUT')) as response:
            self.assertEqual(json.load(response)['saved'], 'project/도면.dxf')
        with urlopen(origin + '/file?path=' + path + '&download=1') as response:
            self.assertEqual(response.read(), DXF)
            self.assertIn('attachment;', response.headers['Content-Disposition'])
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(origin + '/file?path=../bad.dxf', data=DXF, method='PUT'))
        self.assertEqual(error.exception.code, 400)
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(origin + '/file?path=' + path, data=b'', method='PUT',
                            headers={'Content-Length': str(backend.MAX_OPEN_BYTES + 1)}))
        self.assertEqual(error.exception.code, 413)

    def start_http(self):
        server = backend.ThreadingHTTPServer(('127.0.0.1', 0), backend.Handler)
        server.storage = self.storage
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}/cad/api'

    @staticmethod
    def post(origin, route, payload):
        request = Request(origin + route, data=json.dumps(payload).encode(), method='POST',
                          headers={'Content-Type': 'application/json'})
        with urlopen(request) as response:
            return json.load(response)

    @staticmethod
    def put_upload(origin, path, data):
        request = Request(origin + '/file?path=' + quote(path) + '&upload=1',
                          data=data, method='PUT')
        with urlopen(request) as response:
            return json.load(response)

    @staticmethod
    def get_file(origin, path):
        with urlopen(origin + '/file?path=' + quote(path)) as response:
            return response.read()

    def test_http_mutations_reject_foreign_origin_before_storage_changes(self):
        api = self.start_http()
        drawing = self.storage.root / 'project/source.dxf'
        drawing.write_bytes(DXF)
        for method, route, data in [
            ('POST', '/folder?path=foreign', b''),
            ('POST', '/rename', b'{"from":"project/source.dxf","to":"project/renamed.dxf"}'),
            ('POST', '/delete', b'{"path":"project/source.dxf"}'),
            ('POST', '/restore', b'{"trashed":".trash/bucket/project/source.dxf","to":"project/source.dxf"}'),
            ('PUT', '/file?path=project/source.dxf', b'changed'),
        ]:
            for content_type in ('text/plain', 'application/json'):
                with self.subTest(route=route, content_type=content_type), self.assertRaises(HTTPError) as error:
                    urlopen(Request(api + route, data=data, method=method, headers={
                        'Origin': 'https://untrusted.example', 'Content-Type': content_type,
                    }))
                self.assertEqual(error.exception.code, 403)
        self.assertEqual(drawing.read_bytes(), DXF)
        self.assertEqual([p.name for p in self.storage.root.iterdir()], ['project'])
        self.assertEqual([p.name for p in drawing.parent.iterdir()], ['source.dxf'])

    def test_http_same_origin_and_json_content_type(self):
        api = self.start_http()
        origin = api.removesuffix('/cad/api')
        # Folder creation has no JSON body in the browser UI.
        with urlopen(Request(api + '/folder?path=created', data=b'', method='POST',
                             headers={'Origin': origin})) as response:
            self.assertEqual(json.load(response), {'created': 'created'})
        for route, payload in [
            ('/rename', {'from': 'created', 'to': 'renamed'}),
            ('/delete', {'path': 'created'}),
            ('/restore', {'trashed': '.trash/bucket/created', 'to': 'created'}),
        ]:
            with self.subTest(route=route), self.assertRaises(HTTPError) as error:
                urlopen(Request(api + route, data=json.dumps(payload).encode(), method='POST',
                                headers={'Origin': origin, 'Content-Type': 'text/plain'}))
            self.assertEqual(error.exception.code, 415)
        with urlopen(Request(api + '/rename', data=b'{"from":"created","to":"renamed"}',
                             method='POST', headers={
                                 'Origin': origin, 'Content-Type': 'application/json; charset=utf-8',
                             })) as response:
            self.assertEqual(json.load(response), {'renamed': 'renamed'})
        self.assertTrue((self.storage.root / 'renamed').is_dir())

    def test_file_rename_delete_restore_and_trash_boundary(self):
        origin = self.start_http()
        original = self.storage.root / 'project/source.dxf'
        original.write_bytes(DXF)

        self.assertEqual(self.post(origin, '/rename', {
            'from': 'project/source.dxf', 'to': 'project/renamed.dxf',
        }), {'renamed': 'project/renamed.dxf'})
        self.assertFalse(original.exists())
        renamed = self.storage.root / 'project/renamed.dxf'
        self.assertEqual(renamed.read_bytes(), DXF)

        (self.storage.root / 'project/taken.dxf').write_bytes(b'keep')
        with self.assertRaises(HTTPError) as error:
            self.post(origin, '/rename', {
                'from': 'project/renamed.dxf', 'to': 'project/taken.dxf',
            })
        self.assertEqual(error.exception.code, 409)
        self.assertEqual((self.storage.root / 'project/taken.dxf').read_bytes(), b'keep')

        deleted = self.post(origin, '/delete', {'path': 'project/renamed.dxf'})
        self.assertEqual(deleted['from'], 'project/renamed.dxf')
        self.assertTrue(deleted['trashed'].startswith('.trash/'))
        trashed_file = self.storage.root / deleted['trashed']
        self.assertEqual(trashed_file.read_bytes(), DXF)
        self.assertEqual([folder['name'] for folder in self.storage.listing()['folders']], ['project'])
        with self.assertRaises(HTTPError) as error:
            urlopen(origin + '/file?path=' + quote(deleted['trashed']))
        self.assertEqual(error.exception.code, 400)
        with self.assertRaises(HTTPError) as error:
            self.post(origin, '/delete', {'path': '../outside.dxf'})
        self.assertEqual(error.exception.code, 400)

        renamed.write_bytes(b'conflict')
        with self.assertRaises(HTTPError) as error:
            self.post(origin, '/restore', {
                'trashed': deleted['trashed'], 'to': deleted['from'],
            })
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(trashed_file.read_bytes(), DXF)
        for route, payload in [
            ('/rename', {'from': '../outside.dxf', 'to': 'project/other.dxf'}),
            ('/rename', {'from': 'project/renamed.dxf', 'to': '../outside.dxf'}),
            ('/restore', {'trashed': '../outside.dxf', 'to': deleted['from']}),
            ('/restore', {'trashed': deleted['trashed'], 'to': '../outside.dxf'}),
            ('/restore', {'trashed': deleted['trashed'], 'to': 'project/elsewhere.dxf'}),
        ]:
            with self.subTest(route=route, payload=payload), self.assertRaises(HTTPError) as error:
                self.post(origin, route, payload)
            self.assertEqual(error.exception.code, 400)
        renamed.unlink()
        self.assertEqual(self.post(origin, '/restore', {
            'trashed': deleted['trashed'], 'to': deleted['from'],
        }), {'restored': 'project/renamed.dxf'})
        self.assertEqual(renamed.read_bytes(), DXF)

    def test_folder_rename_delete_restore_preserves_legacy_dwg(self):
        origin = self.start_http()
        old_folder = self.storage.root / 'project'
        (old_folder / 'plan.dxf').write_bytes(DXF)
        legacy_dwg = old_folder / 'legacy.dwg'
        legacy_dwg.write_bytes(b'legacy DWG')

        self.assertEqual(self.post(origin, '/rename', {
            'from': 'project', 'to': 'renamed',
        }), {'renamed': 'renamed'})
        self.assertFalse(old_folder.exists())
        self.assertEqual((self.storage.root / 'renamed/legacy.dwg').read_bytes(), b'legacy DWG')
        self.assertEqual(self.storage.listing(), {'folders': [{
            'name': 'renamed', 'files': [{'name': 'plan.dxf', 'kind': 'dxf'}],
        }]})
        for route, payload in [
            ('/rename', {'from': 'renamed/legacy.dwg', 'to': 'renamed/new.dwg'}),
            ('/delete', {'path': 'renamed/legacy.dwg'}),
        ]:
            with self.subTest(route=route), self.assertRaises(HTTPError) as error:
                self.post(origin, route, payload)
            self.assertEqual(error.exception.code, 400)
        with self.assertRaises(HTTPError) as error:
            urlopen(origin + '/file?path=' + quote('renamed/legacy.dwg'))
        self.assertEqual(error.exception.code, 400)
        self.assertEqual((self.storage.root / 'renamed/legacy.dwg').read_bytes(), b'legacy DWG')

        deleted = self.post(origin, '/delete', {'path': 'renamed'})
        self.assertEqual(deleted['from'], 'renamed')
        self.assertEqual((self.storage.root / deleted['trashed'] / 'legacy.dwg').read_bytes(), b'legacy DWG')
        self.assertEqual(self.post(origin, '/restore', {
            'trashed': deleted['trashed'], 'to': 'renamed',
        }), {'restored': 'renamed'})
        self.assertEqual((self.storage.root / 'renamed/legacy.dwg').read_bytes(), b'legacy DWG')

        (self.storage.root / 'occupied').mkdir()
        with self.assertRaises(HTTPError) as error:
            self.post(origin, '/rename', {'from': 'renamed', 'to': 'occupied'})
        self.assertEqual(error.exception.code, 409)

    def test_dxf_and_dwg_uploads_choose_first_free_number(self):
        origin = self.start_http()
        project = self.storage.root / 'project'
        for name in ('drawing.dxf', 'drawing (1).dxf', 'drawing (3).dxf'):
            (project / name).write_bytes(name.encode())
        result = self.put_upload(origin, 'project/drawing.dxf', DXF)
        self.assertEqual(result, {'saved': 'project/drawing (2).dxf'})
        self.assertEqual(self.get_file(origin, result['saved']), DXF)

        for name in ('converted.dxf', 'converted (1).dxf', 'converted (3).dxf'):
            (project / name).write_bytes(name.encode())

        def convert(command, **_kwargs):
            Path(command[-1]).write_bytes(b'converted bytes')

        with mock.patch.object(backend.subprocess, 'run', side_effect=convert):
            result = self.put_upload(origin, 'project/converted.dwg', b'dwg upload')
        self.assertEqual(result, {
            'saved': 'project/converted (2).dxf',
        })
        self.assertEqual(self.get_file(origin, result['saved']), b'converted bytes')
        self.assertFalse((project / 'converted.dwg').exists())
        self.assertEqual([path.name for path in project.glob('*.dwg')], [])

    def test_dwg_conversion_failure_cleans_temporary_files_and_preserves_existing_files(self):
        project = self.storage.root / 'project'
        legacy = project / 'broken.dwg'
        legacy.write_bytes(b'legacy DWG must remain')
        existing_dxf = project / 'broken.dxf'
        existing_dxf.write_bytes(b'keep existing DXF')

        def fail_after_partial_output(command, **_kwargs):
            Path(command[-1]).write_bytes(b'partial conversion')
            raise subprocess.CalledProcessError(1, command)

        with mock.patch.object(backend.subprocess, 'run', side_effect=fail_after_partial_output):
            with self.assertRaises(subprocess.CalledProcessError):
                self.storage.save('project/broken.dwg', b'new DWG upload', upload=True)

        self.assertEqual(legacy.read_bytes(), b'legacy DWG must remain')
        self.assertEqual(existing_dxf.read_bytes(), b'keep existing DXF')
        self.assertFalse((project / 'broken (1).dxf').exists())
        self.assertEqual(list(project.glob('.cad-convert-*')), [])

    @unittest.skipUnless(CLI.is_file(), 'cadcraft-cli is required for actual DWG roundtrip')
    def test_real_dwg_upload_and_dxf_open_rename_delete_restore(self):
        fixture = self.base / 'fixture.dxf'
        fixture.write_bytes(DXF)
        dwg = self.base / 'fixture.dwg'
        subprocess.run([str(CLI), 'convert', str(fixture), str(dwg)], check=True, capture_output=True, timeout=60)
        origin = self.start_http()
        with urlopen(Request(origin + '/file?path=' + quote('project/시험.dwg'),
                             data=dwg.read_bytes(), method='PUT')) as response:
            result = json.load(response)
        self.assertEqual(result['saved'], 'project/시험.dxf')
        saved = self.storage.root / result['saved']
        converted_bytes = saved.read_bytes()
        self.assertIn(b'ENTITIES', converted_bytes)
        self.assertTrue(saved.is_file())
        self.assertFalse((self.storage.root / 'project/시험.dwg').exists())
        self.assertFalse((self.storage.root / 'project/시험_변환.dxf').exists())
        self.assertEqual(self.get_file(origin, result['saved']), converted_bytes)

        renamed = self.post(origin, '/rename', {
            'from': result['saved'], 'to': 'project/renamed.dxf',
        })['renamed']
        self.assertEqual(self.get_file(origin, renamed), converted_bytes)
        deleted = self.post(origin, '/delete', {'path': renamed})
        with self.assertRaises(HTTPError) as error:
            urlopen(origin + '/file?path=' + quote(renamed))
        self.assertEqual(error.exception.code, 404)
        self.assertEqual(self.post(origin, '/restore', {
            'trashed': deleted['trashed'], 'to': renamed,
        }), {'restored': renamed})
        self.assertEqual(self.get_file(origin, renamed), converted_bytes)


if __name__ == '__main__':
    unittest.main()
