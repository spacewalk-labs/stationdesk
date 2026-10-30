"""User configuration preservation during StationDesk projection."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('agent_configure', Path(__file__).parents[1] / 'agents/configure.py')
wiring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wiring)


class AgentConfigurationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root, self.home = self.base / 'product', self.base / 'home'
        self.facts = self.root / 'state/box-facts.md'
        self.facts.parent.mkdir(parents=True)
        self.facts.write_text('Remote box\nhttps://box.example.test/\n')

    def write(self, name, content):
        path = self.home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_projection_preserves_claude_and_linked_codex_bytes(self):
        claude = self.write('.claude/CLAUDE.md', b'User instructions\r\nunchanged\xff')
        target = self.write('personal/codex.md', b'prefix\r\n<!-- stationdesk:begin -->old<!-- stationdesk:end -->\r\nsuffix\xff')
        link = self.home / '.codex/AGENTS.md'
        link.parent.mkdir()
        link.symlink_to(target)
        self.assertEqual(wiring.configure(self.root, self.home), [])
        self.assertTrue(link.is_symlink())
        self.assertEqual(claude.read_bytes(), b'User instructions\r\nunchanged\xff')
        self.assertEqual(target.read_bytes(), b'prefix\r\n' + wiring.BEGIN + b'\n' + self.facts.read_bytes() + wiring.END + b'\r\nsuffix\xff')
        for rule in ('.claude/rules/stationdesk.md', '.gemini/config/rules/stationdesk.md'):
            self.assertEqual((self.home / rule).resolve(), self.facts)
        self.assertFalse((self.home / '.config/opencode/AGENTS.md').exists())
        before = {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        self.assertEqual(wiring.configure(self.root, self.home), [])
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.facts.write_text('Changed facts\n')
        self.assertEqual(wiring.configure(self.root, self.home), [])
        self.assertIn(b'Changed facts', target.read_bytes())

    def test_jsonc_preserves_comments_provider_model_and_nested_instructions(self):
        original = '''{
  // do not rewrite https://vendor.test/a
  "provider": {"custom": {"instructions": ["nested"], "url":"https://api.test/*x*/"}},
  "model": "custom/model",
  "instructions": ["personal.md", /* keep this */],
}
'''
        path = self.write('.config/opencode/opencode.jsonc', original.encode())
        self.assertEqual(wiring.configure(self.root, self.home), [])
        self.assertEqual(path.read_text(), original.replace('/* keep this */]', '/* keep this */' + json.dumps(str(self.facts)) + ']'))
        self.assertFalse(path.with_suffix('.json').exists())

    def test_json_and_existing_agy_entries_are_extended(self):
        path = self.write('.config/opencode/opencode.json', b'{"model":"mine", "instructions": ["mine.md"]}')
        skills = self.write('.gemini/config/skills.json', b'{"other": 9, "entries": [{"path":"/personal", "include_only":["mine"]}]}')
        self.assertEqual(wiring.configure(self.root, self.home), [])
        self.assertEqual(json.loads(path.read_text()), {'model': 'mine', 'instructions': ['mine.md', str(self.facts)]})
        self.assertFalse(path.with_suffix('.jsonc').exists())
        data = json.loads(skills.read_text())
        self.assertEqual(data['other'], 9)
        self.assertEqual(data['entries'][0], {'path': '/personal', 'include_only': ['mine']})
        self.assertEqual(data['entries'][1], {'path': str(self.home / '.claude/skills'), 'include_only': ['share-docs']})

    def test_conflicts_and_invalid_config_preserved_while_other_vendors_continue(self):
        rule = self.write('.claude/rules/stationdesk.md', b'my rule')
        config = self.write('.config/opencode/opencode.jsonc', b'{ broken file')
        codex = self.write('.codex/AGENTS.md', b'personal\n<!-- stationdesk:begin -->unfinished')
        before = {p: p.read_bytes() for p in (rule, config, codex)}
        self.assertEqual(len(wiring.configure(self.root, self.home)), 3)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertTrue((self.home / '.gemini/config/rules/stationdesk.md').is_symlink())


if __name__ == '__main__':
    unittest.main()
