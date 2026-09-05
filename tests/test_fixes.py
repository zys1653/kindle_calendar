import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from concurrent.futures import Future
import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'todoclock'))
from app.device import battery_status
from app.input import key_direction, Inputs, EVENT
from app.interaction import Feedback
from app.network import HTTP, ServiceError
from app.render import Canvas
from app.scratch import scratch
from app.simulator import make_demo
from app.weather import QWeather, api_origin
from app.storage import Store


class FixTests(unittest.TestCase):
    def test_regional_api_hosts(self):
        self.assertEqual(api_origin('https://example.re.qweatherapi.com/'),
                         'https://example.re.qweatherapi.com')
        self.assertEqual(api_origin(' EXAMPLE.XY.QWEATHERAPI.COM '),
                         'https://example.xy.qweatherapi.com')

    def test_regional_host_validation_rejects_invalid_destinations(self):
        for host in ('qweatherapi.com', '.re.qweatherapi.com', 'example..qweatherapi.com',
                     '-example.re.qweatherapi.com', 'example-.re.qweatherapi.com',
                     'example.re.qweatherapi.com.evil.org', 'example.re.qweatherapi.com@evil.org',
                     'example.re.qweatherapi.com:443', 'example.re.qweatherapi.com/path',
                     'example.re.qweatherapi.com?key=x', 'http://example.re.qweatherapi.com',
                     'a'*64 + '.re.qweatherapi.com'):
            with self.subTest(host=host), self.assertRaises(ServiceError):
                api_origin(host)

    def test_keys_follow_physical_right_next(self):
        self.assertEqual(key_direction(104), 1)
        self.assertEqual(key_direction(109), -1)

    def test_arrows_do_not_require_font_glyphs(self):
        c = Canvas((160, 80), 'missing-font.ttf')
        c.button((0, 0, 70, 70), '‹', ('prev',))
        c.button((80, 0, 150, 70), '›', ('next',))
        self.assertEqual(c.image.getpixel((28, 35)), 0)
        self.assertEqual(c.image.getpixel((122, 35)), 0)

    def test_quick_tap_executes_without_waiting_for_frame(self):
        now = [0.0]
        feedback = Feedback(lambda: now[0])
        hits = [((0, 0, 40, 40), ('duration', 1))]
        for _ in range(10):
            feedback.event('press', (20, 20), hits, 1)
            self.assertEqual(feedback.event('tap', (20, 20), hits, 1), ('duration', 1))
        base = Image.new('L', (50, 50), 255)
        self.assertEqual(feedback.paint(base).getpixel((20, 20)), 0)
        self.assertEqual(base.getpixel((20, 20)), 255)
        now[0] = .09
        feedback.expire(1)
        self.assertIsNone(feedback.region)
        self.assertIsNone(feedback.event('tap', (20, 20), hits, 1))

    def test_cancel_drag_and_changed_content_never_dispatch(self):
        hits = [((0, 0, 40, 40), ('complete', 0))]
        for kind, point, rev in [('cancel', None, 1), ('tap', (45, 45), 1), ('tap', (20, 20), 2)]:
            feedback = Feedback()
            feedback.event('press', (20, 20), hits, 1)
            feedback.event(kind, point, hits, rev)
            self.assertIsNone(feedback.region)
        feedback.event('tap', (45, 45), hits, 1)  # release without press

    def test_linux_press_and_release_in_one_read(self):
        reader = Inputs.__new__(Inputs)
        from app.input import TouchFrame
        reader.fds, reader.buffers, reader.held = {3: 'touch'}, {3: b''}, {}
        reader.frame = TouchFrame()
        reader.calibration = {'matrix': [1, 0, 0, 0, 1, 0]}
        samples = [(3, 53, 100), (3, 54, 200), (3, 57, 1), (0, 0, 0), (3, 57, -1), (0, 0, 0)]
        data = b''.join(EVENT.pack(0, 0, *sample) for sample in samples)
        with patch('app.input.select.select', return_value=([3], [], [])), patch('app.input.os.read', return_value=data):
            events = reader.poll(90)
        self.assertEqual([e[0] for e in events], ['press', 'tap'])
        self.assertEqual(events[0][1], events[1][1])

    def test_connection_diagnostics_never_expose_exception_text(self):
        for exc, kind in [(requests.exceptions.SSLError('secret-key'), 'TLS_ERROR'),
                          (requests.exceptions.ReadTimeout('secret-key'), 'READ_TIMEOUT'),
                          (requests.exceptions.ConnectionError(socket.gaierror('secret-key')), 'DNS_ERROR')]:
            session = Mock()
            session.request.side_effect = exc
            with self.assertRaises(ServiceError) as cm:
                HTTP(session).request('GET', 'https://test.invalid')
            self.assertEqual(cm.exception.kind, kind)
            self.assertNotIn('secret-key', cm.exception.diagnostic())

    def test_weather_error_includes_endpoint_and_status(self):
        with scratch(ROOT / '.scratch') as path:
            http = Mock()
            http.request.side_effect = ServiceError('无访问权限', 403, kind='HTTP_ERROR')
            service = QWeather({'api_host': 'test.qweatherapi.com', 'api_key': 'secret'}, Store(path), http)
            place = {'latitude': 39.1, 'longitude': 117.1}
            with self.assertRaises(ServiceError) as cm:
                service.sync(place)
            self.assertEqual(cm.exception.stage, 'current')
            self.assertIn('HTTP 403', service.cached(place)['current_error'])
            self.assertNotIn('secret', service.cached(place)['current_error'])
            self.assertEqual(http.request.call_count, 1)

    def test_power_detach_and_charge_are_fresh(self):
        with scratch(ROOT / '.scratch') as root:
            def write(relative, values):
                path = root / relative
                path.mkdir(parents=True, exist_ok=True)
                for name, value in values.items():
                    (path / name).write_text(str(value))
            write('class/power_supply/main', dict(type='Battery', capacity=80, status='Discharging'))
            write('class/power_supply/soda', dict(type='Battery', capacity=60, status='Discharging', present=1))
            write('devices/platform/soda/power_supply/soda_fg', dict(capacity=60, status='Discharging'))
            write('devices/system/wario_charger/wario_charger0', dict(charging=1))
            status = battery_status(root, False)
            self.assertEqual(status['cover'], 60)
            self.assertTrue(status['charging'])
            write('class/power_supply/soda', dict(present=0))
            status = battery_status(root, False)
            self.assertFalse(status['cover_present'])
            self.assertIsNone(status['cover'])
            write('devices/system/wario_charger/wario_charger0', dict(charging=0))
            write('class/power_supply/usb', dict(type='USB', online=1))
            status = battery_status(root, False)
            self.assertFalse(status['charging'])
            self.assertTrue(status['external_power'])

    def test_cover_capacity_alone_is_not_presence(self):
        with scratch(ROOT / '.scratch') as root:
            path = root / 'devices/platform/soda/power_supply/soda_fg'
            path.mkdir(parents=True)
            (path / 'capacity').write_text('60')
            status = battery_status(root, False)
            self.assertIsNone(status['cover_present'])
            self.assertIsNone(status['cover'])

    def test_power_poll_changes_only_when_needed_and_rejects_old_snapshot(self):
        with scratch(ROOT / '.scratch') as path:
            app = make_demo(ROOT / 'todoclock', path)
            try:
                app.status['wifi_on'] = app.device.data['wifi_on'] = False
                app.last_status = float('inf')
                app.last_power = 100
                app.device.data['battery'] = 20
                revision = app.revision
                with patch('app.controller.time.monotonic', return_value=100.5):
                    app.tick()
                self.assertNotEqual(app.status['battery'], 20)
                old = Future()
                old.set_result(dict(battery=99, wifi_on=False, wifi='已关闭', light=0))
                app.status_future = old
                with patch('app.controller.time.monotonic', return_value=101.0):
                    app.tick()
                self.assertEqual(app.status['battery'], 20)
                revision = app.revision
                with patch('app.controller.time.monotonic', return_value=102.0):
                    app.tick()
                self.assertEqual(app.revision, revision)
            finally:
                app.close()
