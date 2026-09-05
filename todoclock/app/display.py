"""Single display owner, one active frame and one replaceable pending frame."""
import copy
import threading


class Display:
    def __init__(self, device):
        self.device = device
        self.condition = threading.Condition()
        self.pending = None
        self.completed = (None, [])
        self.error = None
        self.stopping = False
        self.thread = threading.Thread(target=self._run, name='display', daemon=True)
        self.thread.start()

    def submit(self, image, config, force, context, hits):
        with self.condition:
            if self.stopping:
                return
            force = force or bool(self.pending and self.pending[2])
            self.pending = (image.copy(), copy.deepcopy(config), force, context, list(hits))
            self.condition.notify()

    def snapshot(self):
        with self.condition:
            if self.error:
                raise RuntimeError('屏幕刷新失败，请退出并运行诊断') from None
            return self.completed

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.stopping or self.pending is not None)
                if self.stopping:
                    return
                image, config, force, context, hits = self.pending
                self.pending = None
            try:
                self.device.config = config
                self.device.show(image, force)
            except Exception:
                with self.condition:
                    self.error = True
                    self.pending = None
                    self.stopping = True
                return
            with self.condition:
                self.completed = (context, hits)

    def close(self):
        with self.condition:
            self.stopping = True
            self.pending = None
            self.condition.notify()
        # Device commands time out at 10 seconds. Do not restore underneath a writer.
        self.thread.join(12)
        if self.thread.is_alive():
            raise RuntimeError('屏幕线程未退出，由看护进程恢复')
