import time


class Timer:
    def __init__(self, countdown=False, value=0, clock=time.monotonic):
        self.countdown, self.value, self.clock = countdown, float(value), clock
        self.started = None
        self.finished = False

    def seconds(self):
        delta = self.clock() - self.started if self.started is not None else 0
        return max(0, self.value - delta) if self.countdown else self.value + delta

    def toggle(self):
        if self.started is None:
            if not self.countdown or self.value > 0:
                self.started = self.clock()
                self.finished = False
        else:
            self.value = self.seconds()
            self.started = None

    def reset(self, value=0):
        self.value, self.started, self.finished = float(value), None, False

    def tick(self):
        if self.countdown and self.started is not None and self.seconds() <= 0:
            self.value, self.started, self.finished = 0, None, True
            return True
        return False

    def snapshot(self):
        return {"seconds": self.seconds(), "was_running": self.started is not None}


def format_seconds(value):
    value = max(0, int(value))
    return "{:02d}:{:02d}:{:02d}".format(value // 3600, value // 60 % 60, value % 60)
