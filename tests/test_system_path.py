import importlib.machinery
import importlib.util
import unittest
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / 'bin/stationdesk'
loader = importlib.machinery.SourceFileLoader('stationdesk_engine_path', str(ENGINE))
spec = importlib.util.spec_from_loader(loader.name, loader)
engine = importlib.util.module_from_spec(spec)
loader.exec_module(engine)


class SystemPath(unittest.TestCase):
    def test_adds_missing_sbin_once_in_order(self):
        self.assertEqual(engine.system_path('/home/u/.local/bin:/usr/bin:/bin'),
                         '/home/u/.local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin')
        self.assertEqual(engine.system_path('/usr/sbin:/usr/bin'), '/usr/sbin:/usr/bin:/usr/local/sbin:/sbin')


if __name__ == '__main__':
    unittest.main()
