import errno
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'todoclock'))
from app.display import Display
from app.interaction import Feedback, context
from app.power import sample, CoverTracker, read_field, diagnostics, sources
from app.scratch import scratch
from app.simulator import make_demo
from app.render import Canvas, render, font_path
from app.icons import icon, weather_kind


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.workspace = scratch(ROOT / '.scratch')
        self.root = self.workspace.__enter__()

    def tearDown(self):
        self.workspace.__exit__(None, None, None)

    def supply(self, path, **values):
        path = self.root / path
        path.mkdir(parents=True, exist_ok=True)
        for key, value in values.items():
            (path / key).write_text(str(value))
        return path

    def test_user_detached_and_attached_samples(self):
        self.supply('class/power_supply/max77696-battery', type='Battery', present=1,
                    online=1, status='Discharging', capacity=98)
        self.supply('class/power_supply/max77696-uic', type='USB', present=0)
        self.supply('class/power_supply/soda_chg', type='Mains', present=0, online=0)
        self.supply('class/power_supply/max77696-charger', present=0, online=1)
        self.supply('class/power_supply/max77696-eh', type='Mains', present=0, online=0)
        self.supply('class/power_supply/soda_boost', type='Mains', online=1)
        self.supply('devices/system/wario_charger/wario_charger0', charging=0)
        gauge = self.supply('class/power_supply/soda_fg', type='Battery')
        tracker = CoverTracker()
        self.assertIsNone(tracker.update(sample(self.root))['cover_present'])
        self.assertFalse(tracker.update(sample(self.root))['cover_present'])
        self.supply('class/power_supply/soda_fg', status='Full', capacity=100)
        attached = tracker.update(sample(self.root))
        self.assertTrue(attached['cover_present'])
        self.assertEqual(attached['cover'], 100)
        self.assertFalse(attached['cover_charging'])
        self.assertFalse(attached['external_power'])
        (gauge/'status').unlink()
        (gauge/'capacity').unlink()
        first = tracker.update(sample(self.root))
        self.assertIsNone(first['cover'])
        self.assertIsNone(first['cover_present'])
        self.assertFalse(tracker.update(sample(self.root))['cover_present'])

    def test_absent_overrides_alias_and_usb_present_overrides_online(self):
        self.supply('class/power_supply/soda_fg', type='Battery', present=0, capacity=100, status='Full')
        self.supply('devices/platform/soda/power_supply/soda_fg', type='Battery', capacity=100, status='Full')
        self.supply('class/power_supply/max77696-uic', type='USB', present=0, online=1)
        self.assertFalse(sample(self.root)['cover_present'])
        self.assertFalse(sample(self.root)['external_power'])
        self.supply('class/power_supply/max77696-uic', present=1)
        self.assertTrue(sample(self.root)['external_power'])

    def test_charging_from_cover_is_not_external_usb(self):
        self.supply('class/power_supply/main', type='Battery', status='Charging', capacity=85)
        self.supply('class/power_supply/soda_boost', type='Mains', online=1)
        self.assertTrue(sample(self.root)['charging'])
        self.assertFalse(sample(self.root)['external_power'])

    def test_partial_and_permission_never_become_absent(self):
        path = self.supply('class/power_supply/soda_fg', type='Battery', capacity=60)
        tracker = CoverTracker()
        for _ in range(3):
            self.assertIsNone(tracker.update(sample(self.root))['cover_present'])
        with patch.object(Path, 'read_text', side_effect=PermissionError(errno.EACCES, 'private')):
            self.assertEqual(read_field(path, 'status'), (None, 'permission'))
        with patch.object(Path, 'read_text', side_effect=OSError(errno.ENODATA, 'private')):
            self.assertEqual(read_field(path, 'status'), (None, 'no_data'))
        self.assertEqual(diagnostics(self.root)[str(path)]['status']['read'], 'missing')

    def test_power_aliases_are_deduplicated(self):
        a = self.supply('class/power_supply/soda_fg', type='Battery')
        b = self.supply('devices/platform/soda/power_supply/soda_fg', type='Battery')
        original = Path.resolve
        with patch.object(Path, 'resolve', lambda path, *args, **kw: a if path == b else original(path, *args, **kw)):
            self.assertEqual(sources(self.root), [a])

    def test_symbols_and_weather_artwork_need_no_fonts(self):
        canvas = Canvas((200, 100), 'nonexistent.ttf')
        canvas.button((0, 0, 80, 80), '−', ('duration', -1))
        canvas.button((100, 0, 180, 80), '+', ('duration', 1))
        self.assertEqual(canvas.image.getpixel((40, 40)), 0)
        self.assertEqual(canvas.image.getpixel((140, 30)), 0)
        kinds = ('sun', 'moon', 'cloud', 'partly', 'rain', 'snow', 'sleet', 'thunder', 'fog',
                 'temperature', 'humidity', 'wind', 'unknown')
        for kind in kinds:
            im = icon(kind, 80)
            self.assertIsNotNone(ImageChops.difference(im, Image.new('L', im.size, 255)).getbbox())
        self.assertEqual(weather_kind('150'), 'moon')
        self.assertEqual(weather_kind('404'), 'sleet')
        self.assertEqual(weather_kind('new-code'), 'unknown')

    def test_weather_keys_and_stable_task_actions(self):
        app = make_demo(ROOT/'todoclock', self.root/'state')
        try:
            app.page, app.show_hours = 'weather', True
            for _ in range(100): app.key(1)
            self.assertEqual(app.weather_page, 5)
            app.action(('weather_page', -1))
            self.assertEqual(app.weather_page, 4)
            for _ in range(100): app.key(-1)
            self.assertEqual(app.weather_page, 0)
            app.show_hours = False
            app.key(1)
            self.assertEqual(app.weather_page, 0)
            app.weather_view['hours'] = []
            app.show_hours = True
            app.key(1)
            self.assertEqual(app.weather_page, 0)
            app.page = 'todo'
            lid, task = app.tasks[0]
            app.tasks.reverse()
            app.action(('complete', lid, task['id']))
            self.assertEqual(app.outbox[0]['task_id'], task['id'])
            before = len(app.outbox)
            app.action(('complete', 'missing', 'missing'))
            self.assertEqual(len(app.outbox), before)
        finally:
            app.close()

    def test_page_transition_cancels_unfinished_gesture(self):
        app = make_demo(ROOT/'todoclock', self.root/'state')
        try:
            feedback = Feedback()
            hits = [((0, 0, 50, 50), ('complete', 'list', 'task'))]
            feedback.event('press', (20, 20), hits, context(app))
            app.action(('page', 'weather'))
            self.assertIsNone(feedback.event('tap', (20, 20), hits, context(app)))
        finally:
            app.close()

    def test_slow_display_keeps_latest_and_preserves_force_and_clicks(self):
        started, release, second = threading.Event(), threading.Event(), threading.Event()
        frames = []
        class Device:
            config = {}
            def show(self, image, force):
                frames.append((image.getpixel((0, 0)), force, self.config['rotation']))
                if len(frames) == 1:
                    started.set()
                    if not release.wait(3): raise RuntimeError('test stalled')
                else: second.set()
        display = Display(Device())
        app = make_demo(ROOT/'todoclock', self.root/'state')
        try:
            display.submit(Image.new('L', (5, 5), 0), {'rotation': 90}, False, 'first', [])
            self.assertTrue(started.wait(2))
            app.page = 'timer'
            original = app.duration
            f = Feedback()
            hits = [((0, 0, 50, 50), ('duration', 1))]
            for i in range(10):
                f.event('press', (20, 20), hits, context(app))
                action = f.event('tap', (20, 20), hits, context(app))
                app.action(action)
                display.submit(Image.new('L', (5, 5), i+1), {'rotation': 270}, i == 0, 'latest', hits)
            self.assertEqual(app.duration, original+10)
            self.assertEqual(len(frames), 1)
            release.set()
            self.assertTrue(second.wait(2))
        finally:
            release.set()
            display.close()
            app.close()
        self.assertEqual(frames, [(0, False, 90), (10, True, 270)])
        self.assertEqual(display.snapshot()[0], 'latest')
        self.assertFalse(display.thread.is_alive())

    def test_display_failure_is_reported_without_raw_error(self):
        failed = threading.Event()
        class Device:
            def show(self, *args):
                failed.set()
                raise RuntimeError('private details')
        display = Display(Device())
        display.submit(Image.new('L', (2, 2)), {}, False, None, [])
        self.assertTrue(failed.wait(2))
        display.thread.join(2)
        with self.assertRaisesRegex(RuntimeError, '屏幕刷新失败'):
            display.snapshot()
        display.close()

    def test_shutdown_discards_pending_and_waits_for_active_writer(self):
        started, release = threading.Event(), threading.Event()
        frames = []
        class Device:
            def show(self, image, force):
                frames.append(1)
                started.set()
                release.wait(3)
        display = Display(Device())
        display.submit(Image.new('L', (2, 2)), {}, False, None, [])
        self.assertTrue(started.wait(2))
        display.submit(Image.new('L', (2, 2)), {}, True, None, [])
        closer = threading.Thread(target=display.close)
        closer.start()
        with display.condition:
            # Synchronize on the close state instead of sleeping for a guessed delay.
            display.condition.wait_for(lambda: display.stopping, timeout=2)
            self.assertTrue(display.stopping)
        self.assertTrue(display.thread.is_alive())
        release.set()
        closer.join(2)
        self.assertFalse(closer.is_alive())
        self.assertFalse(display.thread.is_alive())
        self.assertEqual(frames, [1])
