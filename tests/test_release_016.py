"""0.1.6 unified account, aggregate status and queue regressions."""
from concurrent.futures import Future
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_mail as fixtures
from app.microsoft import Microsoft, SCOPES, LEGACY_SCOPES
from app.network import ServiceError
from app.statusbar import sync_ready
from app.render import render, Canvas


class SummaryTests(unittest.TestCase):
    setUp = fixtures.MailTests.setUp
    tearDown = fixtures.MailTests.tearDown

    def test_full_success_only_and_account_isolation(self):
        self.ms.graph.return_value = {'value': []}
        self.assertEqual(self.mail.summary()['synced'], 0)
        with patch('app.mail.time.time', return_value=100):
            self.mail.sync()
        self.assertEqual(self.mail.summary(), {'synced': 100, 'error': ''})
        self.ms.graph.side_effect = [{'value': []}, ServiceError('failed', 429)]
        with patch('app.mail.time.time', return_value=200), self.assertRaises(ServiceError):
            self.mail.sync()
        self.assertEqual(self.mail.summary()['synced'], 100)
        self.assertTrue(self.mail.summary()['error'])
        self.store.write('token.json', {'account_id': 'other', 'client_id': 'client', 'scope': SCOPES})
        self.assertEqual(self.mail.summary()['synced'], 0)

    def test_old_cache_history_and_body_never_imply_full_sync(self):
        self.ms.graph.return_value = {'value': [], '@odata.nextLink': 'https://graph.microsoft.com/v1.0/me/messages?next=1'}
        self.mail.fetch('focused')
        self.ms.graph.return_value = {'value': []}
        self.mail.fetch('focused', True)
        self.ms.graph.return_value = {'id': 'one', 'body': {'contentType': 'text', 'content': 'text'}}
        self.mail.body('one', True)
        self.assertEqual(self.mail.summary()['synced'], 0)

    def test_interrupted_sync_is_persistently_incomplete(self):
        self.store.write(self.mail.filename('sync'), {'synced': 100, 'error': ''})
        self.ms.graph.side_effect = RuntimeError('interrupted')
        with self.assertRaises(RuntimeError):
            self.mail.sync()
        self.assertEqual(self.mail.summary()['synced'], 100)
        self.assertTrue(self.mail.summary()['error'])

    def test_retry_success_clears_persisted_error(self):
        self.store.write(self.mail.filename('sync'), {'synced': 1, 'error': 'failed'})
        self.ms.graph.return_value = {'value': []}
        self.mail.sync()
        self.assertGreater(self.mail.summary()['synced'], 1)
        self.assertFalse(self.mail.summary()['error'])


class AggregateTests(unittest.TestCase):
    def app(self):
        return SimpleNamespace(todo={'synced': 100, 'lists': []}, weather_synced=100,
            mail_synced=100, mail_summary={'error': ''}, mail_ready=True,
            weather_view={'errors': []}, mail_queue=[], outbox=[], jobs={}, errors={},
            now=datetime.fromtimestamp(110, timezone.utc),
            config={'todo_minutes': 30, 'weather_minutes': 30, 'mail_minutes': 15})

    def test_mail_required_and_manual_freshness(self):
        app = self.app()
        self.assertTrue(sync_ready(app))
        app.mail_synced = 0
        self.assertFalse(sync_ready(app))
        app.mail_synced = 100
        app.now = datetime.fromtimestamp(1010, timezone.utc)
        self.assertFalse(sync_ready(app))
        app.config['mail_minutes'] = 0
        self.assertTrue(sync_ready(app))
        app.mail_ready = False
        self.assertFalse(sync_ready(app))

    def test_mail_jobs_queue_and_partial_errors(self):
        for field, name in [('jobs', 'mail'), ('jobs', 'mail_flush'), ('errors', 'mail'), ('errors', 'mail_flush')]:
            app = self.app()
            getattr(app, field)[name] = True
            self.assertFalse(sync_ready(app))
        app = self.app()
        app.mail_summary['error'] = 'partial'
        self.assertFalse(sync_ready(app))
        for state in ('pending', 'failed'):
            app = self.app()
            app.mail_queue = [{'state': state}]
            self.assertFalse(sync_ready(app))

    def test_history_and_body_jobs_do_not_affect_header(self):
        app = self.app()
        app.jobs = {'mail_more': True, 'mail_body': True}
        app.errors = {'mail_more': True, 'mail_body': True}
        self.assertTrue(sync_ready(app))


