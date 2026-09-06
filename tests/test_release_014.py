from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'todoclock'))
from app.statusbar import body_battery, sync_ready
from app.controller import Controller


class HeaderLogicTests(unittest.TestCase):
    def ready_app(self):
        return SimpleNamespace(todo={'synced': 1000, 'lists': []}, weather_synced=1000,
                               weather_view={'errors': []}, jobs={}, errors={}, outbox=[],
                               config={'todo_minutes': 30, 'weather_minutes': 30},
                               now=datetime.fromtimestamp(1100, timezone.utc))

    def test_both_services_must_be_successful_and_complete(self):
        app = self.ready_app()
        self.assertTrue(sync_ready(app))
        app.weather_synced = 0
        self.assertFalse(sync_ready(app))
        app.weather_synced = 1000
        app.todo['synced'] = 0
        self.assertFalse(sync_ready(app))

    def test_active_jobs_errors_partial_lists_and_outbox_need_attention(self):
        for name in ('todo', 'weather', 'flush'):
            for field in ('jobs', 'errors'):
                app = self.ready_app()
                getattr(app, field)[name] = 'pending'
                self.assertFalse(sync_ready(app))
        app = self.ready_app()
        app.outbox = [{'state': 'pending'}]
        self.assertFalse(sync_ready(app))
        app = self.ready_app()
        app.todo['lists'] = [{'error': 'partial'}]
        self.assertFalse(sync_ready(app))
        app = self.ready_app()
        app.weather_view['errors'] = ['daily']
        self.assertFalse(sync_ready(app))

    def test_expiry_and_manual_only_sync(self):
        app = self.ready_app()
        app.now = datetime.fromtimestamp(3000, timezone.utc)
        self.assertFalse(sync_ready(app))
        app.config.update(todo_minutes=0, weather_minutes=0)
        self.assertTrue(sync_ready(app))

    def test_body_has_one_charging_label_without_mutating_cover(self):
        for charging, external in ((True, False), (False, True), (True, True)):
            status = dict(battery=98, charging=charging, external_power=external,
                          cover=100, cover_present=True, cover_charging=False)
            original = dict(status)
            self.assertEqual(body_battery(status), '本体 98% 充电')
            self.assertEqual(status, original)
        self.assertEqual(body_battery({'battery': 98}), '本体 98%')

    def test_settings_key_boundaries_and_direct_page_selection(self):
        app = Controller.__new__(Controller)
        app.page, app.modal, app.revision, app.settings_page = 'settings', None, 0, 0
        app.key(-1)
        self.assertEqual(app.settings_page, 0)
        app.key(1)
        app.key(1)
        self.assertEqual(app.settings_page, 1)
        app._action('settings_page', [0])
        self.assertEqual(app.settings_page, 0)
        app._action('settings_page', [1])
        self.assertEqual(app.settings_page, 1)
        app.key(-1)
        self.assertEqual(app.settings_page, 0)

    def test_manual_full_refresh_preserves_config_and_page(self):
        app = Controller.__new__(Controller)
        app.page, app.force_refresh = 'weather', False
        app.config = {'full_refresh_minutes': 15}
        app._action('full_refresh', [])
        self.assertTrue(app.force_refresh)
        self.assertEqual(app.page, 'weather')
        self.assertEqual(app.config['full_refresh_minutes'], 15)
