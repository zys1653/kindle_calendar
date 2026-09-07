"""Input actions execute immediately; feedback never gates another gesture."""
import time
from PIL import ImageChops


def context(app):
    return ((app.mail_folder, app.mail_page, (app.mail_detail or {}).get('id'), app.mail_body_page) if app.page == 'mail' else None, app.page, id(app.modal), app.modal_page, app.config['rotation'],
            app.list_index if app.page == 'todo' else None,
            app.task_page if app.page == 'todo' else None,
            app.settings_page if app.page == 'settings' else None,
            app.timer_tab if app.page == 'timer' else None,
            (app.show_hours if app.show_hours is not None else app.weather_view['rain']) if app.page == 'weather' else None)


class Feedback:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.clear()

    def clear(self):
        self.region = self.pressed = self.revision = None
        self.until = 0

    def event(self, kind, point, hits, revision):
        if kind == 'cancel':
            self.clear()
            return None
        region = next(((box, action) for box, action in reversed(hits)
                       if box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]), None)
        if kind == 'press':
            self.pressed = self.region = region
            self.revision = revision
            self.until = self.clock() + .08
        elif kind == 'tap':
            valid = region and region == self.pressed and revision == self.revision
            self.pressed = None
            if valid:
                self.region, self.until = region, self.clock() + .08
                return region[1]
            self.clear()
        return None

    def expire(self, revision):
        if revision != self.revision:
            self.clear()
        elif self.clock() >= self.until:
            self.region = None

    def paint(self, image):
        if self.region:
            image = image.copy()
            a, b, c, d = self.region[0]
            box = (int(a)+3, int(b)+3, int(c)-2, int(d)-2)
            image.paste(ImageChops.invert(image.crop(box)), box)
        return image
