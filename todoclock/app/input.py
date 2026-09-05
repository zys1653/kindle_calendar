"""Linux event reader; supports type A/B multitouch and single touch frames."""
import os
from pathlib import Path
import re
import select
import struct
import time
from .device import native_to_logical

EVENT = struct.Struct("@llHHi")


def key_direction(code):
    # Original top key is on the right in the requested buttons-down landscape.
    return 1 if code in (104, 191, 193) else -1


def discover():
    result = {}
    for block in Path("/proc/bus/input/devices").read_text().split("\n\n"):
        name = re.search(r'N: Name="([^"]+)"', block)
        event = re.search(r"\bevent\d+\b", block)
        if not name or not event:
            continue
        name = name.group(1)
        kind = "touch" if re.search(r"cyttsp.*_mt", name) else "keys" if name in ("gpiokey", "gpio-keys") else "power" if "onkey" in name else None
        if kind:
            result[kind] = {"path": "/dev/input/" + event.group(), "name": name}
    return result


class TouchFrame:
    def __init__(self):
        self.x = self.y = None
        self.down = False
        self.released = False
        self.slot = 0
        self.start = None
        self.dropped = False

    def feed(self, typ, code, value):
        if typ == 0 and code == 3:  # SYN_DROPPED: discard until next frame
            self.dropped = True
            self.down, self.start = False, None
        if self.dropped:
            if typ == 0 and code == 0:
                self.dropped = False
            return None
        if typ == 3:
            if code == 47:
                self.slot = value
            if self.slot != 0:
                return None
            if code in (0, 53):
                self.x = value
            elif code in (1, 54):
                self.y = value
            elif code == 57:
                self.released = value < 0
                self.down = value >= 0
        if typ == 1 and code == 330:
            self.down = value != 0
            self.released = value == 0
        if typ == 0 and code == 0:
            if self.down and self.start is None and self.x is not None and self.y is not None:
                self.start = (self.x, self.y, time.monotonic())
            if self.released:
                self.released = False
                start, self.start = self.start, None
                if start and self.x is not None and self.y is not None and time.monotonic() - start[2] < 2 and abs(self.x - start[0]) + abs(self.y - start[1]) < 150:
                    return self.x, self.y
        return None


def apply_calibration(x, y, calibration):
    a, b, c, d, e, f = calibration["matrix"]
    return a*x + b*y + c, d*x + e*y + f


class Inputs:
    def __init__(self, calibration=None, grab=False):
        import fcntl
        self.devices = discover()
        self.fds, self.buffers = {}, {}
        self.frame, self.calibration = TouchFrame(), calibration
        self.held = {}
        try:
            for kind, entry in self.devices.items():
                fd = os.open(entry["path"], os.O_RDONLY | os.O_NONBLOCK)
                self.fds[fd] = kind
                self.buffers[fd] = b""
                if grab and kind != "power":
                    fcntl.ioctl(fd, 0x40044590, 1)  # EVIOCGRAB; close also releases.
        except Exception:
            self.close()
            raise

    def poll(self, rotation, raw=False):
        events = []
        ready, _, _ = select.select(list(self.fds), [], [], 0)
        for fd in ready:
            data = os.read(fd, EVENT.size * 64)
            if not data:
                raise RuntimeError("输入设备已断开")
            self.buffers[fd] += data
            while len(self.buffers[fd]) >= EVENT.size:
                _, _, typ, code, value = EVENT.unpack(self.buffers[fd][:EVENT.size])
                self.buffers[fd] = self.buffers[fd][EVENT.size:]
                kind = self.fds[fd]
                if kind == "touch":
                    started = self.frame.start
                    point = self.frame.feed(typ, code, value)
                    if not raw and started is None and self.frame.start is not None:
                        press = native_to_logical(*apply_calibration(*self.frame.start[:2], self.calibration), rotation)
                        events.append(('press', press))
                    if point:
                        if not raw:
                            point = native_to_logical(*apply_calibration(*point, self.calibration), rotation)
                        events.append(("tap", point))
                    elif not raw and started is not None and self.frame.start is None:
                        events.append(('cancel', None))
                elif typ == 1 and kind == "power" and value == 1:
                    events.append(("exit", None))
                elif typ == 1 and kind == "keys":
                    if value == 1:
                        self.held[code] = time.monotonic()
                        if code in (104, 109, 191, 192, 193, 194):
                            events.append(("key", key_direction(code)))
                    elif value == 0:
                        self.held.pop(code, None)
        if len(self.held) >= 2 and time.monotonic() - max(self.held.values()) >= 3:
            events.append(("exit", None))
        return events

    def close(self):
        for fd in self.fds:
            os.close(fd)
        self.fds.clear()
