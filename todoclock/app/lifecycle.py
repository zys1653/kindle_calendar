import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from .device import Kindle, identity, lipc_get, lipc_set, processes
from .input import discover
from .storage import Store

RUNTIME = Path('/tmp/todoclock-runtime')


def require_supervisor(runtime):
    """Never permit direct internal CLI modes to take over without a live owner."""
    for _ in range(30):
        owner = Store(runtime).read('owner.json', {})
        supervisor, child = owner.get('supervisor'), owner.get('child')
        if (supervisor and child and child['pid'] == os.getpid()
                and identity(child['pid']) == child and identity(supervisor['pid']) == supervisor
                and supervisor['pid'] == os.getppid()):
            return
        time.sleep(0.1)
    raise RuntimeError('没有匹配的独立看护进程，请使用 KUAL Start / Touch calibration')


def launch(root, mode='kindle'):
    if sys.platform != 'linux' or not Path('/dev/fb0').exists():
        raise RuntimeError('此命令仅适用于 Kindle；电脑请使用 simulate')
    RUNTIME.mkdir(parents=True, exist_ok=True)
    # Do not overwrite a running supervisor. The supervisor also owns an exclusive lock.
    owners = Store(RUNTIME).read('owner.json', {})
    owner = owners.get('supervisor')
    if owner and identity(owner['pid']) == owner:
        return
    orphan = owners.get('child')
    if orphan and identity(orphan['pid']) == orphan:
        stop()
    shutil.copyfile(Path(__file__).with_name('guardian.py'), RUNTIME / 'guardian.py')
    subprocess.Popen([sys.executable, str(RUNTIME / 'guardian.py'), str(root), str(RUNTIME), mode],
                     start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop():
    if not RUNTIME.exists():
        return
    (RUNTIME / 'stop').touch()
    owner = Store(RUNTIME).read('owner.json', {}).get('supervisor')
    if owner and identity(owner['pid']) == owner:
        for _ in range(30):
            if identity(owner['pid']) != owner:
                return
            time.sleep(0.5)
        raise RuntimeError('看护进程仍在恢复，请检查 /tmp/todoclock-runtime/result.json')
    # An orphaned application must stop before replaying a recovery journal.
    child = Store(RUNTIME).read('owner.json', {}).get('child')
    if child and identity(child['pid']) == child:
        os.kill(child['pid'], signal.SIGTERM)
        time.sleep(2)
        if identity(child['pid']) == child:
            os.kill(child['pid'], signal.SIGKILL)
            time.sleep(0.5)
    script = RUNTIME / 'guardian.py'
    if script.exists():
        subprocess.run([sys.executable, str(script), 'restore', str(RUNTIME)], check=True, timeout=45)


def enter(root, config, runtime, calibration=False):
    require_supervisor(runtime)
    device = Kindle(config, runtime)
    device.probe()
    found = discover()
    if not all(k in found for k in ('touch', 'keys', 'power')):
        raise RuntimeError('未找到触摸、翻页键或电源键，禁止接管')
    if not calibration:
        if not config['device'].get('verified'):
            raise RuntimeError('请完成诊断/校准并在 local.json 设置 device.verified=true')
        saved = Store(Path(root) / 'state').read('calibration.json', {})
        if saved.get('device') != found['touch']['name'] or len(saved.get('matrix', [])) != 6:
            raise RuntimeError('缺少匹配的触摸校准')
    properties = []
    for service, name in [('com.lab126.powerd', 'preventScreenSaver'),
                          ('com.lab126.powerd', 'flIntensity'),
                          ('com.lab126.cmd', 'wirelessEnable'),
                          ('com.lab126.wifid', 'enable')]:
        properties.append({'service': service, 'name': name, 'value': lipc_get(service, name)})
    paused = []
    for name in config['device'].get('suppress_processes', []):
        if name not in ('awesome',):
            raise RuntimeError('不允许暂停此系统进程')
        matches = processes(name)
        if not matches:
            raise RuntimeError('未找到可验证的原生窗口管理器')
        for record in matches:
            stat = Path('/proc/{}/stat'.format(record['pid'])).read_text()
            if stat[stat.rfind(')') + 2:].split()[0] in ('T', 't'):
                raise RuntimeError('原生界面已被其他应用暂停，请先退出其他插件')
        paused.extend(matches)
    device.snapshot()
    Store(runtime).write('journal.json', {'properties': properties, 'processes': paused,
                                          'fbink': config['fbink'], 'restored': False})
    # All operations below have durable undo information, even if interrupted between calls.
    lipc_set('com.lab126.powerd', 'preventScreenSaver', 1)
    device.set_light(0)
    for record in paused:
        if identity(record['pid']) != record:
            raise RuntimeError('原生进程发生变化，取消接管')
        os.kill(record['pid'], signal.SIGSTOP)
    return device
