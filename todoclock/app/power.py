"""Read-only power sampling and KOA1 cover interpretation."""
import errno
from pathlib import Path

STATES = ('Charging', 'Discharging', 'Not charging', 'Full')


def read_field(path, name):
    try:
        value = (path / name).read_text().strip()
    except OSError as exc:
        kind = {errno.ENOENT: 'missing', errno.ENODEV: 'no_data', errno.ENODATA: 'no_data',
                errno.EACCES: 'permission', errno.EPERM: 'permission'}.get(exc.errno, 'io_error')
        return None, kind
    allowed = STATES + ('Unknown', 'Battery', 'USB', 'Mains', 'USB_DCP', 'USB_CDP', 'USB_C', 'Wireless')
    if value.isdigit() and len(value) <= 5 or value in allowed:
        return value, 'ok'
    return None, 'empty' if not value else 'unrecognized'


def sources(sys_root):
    paths = list((Path(sys_root) / 'class/power_supply').glob('*'))
    paths.append(Path(sys_root) / 'devices/platform/soda/power_supply/soda_fg')
    unique = {}
    for path in paths:
        if path.exists():
            unique.setdefault(path.resolve(), path)
    return list(unique.values())


def capacity(path, name='capacity'):
    value, _ = read_field(path, name)
    return int(value) if value is not None and value.isdigit() and 0 <= int(value) <= 100 else None


def sample(sys_root=Path('/sys')):
    sys_root = Path(sys_root)
    result = dict(battery=None, charging=False, cover=None, cover_present=None,
                  cover_charging=False, external_power=False, cover_evidence='unknown')
    covers = []
    for path in sources(sys_root):
        get = lambda name: read_field(path, name)[0]
        kind = (get('type') or '').lower()
        if kind == 'battery' or path.name == 'soda_fg':
            if any(word in path.name.lower() for word in ('cover', 'aux', 'soda')):
                covers.append(path)
            elif get('present') != '0':
                result.update(battery=capacity(path), charging=get('status') == 'Charging')
        # KOA input rails only. Neither boost nor battery.online means USB power.
        elif 'boost' not in path.name.lower() and (kind in ('usb', 'mains', 'usb_dcp', 'usb_cdp', 'usb_c')
                or path.name in ('max77696-charger', 'max77696-uic')):
            present = get('present')
            result['external_power'] |= present == '1' or (present is None and get('online') == '1')
    readings = [(p, read_field(p, 'present'), read_field(p, 'status'), read_field(p, 'capacity')) for p in covers]
    if any(present[0] == '0' for _, present, _, _ in readings) or not covers:
        result.update(cover_present=False, cover_evidence='explicit_absent' if covers else 'no_device')
    else:
        valid = next(((status[0], int(cap[0])) for p, present, status, cap in readings
                      if cap[0] is not None and cap[0].isdigit() and 0 <= int(cap[0]) <= 100
                      and status[0] in STATES), None)
        if valid is not None:
            result.update(cover_present=True, cover=valid[1],
                          cover_charging=valid[0] == 'Charging',
                          cover_evidence='live_gauge')
        elif any(present[0] == '1' for _, present, _, _ in readings):
            result.update(cover_present=True, cover_evidence='present_without_data')
        elif readings and all(status[1] in ('missing', 'no_data', 'empty') and
                             cap[1] in ('missing', 'no_data', 'empty') for _, _, status, cap in readings):
            result['cover_evidence'] = 'no_data'
        else:
            result['cover_evidence'] = 'read_error_or_partial'
    if result['battery'] is None:
        result['battery'] = capacity(sys_root / 'devices/system/wario_battery/wario_battery0', 'battery_capacity')
    charging, _ = read_field(sys_root / 'devices/system/wario_charger/wario_charger0', 'charging')
    if charging in ('0', '1'):
        result['charging'] = charging == '1'
    return result


class CoverTracker:
    def __init__(self):
        self.missing = 0

    def update(self, result):
        result = dict(result)
        self.missing = self.missing + 1 if result.get('cover_evidence') == 'no_data' else 0
        if self.missing >= 2:
            result.update(cover_present=False, cover=None, cover_charging=False)
        return result


def diagnostics(sys_root=Path('/sys')):
    report = {}
    paths = sources(sys_root) + [Path(sys_root) / 'devices/system/wario_charger/wario_charger0']
    for path in paths:
        report[str(path)] = {field: {'value': value, 'read': kind}
                             for field in ('type', 'present', 'online', 'status', 'capacity', 'charging')
                             for value, kind in [read_field(path, field)]}
    return report
