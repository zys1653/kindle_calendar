"""Atomic JSON stores. File permissions are not a security boundary on FAT."""
import copy
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import tempfile
import threading


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def read(self, name, default=None):
        with self.lock:
            path = self.root / name
            if not path.exists():
                return copy.deepcopy(default)
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                # Never silently discard a damaged operation queue or credentials.
                raise RuntimeError("无法读取状态文件: " + name) from None

    def write(self, name, value):
        with self.lock:
            fd, tmp = tempfile.mkstemp(prefix=".write-", dir=str(self.root))
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
                    json.dump(value, out, ensure_ascii=False, indent=2)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(tmp, self.root / name)
                if os.name == 'posix':
                    directory = os.open(str(self.root), os.O_RDONLY)
                    try:
                        try:
                            os.fsync(directory)
                        except OSError:
                            # Some Kindle FAT/FUSE mounts do not support directory fsync.
                            pass
                    finally:
                        os.close(directory)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

    def update(self, name, default, change):
        with self.lock:
            data = self.read(name, default)
            change(data)
            self.write(name, data)
            return data


def merge(base, extra):
    result = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def configuration(root):
    files = Store(Path(root) / "config")
    config = merge(files.read("default.json", {}), files.read("local.json", {}))
    config = merge(config, Store(Path(root) / "state").read("preferences.json", {}))
    if config["rotation"] not in (0, 90, 180, 270):
        raise ValueError("rotation 必须是 0/90/180/270")
    for key in ("todo_minutes", "weather_minutes"):
        if config[key] not in (0, 15, 30, 60, 120):
            raise ValueError("同步周期无效")
    if config["mail_minutes"] not in (0, 5, 15, 30, 60, 120):
        raise ValueError("邮箱同步周期无效")
    if config["full_refresh_minutes"] not in (5, 15, 30, 60):
        raise ValueError("全刷周期无效")
    if config["location"] not in config["locations"]:
        raise ValueError("天气地点不存在")
    for place in config["locations"].values():
        if not (-90 <= place["latitude"] <= 90 and -180 <= place["longitude"] <= 180):
            raise ValueError("天气坐标无效")
    return config, files.read("secrets.json", {})


def setup_logging(root):
    logger = logging.getLogger("todoclock")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(str(Path(root) / "app.log"), maxBytes=1000000,
                                      backupCount=4, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    # Call sites only log event codes / safe error classes, never raw exceptions.
    return logger
