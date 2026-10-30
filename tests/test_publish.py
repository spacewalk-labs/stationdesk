"""Share operations preserve originals and never overwrite an existing publication."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('publish', Path(__file__).parents[1] / 'apps/publish/server.py')
publish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish)


class ShareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'share').mkdir()
        self.share = publish.Share(self.root / 'share')
        self.source = self.root / 'source.txt'
        self.source.write_text('original')

    def test_link_unpublish_preserves_original_and_existing_names(self):
        self.share.publish(self.source, '한글.txt')
        with self.assertRaises(FileExistsError):
            self.share.publish(self.source, '한글.txt')
        self.assertEqual('/%ED%95%9C%EA%B8%80.txt', self.share.list()[0]['url'])
        self.share.unpublish('한글.txt')
        self.assertEqual([], self.share.list())
        self.assertEqual('original', self.source.read_text())

    def test_copies_and_direct_files_are_not_unpublished(self):
        self.share.publish(self.source, 'copy.txt', 'copy')
        with self.assertRaises(ValueError):
            self.share.unpublish('copy.txt')
        self.assertEqual('original', (self.share.root / 'copy.txt').read_text())
        with self.assertRaises(FileExistsError):
            self.share.publish(self.source, 'copy.txt', 'copy')

    def test_traversal_and_recursive_copy_refused(self):
        for name in ('..', '../source.txt', '/tmp/file', '_assets'):
            with self.assertRaises(ValueError):
                self.share.unpublish(name)
        with self.assertRaises(ValueError):
            self.share.publish(self.root, 'recursive', 'copy')
        self.assertFalse((self.share.root / 'recursive').exists())

    def test_broken_link_can_be_unpublished(self):
        self.share.publish(self.source, 'broken')
        self.source.unlink()
        self.assertTrue(self.share.list()[0]['broken'])
        self.share.unpublish('broken')
        self.assertEqual([], self.share.list())
