"""Visible press feedback shared by the device and desktop simulator."""
import time
from PIL import ImageChops


class Feedback:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.clear()

    def clear(self):
        self.region = None
        self.revision = None
        self.pending = False
        self.shown_at = None
        self.since = self.clock()

    def event(self, kind, point, hits, revision):
        if kind == 'cancel':
            self.clear()
            return
        region = next(((box, action) for box, action in reversed(hits)
                       if box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]), None)
        if kind == 'press' and not self.pending:
            self.clear()
            self.region, self.revision = region, revision
        elif kind == 'tap' and not self.pending:
            if self.region != region or revision != self.revision:
                self.clear()
            elif region:
                self.pending = True

    def paint(self, image):
        if self.region:
            image = image.copy()
            a, b, c, d = self.region[0]
            box = (int(a)+3, int(b)+3, int(c)-2, int(d)-2)
            image.paste(ImageChops.invert(image.crop(box)), box)
        return image

    def shown(self):
        if self.region and self.shown_at is None:
            self.shown_at = self.clock()

    def take(self, revision):
        if not self.region:
            return None
        if revision != self.revision or self.clock() - self.since > 3:
            self.clear()
            return None
        if self.pending and self.shown_at is not None and self.clock() - self.shown_at >= 0.18:
            action = self.region[1]
            self.clear()
            return action
