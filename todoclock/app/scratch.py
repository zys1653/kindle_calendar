"""Private simulation workspaces with inherited desktop ACLs (Python 3.12 Windows)."""
from contextlib import contextmanager
from pathlib import Path
import shutil
import uuid


@contextmanager
def scratch(parent):
    parent = Path(parent).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / ('run-' + uuid.uuid4().hex)
    path.mkdir()
    try:
        yield path
    finally:
        resolved = path.resolve()
        if resolved.parent != parent or not resolved.name.startswith('run-'):
            raise RuntimeError('拒绝清理工作目录以外的路径')
        shutil.rmtree(str(resolved))
