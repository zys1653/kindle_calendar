"""Portable static release checks; no device or network access."""
import ast
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
errors = []
for path in (ROOT/'todoclock').rglob('*.py'):
    try:
        ast.parse(path.read_text(encoding='utf-8'), filename=str(path), feature_version=(3, 9))
    except SyntaxError as exc:
        errors.append('{}: {}'.format(path, exc.msg))
for path in (ROOT/'todoclock/bin').glob('*.sh'):
    if b'\r' in path.read_bytes():
        errors.append(str(path)+' must use LF')
for path in (ROOT/'todoclock/config').glob('*.json'):
    json.loads(path.read_text(encoding='utf-8'))
ET.parse(ROOT/'todoclock/config.xml')
menu = json.loads((ROOT/'todoclock/menu.json').read_text(encoding='utf-8'))
for item in menu['items'][0]['items']:
    if not (ROOT/'todoclock'/item['action'].removeprefix('sh ')).is_file():
        errors.append('Missing KUAL command')
if errors:
    raise SystemExit('\n'.join(errors))
print('PASS: Python 3.9 syntax, LF shell files, JSON/XML, KUAL command targets')
