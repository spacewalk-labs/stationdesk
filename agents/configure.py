#!/usr/bin/env python3
"""Add StationDesk facts to vendor configuration without replacing user settings."""
import json
from pathlib import Path
import re
import sys


BEGIN = b'<!-- stationdesk:begin -->'
END = b'<!-- stationdesk:end -->'
TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|//[^\r\n]*|/\*[\s\S]*?\*/|[^\s{}\[\],:]+|[{}\[\],:]')


def append_array(path, key, value):
    """Insert into a top-level JSON(C) array, retaining original bytes elsewhere."""
    original = path.read_bytes().decode('utf-8') if path.exists() else '{}\n'
    tokens = [m for m in TOKEN.finditer(original) if not m.group().startswith(('//', '/*'))]
    clean = ''.join(m.group() for i, m in enumerate(tokens)
                    if not (m.group() == ',' and tokens[i + 1].group() in (']', '}')))
    config = json.loads(clean)
    if not isinstance(config, dict):
        raise ValueError(f'{path}: expected an object')
    if key in config and not isinstance(config[key], list):
        raise ValueError(f'{path}: {key} must be an array')
    if value in config.get(key, []):
        return
    addition = json.dumps(value, ensure_ascii=False)
    depth = 0
    for i, token in enumerate(tokens):
        text = token.group()
        if depth == 1 and text.startswith('"') and json.loads(text) == key and tokens[i + 1].group() == ':':
            start = i + 2
            nested = 0
            for j in range(start, len(tokens)):
                nested += (tokens[j].group() in ('[', '{')) - (tokens[j].group() in (']', '}'))
                if nested == 0:
                    closing = tokens[j]
                    separator = '' if tokens[j - 1].group() in ('[', ',') else ','
                    addition = separator + addition
                    break
            break
        depth += (text in ('[', '{')) - (text in (']', '}'))
    else:
        closing = tokens[-1]
        separator = '' if tokens[-2].group() in ('{', ',') else ','
        addition = separator + '\n  ' + json.dumps(key) + ': [' + addition + ']\n'
    updated = original[:closing.start()] + addition + original[closing.start():]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(updated.encode('utf-8'))


def link_rule(path, facts):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() and path.resolve() == facts:
        return
    if path.exists() or path.is_symlink():
        raise FileExistsError(f'{path}: existing rule preserved; expected link to {facts}')
    path.symlink_to(facts)


def codex_facts(path, facts):
    # Follow a user's existing symlink instead of replacing the link itself.
    path = path.resolve()
    old = path.read_bytes() if path.exists() else b''
    block = BEGIN + b'\n' + facts.read_bytes().rstrip(b'\n') + b'\n' + END
    start, end = old.find(BEGIN), old.find(END)
    if start >= 0 and end >= start:
        new = old[:start] + block + old[end + len(END):]
    elif start >= 0 or end >= 0:
        raise ValueError(f'{path}: incomplete StationDesk markers; file preserved')
    else:
        new = old + (b'\n' if old and not old.endswith(b'\n') else b'') + block + b'\n'
    if new != old:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(new)


def configure(root, home=None):
    """Wire each independent vendor; return warnings for preserved invalid files."""
    root, home = Path(root).resolve(), Path(home or Path.home()).resolve()
    facts = root / 'state/box-facts.md'
    opencode = home / '.config/opencode'
    config = opencode / 'opencode.jsonc'
    if not config.exists() and (opencode / 'opencode.json').exists():
        config = opencode / 'opencode.json'
    operations = (
        lambda: link_rule(home / '.claude/rules/stationdesk.md', facts),
        lambda: codex_facts(home / '.codex/AGENTS.md', facts),
        lambda: append_array(config, 'instructions', str(facts)),
        lambda: link_rule(home / '.gemini/config/rules/stationdesk.md', facts),
        lambda: append_array(home / '.gemini/config/skills.json', 'entries',
                             {'path': str(home / '.claude/skills'), 'include_only': ['share-docs']}),
    )
    warnings = []
    for operation in operations:
        try:
            operation()
        except (OSError, ValueError) as error:
            warnings.append(str(error))
    return warnings


if __name__ == '__main__':
    warnings = configure(sys.argv[1])
    for warning in warnings:
        print(f'agent configuration: {warning}', file=sys.stderr)
    sys.exit(bool(warnings))
