"""Standalone supervisor, copied to /tmp before device takeover; stdlib only."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def identity(pid):
    try:
        text = Path('/proc/{}/stat'.format(pid)).read_text()
        return {'pid': pid, 'start': text[text.rfind(')') + 2:].split()[19],
                'name': Path('/proc/{}/comm'.format(pid)).read_text().strip()}
    except (OSError, IndexError):
        return None


def same(record):
    return bool(record and identity(record['pid']) == record)


def read(path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def write(path, value):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as out:
        json.dump(value, out)
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, path)


def run(args):
    try:
        return subprocess.run(args, timeout=8, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def restore(runtime):
    journal = read(runtime / 'journal.json', {})
    if not journal or journal.get('restored'):
        return True
    ok = True
    for process in journal.get('processes', []):
        if same(process):
            try:
                os.kill(process['pid'], signal.SIGCONT)
            except OSError:
                ok = False
    for prop in journal.get('properties', []):
        ok = run(['/usr/bin/lipc-set-prop', prop['service'], prop['name'], str(prop['value'])]) and ok
    if (runtime / 'original.png').exists():
        ok = run([journal['fbink'], '-q', '-g', 'file=' + str(runtime / 'original.png'), '-W', 'GC16', '-f']) and ok
    if ok:
        journal['restored'] = True
        write(runtime / 'journal.json', journal)
    return ok


def supervise(root, runtime, mode):
    import fcntl
    runtime.mkdir(exist_ok=True, parents=True)
    with (runtime / 'supervisor.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        if not restore(runtime):
            return 2
        for name in ('stop', 'heartbeat', 'journal.json'):
            try:
                (runtime / name).unlink()
            except FileNotFoundError:
                pass
        stopped = [False]
        def stop(*_):
            stopped[0] = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        child = subprocess.Popen([sys.executable, str(root / 'run.py'), mode, '--runtime', str(runtime)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        record = identity(child.pid)
        write(runtime / 'owner.json', {'supervisor': identity(os.getpid()), 'child': record})
        start = time.monotonic()
        try:
            while child.poll() is None:
                if (runtime / 'stop').exists() or stopped[0]:
                    break
                try:
                    age = time.time() - (runtime / 'heartbeat').stat().st_mtime
                except FileNotFoundError:
                    age = time.monotonic() - start
                if age > 45:
                    break
                time.sleep(0.5)
        finally:
            success = False
            try:
                if child.poll() is None and same(record):
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        if same(record):
                            child.kill()
                        child.wait(timeout=5)
            finally:
                # Restoration must still run if termination/wait itself fails.
                success = restore(runtime)
                write(runtime / 'result.json', {'restored': success, 'exit_code': child.returncode})
        return 0 if success else 2


if __name__ == '__main__':
    if sys.argv[1] == 'restore':
        sys.exit(0 if restore(Path(sys.argv[2])) else 2)
    sys.exit(supervise(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]))
