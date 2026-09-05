import platform
from pathlib import Path
import sys
from .device import Kindle, battery_status, lipc_get, lipc_text
from .input import discover


def diagnose(root, config, simulated=False):
    import PIL
    import requests
    report = {'python': sys.version.split()[0], 'pillow': PIL.__version__, 'requests': requests.__version__,
              'machine': platform.machine(), 'simulated': simulated,
              'hardware_verified': False, 'font_present': (Path(root) / config['font']).is_file()}
    if simulated:
        return report
    try:
        report['framebuffer'] = Kindle(config, '/tmp/todoclock-diagnostics').probe()
    except Exception as exc:
        report['display_error'] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
    try:
        report['inputs'] = discover()
    except OSError:
        report['inputs'] = {}
    report['battery'] = battery_status()
    from .power import diagnostics
    report['power_sources'] = diagnostics()
    report['properties'] = {}
    for service, name in [('com.lab126.powerd', 'preventScreenSaver'), ('com.lab126.powerd', 'flIntensity'),
                          ('com.lab126.cmd', 'wirelessEnable'), ('com.lab126.wifid', 'enable'), ('com.lab126.wifid', 'cmState')]:
        try:
            report['properties'][service + '/' + name] = lipc_text(service, name) if name == 'cmState' else lipc_get(service, name)
        except Exception:
            report['properties'][service + '/' + name] = 'unavailable'
    # No serial number, network name, credentials, account data or task content.
    return report
