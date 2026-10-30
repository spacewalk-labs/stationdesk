"""Public snapshots read the same built-in document assets nginx serves."""
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch


APP = Path(__file__).parents[1] / 'apps/publish'
spec = importlib.util.spec_from_file_location('publish_assets', APP / 'server.py')
publish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish)


class SnapshotAssetTests(unittest.TestCase):
    def test_default_template_inlines_builtin_assets_before_share_alias(self):
        with tempfile.TemporaryDirectory() as temp:
            share = Path(temp)
            (share / '_assets').mkdir()
            (share / '_assets/doc.css').write_text('SHADOW_CSS')
            (share / '_assets/doc.js').write_text('SHADOW_JS')
            (share / 'base.html').write_text((APP / 'share-docs/templates/base.html').read_text())
            with patch.object(publish, 'ROOT', str(share)):
                _, snapshot, warnings = publish.bundle_single_file('base.html')
            html = snapshot.decode()
            self.assertEqual([], warnings)
            self.assertIn((APP / 'share-docs/assets/doc.css').read_text().replace('</style', '<\\/style'), html)
            self.assertIn((APP / 'share-docs/assets/doc.js').read_text().replace('</script', '<\\/script'), html)
            self.assertNotIn('SHADOW_CSS', html)
            self.assertNotIn('SHADOW_JS', html)
            self.assertNotRegex(html, r'(?:href|src)="/_assets/')

    def test_nested_builtin_paths_and_relative_references_stay_inside_alias(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / 'app/share-docs/assets'
            (assets / 'nested').mkdir(parents=True)
            (assets / 'nested/theme.css').write_text('body { color: red }')
            (assets / 'image.svg').write_text('<svg/>')
            share = root / 'share'
            share.mkdir()
            with patch.object(publish, '__file__', str(root / 'app/server.py')), patch.object(publish, 'ROOT', str(share)):
                data, err = publish._read_share_file('/_assets/nested/theme.css?v=1#theme', str(share))
                self.assertEqual((b'body { color: red }', None), (data, err))
                data, err = publish._read_share_file('../image.svg', str(assets / 'nested'))
                self.assertEqual((b'<svg/>', None), (data, err))
                for ref in ('/_assets/../../../outside', '../../outside'):
                    data, err = publish._read_share_file(ref, str(assets / 'nested'))
                    self.assertIsNone(data)
                    self.assertEqual('outside share dir', err)

    def test_share_own_assets_answer_when_builtin_has_no_such_file(self):
        # Documents made before StationDesk link /_assets/legacy-doc.css kept in the share itself.
        with tempfile.TemporaryDirectory() as temp:
            share = Path(temp)
            (share / '_assets').mkdir()
            (share / '_assets/legacy-doc.css').write_text('OLD_DOC_CSS')
            (share / '_assets/doc.css').write_text('SHADOW_CSS')
            with patch.object(publish, 'ROOT', str(share)):
                self.assertEqual((b'OLD_DOC_CSS', None), publish._read_share_file('/_assets/legacy-doc.css', str(share)))
                builtin = (APP / 'share-docs/assets/doc.css').read_bytes()
                self.assertEqual((builtin, None), publish._read_share_file('/_assets/doc.css', str(share)))
                self.assertEqual('outside share dir', publish._read_share_file('/_assets/../../outside', str(share))[1])

    def test_shipped_template_css_has_no_unhandled_import_or_url_dependencies(self):
        # The current template assets are self-contained. If they gain nested
        # imports/images, the snapshot builder must inline those before release.
        for stylesheet in (APP / 'share-docs/assets').glob('*.css'):
            css = re.sub(r'/\*.*?\*/', '', stylesheet.read_text(), flags=re.S)
            self.assertNotRegex(css, r'(?i)@import\b|url\s*\(', stylesheet.name)


if __name__ == '__main__':
    unittest.main()