class UnifiedLoginTests(unittest.TestCase):
    setUp = fixtures.MailTests.setUp
    tearDown = fixtures.MailTests.tearDown
    prepare = fixtures.AuthorizationTests.prepare

    def test_plain_login_requests_both_services_and_checks_identity(self):
        ms, http = self.prepare()
        ms.begin_login()
        self.assertEqual(http.request.call_args.kwargs['data']['scope'], SCOPES)
        http.request.return_value = {'id': 'wrong'}
        before = self.store.read('token.json')
        with self.assertRaises(ServiceError):
            ms.poll_login('code')
        self.assertEqual(self.store.read('token.json'), before)

    def test_plain_login_cancellation_keeps_token(self):
        ms, http = self.prepare()
        before = self.store.read('token.json')
        ms.cancel_login()
        self.assertEqual(ms.poll_login('code'), 'cancelled')
        self.assertEqual(self.store.read('token.json'), before)

    def test_legacy_refresh_does_not_request_ungranted_scopes(self):
        ms, http = self.prepare()
        self.store.write('token.json', {'client_id': 'client', 'refresh_token': 'old'})
        http.request.return_value = {'access_token': 'refreshed'}
        ms.token(force=True)
        self.assertEqual(http.request.call_args.kwargs['data']['scope'], LEGACY_SCOPES)
        self.assertEqual(self.store.read('token.json')['scope'], LEGACY_SCOPES)


class UnifiedControllerTests(unittest.TestCase):
    setUp = fixtures.MailControllerTests.setUp
    tearDown = fixtures.MailControllerTests.tearDown

    def seed_queue(self):
        self.app.store.write('outbox.json', [
            {'list_id': 'personal', 'task_id': '0', 'state': 'pending'},
            {'list_id': 'personal', 'task_id': '1', 'state': 'conflict', 'error': 'changed'}])
        self.app.store.write(self.app.mail.filename('queue'), [
            {'id': 'focused-0', 'state': 'pending'}, {'id': 'focused-1', 'state': 'failed'}])
        self.app.refresh_cache()

    def test_combined_counts_cancel_and_clear_keep_pending(self):
        self.seed_queue()
        self.assertEqual(self.app.queue_label, '等待同步 2 · 需处理 2')
        self.app.action(('outbox',))
        self.assertIn('待办完成', self.app.modal[1])
        self.app.key(2)
        self.assertIn('邮件已读', self.app.modal[1])
        self.app.action(('queue_cancel', 'mail', 'focused-0'))
        self.assertEqual(self.app.queue_label, '等待同步 1 · 需处理 2')
        self.app.action(('clear_conflicts',))
        self.assertEqual(self.app.queue_label, '等待同步 1')
        self.assertEqual(len(self.app.outbox), 1)
        self.assertFalse(self.app.mail_queue)
        self.app.action(('queue_cancel', 'todo', 'personal', '0'))
        self.assertEqual(self.app.queue_label, '等待同步 0')
        self.app.errors.update(flush='offline', mail_flush='offline')
        self.app.refresh_cache()
        self.assertNotIn('flush', self.app.errors)
        self.assertNotIn('mail_flush', self.app.errors)

    def test_login_success_initial_sync_even_in_manual_mode_once(self):
        self.app.config.update(todo_minutes=0, weather_minutes=0, mail_minutes=0)
        future = Future()
        future.set_result('success')
        self.app.jobs['login_poll'] = future
        with patch.object(self.app, 'sync_todo') as todo, patch.object(self.app, 'sync_mail') as mail:
            self.app.tick()
            self.app.tick()
            todo.assert_called_once_with()
            mail.assert_called_once_with()

    def test_unified_login_action_routes_one_flow(self):
        with patch.object(self.app, 'submit') as submit:
            self.app.action(('login',))
            self.assertEqual(submit.call_args.args, ('login_begin', self.app.microsoft.begin_login))

    def test_settings_actions_and_target_sizes_all_rotations(self):
        self.app.page = 'settings'
        for rotation in (0, 90, 180, 270):
            self.app.config['rotation'] = rotation
            for page in (0, 1):
                self.app.settings_page = page
                image, hits = render(self.app, self.font)
                actions = [a for _, a in hits]
                self.assertNotIn(('mail_sync',), actions)
                self.assertNotIn(('mail_login',), actions)
                self.assertNotIn(('settings_page', 2), actions)
                self.assertEqual(('exit',) in actions, page == 0)
                self.assertEqual(('frequency', 'mail_minutes') in actions, page == 0)
                self.assertEqual(('login',) in actions, page == 1)
                self.assertEqual(('outbox',) in actions, page == 1)
                for (x1,y1,x2,y2), action in hits:
                    self.assertTrue(0 <= x1 < x2 <= image.width and 0 <= y1 < y2 <= image.height)
                    if x1 >= 192:
                        self.assertGreaterEqual(y2-y1, 60, action)

    def test_header_draws_date_and_all_sync_times(self):
        for rotation, size in ((90, 34), (0, 32)):
            self.app.config['rotation'] = rotation
            calls = []
            original = Canvas.text
            def record(canvas, xy, value, font_size=30, fill=0):
                calls.append((xy, str(value), font_size))
                return original(canvas, xy, value, font_size, fill)
            with patch.object(Canvas, 'text', record):
                render(self.app, self.font)
            self.assertTrue(any('周' in text and font_size == size and xy[1] == 3 for xy,text,font_size in calls))
            for name in ('待办 ', '天气 ', '邮箱 '):
                self.assertTrue(any(text.startswith(name) and xy[1] < 157 for xy,text,_ in calls))


if __name__ == '__main__':
    unittest.main()
