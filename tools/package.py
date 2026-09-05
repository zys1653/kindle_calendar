"""Build an installable ZIP from an explicit release allowlist."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def release_files(root=ROOT):
    app = root / 'todoclock'
    files = [(app/name, 'todoclock/'+name) for name in ('run.py', 'config.xml', 'menu.json')]
    for folder, pattern in [('app', '*.py'), ('bin', '*.sh')]:
        files.extend((p, 'todoclock/'+p.relative_to(app).as_posix()) for p in sorted((app/folder).glob(pattern)))
    for name in ('default.json', 'local.example.json', 'secrets.example.json'):
        files.append((app/'config'/name, 'todoclock/config/'+name))
    for name in ('README.md', 'regular.ttf', 'OFL.txt'):
        if (app/'assets/fonts'/name).exists():
            files.append((app/'assets/fonts'/name, 'todoclock/assets/fonts/'+name))
    files.append((root/'README.md', 'todoclock/README.md'))
    for name in ('INSTALL.md', 'ARCHITECTURE.md', 'TESTING.md'):
        files.append((root/'docs'/name, 'todoclock/docs/'+name))
    return files


def build():
    out = ROOT/'dist'
    out.mkdir(exist_ok=True)
    target = out/'TodoClock-0.1.3.zip'
    manifest = {}
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for source, name in release_files():
            data = source.read_bytes()
            if source.suffix in ('.py', '.sh', '.md', '.json', '.xml'):
                data = data.replace(b'\r\n', b'\n')
            info = zipfile.ZipInfo(name, (2026, 9, 5, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100755 if source.suffix == '.sh' else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
            manifest[name] = hashlib.sha256(data).hexdigest()
        archive.writestr('todoclock/MANIFEST.json', json.dumps(manifest, indent=2))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(digest+'  '+target.name+'\n', encoding='ascii')
    print('{} ({} bytes)\nSHA256 {}'.format(target, target.stat().st_size, digest))


if __name__ == '__main__':
    build()
