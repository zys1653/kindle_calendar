"""Kindle-only operations. No shell interpolation and no rootfs changes."""
import ctypes
import os
from pathlib import Path
import re
import signal
import subprocess
import time
from PIL import Image, ImageChops


def command(args, timeout=5):
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("设备命令无法执行: " + Path(args[0]).name) from None
    if result.returncode:
        raise RuntimeError("设备命令失败: " + Path(args[0]).name)
    return result.stdout.decode("utf-8", "replace").strip()


def lipc_get(service, prop):
    return int(command(["/usr/bin/lipc-get-prop", service, prop]))


def lipc_text(service, prop):
    return command(["/usr/bin/lipc-get-prop", service, prop])


def lipc_set(service, prop, value):
    command(["/usr/bin/lipc-set-prop", service, prop, str(int(value))])


def identity(pid):
    try:
        stat = Path("/proc/{}/stat".format(pid)).read_text()
        tail = stat[stat.rfind(")") + 2:].split()
        return {"pid": int(pid), "start": tail[19], "name": Path("/proc/{}/comm".format(pid)).read_text().strip()}
    except (OSError, IndexError, ValueError):
        return None


def same_process(record):
    return bool(record and identity(record["pid"]) == record)


def processes(name):
    return [record for p in Path("/proc").glob("[0-9]*")
            for record in [identity(int(p.name))] if record and record["name"] == name]


def dimensions(rotation):
    return (1448, 1072) if rotation in (90, 270) else (1072, 1448)


def native_to_logical(x, y, rotation):
    if rotation == 90:
        return 1447 - y, x
    if rotation == 180:
        return 1071 - x, 1447 - y
    if rotation == 270:
        return y, 1071 - x
    return x, y


def logical_to_native(x, y, rotation):
    if rotation == 90:
        return y, 1447 - x
    if rotation == 180:
        return 1071 - x, 1447 - y
    if rotation == 270:
        return 1071 - y, x
    return x, y


def framebuffer():
    import fcntl
    import struct
    class Fixed(ctypes.Structure):
        _fields_ = [("id", ctypes.c_char * 16), ("smem_start", ctypes.c_ulong),
                    ("smem_len", ctypes.c_uint), ("type", ctypes.c_uint),
                    ("type_aux", ctypes.c_uint), ("visual", ctypes.c_uint),
                    ("xpan", ctypes.c_ushort), ("ypan", ctypes.c_ushort),
                    ("ywrap", ctypes.c_ushort), ("line_length", ctypes.c_uint),
                    ("mmio_start", ctypes.c_ulong), ("mmio_len", ctypes.c_uint),
                    ("accel", ctypes.c_uint), ("capabilities", ctypes.c_ushort),
                    ("reserved", ctypes.c_ushort * 2)]
    with open("/dev/fb0", "rb", buffering=0) as fb:
        var = struct.unpack("=40I", fcntl.ioctl(fb, 0x4600, bytes(160)))
        fixed = Fixed.from_buffer_copy(fcntl.ioctl(fb, 0x4602, bytes(ctypes.sizeof(Fixed))))
    return {"width": var[0], "height": var[1], "bpp": var[6], "rotation": var[34],
            "xoffset": var[4], "yoffset": var[5], "stride": fixed.line_length}


def battery_status(sys_root=Path('/sys'), allow_lipc=True):
    from .power import sample
    result = sample(sys_root)
    if result['battery'] is None and allow_lipc:
        try:
            result['battery'] = lipc_get('com.lab126.powerd', 'battLevel')
            result['charging'] = bool(lipc_get('com.lab126.powerd', 'isCharging'))
        except (RuntimeError, ValueError):
            pass
    return result


class Kindle:
    def __init__(self, config, runtime):
        self.config, self.runtime = config, Path(runtime)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.previous = None
        self.last_full = 0
        from .power import CoverTracker
        self.cover_tracker = CoverTracker()

    def probe(self):
        info = framebuffer()
        if (info["width"], info["height"], info["bpp"], info["rotation"], info["xoffset"], info["yoffset"]) != (1072, 1448, 8, 0, 0, 0):
            raise RuntimeError("请先将原生屏幕置于竖屏 rotation 0 后诊断")
        if info["stride"] < 1072 or info["stride"] > 8192:
            raise RuntimeError("不支持的 framebuffer stride")
        # --help exits successfully on normal FBInk builds; inspect actual installed build.
        help_text = command([self.config["fbink"], "--help"])
        for option in ("--image", "--waveform", "--flash", "--refresh", "--wait"):
            if option not in help_text:
                raise RuntimeError("FBInk 缺少能力: " + option)
        return info

    def snapshot(self):
        info = self.probe()
        with open("/dev/fb0", "rb", buffering=0) as fb:
            data = fb.read(info["stride"] * 1448)
        image = Image.frombytes("L", (1072, 1448), data, "raw", "L", info["stride"], 1)
        image.save(self.runtime / "original.png")

    def show(self, image, force=False):
        # Software rotation leaves kernel orientation untouched.
        image = image.rotate(self.config["rotation"], expand=True).convert("L")
        now = time.monotonic()
        full = force or self.previous is None or now - self.last_full >= self.config["full_refresh_minutes"] * 60
        box = (0, 0, 1072, 1448) if full else ImageChops.difference(image, self.previous).getbbox()
        if box is None:
            return
        path = self.runtime / "frame.png"
        image.crop(box).save(path)
        args = [self.config["fbink"], "-q", "-w", "-g", "file={},x={},y={}".format(path, box[0], box[1]), "-W", "GC16"]
        if full:
            args.append("-f")
        command(args, 10)
        self.previous = image
        if full:
            self.last_full = now

    def power_status(self):
        return self.cover_tracker.update(battery_status(allow_lipc=False))

    def status(self):
        result = battery_status()
        try:
            result["wifi_on"] = bool(lipc_get("com.lab126.wifid", "enable"))
            result["wifi"] = ('已连接' if lipc_text('com.lab126.wifid', 'cmState') == 'CONNECTED' else '未连接') if result['wifi_on'] else '已关闭'
        except (RuntimeError, ValueError):
            result.update(wifi="未知", wifi_on=None)
        try:
            result["light"] = lipc_get("com.lab126.powerd", "flIntensity")
        except (RuntimeError, ValueError):
            result["light"] = None
        return result

    def set_wifi(self, enabled):
        if enabled:
            lipc_set('com.lab126.cmd', 'wirelessEnable', 1)
            lipc_set('com.lab126.wifid', 'enable', 1)
        else:
            lipc_set('com.lab126.wifid', 'enable', 0)
            lipc_set('com.lab126.cmd', 'wirelessEnable', 0)

    def set_light(self, value):
        lipc_set("com.lab126.powerd", "flIntensity", max(0, min(self.config["device"]["frontlight_max"], value)))


class SimDevice:
    def __init__(self):
        self.data = dict(battery=82, charging=True, cover=64, cover_present=True,
                         cover_charging=False, wifi="已连接", wifi_on=True, light=0)

    def power_status(self):
        return {k: v for k, v in self.data.items() if k not in ('wifi', 'wifi_on', 'light')}

    def status(self):
        return dict(self.data)

    def set_wifi(self, value):
        self.data.update(wifi_on=bool(value), wifi="已连接" if value else "已关闭")

    def set_light(self, value):
        self.data["light"] = min(24, max(0, value))
