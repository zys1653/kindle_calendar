"""Mailbox regression tests: deterministic fake Graph, no real credentials."""
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'todoclock'))
from app.mail import Mail, plain, FOLDERS
from app.microsoft import Microsoft, MAIL_SCOPES
from app.network import HTTP, ServiceError
from app.storage import Store
from app.scratch import scratch
from app.simulator import make_demo
from app.render import render, font_path, PAGES
from app.interaction import context


class MailTests(unittest.TestCase):
    def setUp(self):
        self.temp = scratch(ROOT/'.scratch')
        self.path = self.temp.__enter__()
        self.store = Store(self.path)
        self.store.write('token.json', {'client_id': 'client', 'account_id': 'one', 'scope': MAIL_SCOPES})
        self.ms = Mock(client_id='client')
        self.ms.validate_graph_url = Microsoft.validate_graph_url
        self.mail = Mail(self.ms, self.store)

    def tearDown(self):
        self.temp.__exit__(None, None, None)

    def message(self, mid='1'):
        return {'id': mid, 'subject': 'PRIVATE SUBJECT', 'receivedDateTime': '2026-09-06T01:00:00Z', 'isRead': False}

    def test_classification_query_and_fixed_folders(self):
        self.ms.graph.return_value = {'value': []}
        for folder, _ in FOLDERS:
            self.mail.fetch(folder)
            path = self.ms.graph.call_args.args[1]
            self.assertIn('/me/mailFolders/', path)
            self.assertIn('%24top=30', path)
            if folder in ('focused', 'other'):
                self.assertIn('/inbox/', path)
                self.assertIn('inferenceClassification', path)
                self.assertIn(folder, path)
        self.assertNotIn('body,', path)

    def test_next_link_dedup_batches_and_new_sync_reset(self):
        url = 'https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?offset=30'
        self.ms.graph.side_effect = [{'value': [self.message()], '@odata.nextLink': url},
                                    {'value': [self.message(), self.message('2')]}, {'value': [self.message('3')]}]
        self.mail.fetch('focused')
        self.mail.fetch('focused', True)
        self.assertEqual(len(self.mail.cached('focused')['items']), 2)
        self.assertEqual(self.ms.graph.call_args.args[1], url)
        self.mail.fetch('focused')
        cache = self.mail.cached('focused')
        self.assertEqual([x['id'] for x in cache['items']], ['3'])
        self.assertIsNone(cache['next'])
        self.assertNotIn('items', self.mail.read('focused', {}))
        self.assertEqual(len(list(self.path.glob('*-batch-*'))), 1)

    def test_bad_next_link_and_cycle_preserve_cache(self):
        self.ms.graph.return_value = {'value': [self.message()]}
        self.mail.fetch('focused')
        for url in ['https://evil.example/v1.0/me', 'http://graph.microsoft.com/v1.0/me']:
            self.ms.graph.return_value = {'value': [], '@odata.nextLink': url}
            with self.assertRaises(ServiceError):
                self.mail.fetch('focused')
            self.assertEqual(len(self.mail.cached('focused')['items']), 1)
        url = 'https://graph.microsoft.com/v1.0/me/messages?next=1'
        self.ms.graph.return_value = {'value': [], '@odata.nextLink': url}
        self.mail.fetch('focused')
        with self.assertRaises(ServiceError):
            self.mail.fetch('focused', True)

    def test_partial_failure_and_throttle_not_success(self):
        self.ms.graph.side_effect = [{'value': []}, ServiceError('throttle', 429, 400)]
        with self.assertRaises(ServiceError) as caught:
            self.mail.sync()
        self.assertEqual(caught.exception.retry_after, 400)
        self.assertTrue(self.mail.cached('focused')['synced'])
        self.assertIn('error', self.mail.cached('other'))
        self.assertEqual(self.ms.graph.call_count, 2)

    def test_queue_restart_dedup_confirm_only_after_success(self):
        self.ms.graph.return_value = {'value': [self.message()]}
        self.mail.fetch('focused')
        self.mail.enqueue('1')
        self.mail.enqueue('1')
        reopened = Mail(self.ms, Store(self.path))
        self.assertEqual(len(reopened.queue()), 1)
        self.ms.graph.side_effect = ServiceError('offline')
        with self.assertRaises(ServiceError):
            reopened.flush()
        self.assertFalse(reopened.cached('focused')['items'][0]['isRead'])
        self.assertEqual(reopened.queue()[0]['state'], 'pending')
        self.ms.graph.side_effect = None
        self.ms.graph.return_value = {}
        reopened.flush()
        self.assertTrue(reopened.cached('focused')['items'][0]['isRead'])
        self.assertEqual(reopened.queue(), [])
        self.assertEqual(self.ms.graph.call_args.kwargs['json'], {'isRead': True})

    def test_permanent_failure_stops_retry_and_can_cancel(self):
        for status in (403, 404, 409, 412):
            self.mail.enqueue('1')
            self.ms.graph.side_effect = ServiceError('failed', status)
            self.mail.flush()
            self.assertEqual(self.mail.queue()[0]['state'], 'failed')
            self.ms.graph.reset_mock()
            self.mail.flush()
            self.ms.graph.assert_not_called()
            self.mail.cancel('1')

    def test_account_and_client_isolation_and_clear(self):
        self.mail.enqueue('1')
        self.store.write('token.json', {'client_id': 'client', 'account_id': 'two', 'scope': MAIL_SCOPES})
        self.assertEqual(self.mail.queue(), [])
        self.mail.enqueue('2')
        self.mail.clear()
        self.store.write('token.json', {'client_id': 'client', 'account_id': 'one', 'scope': MAIL_SCOPES})
        self.assertEqual(self.mail.queue()[0]['id'], '1')
        self.ms.client_id = 'different'
        self.assertFalse(self.mail.ready())
        self.assertEqual(self.mail.queue(), [])

    def test_body_preserves_paragraphs_ignores_scripts_and_cache_lru(self):
        self.assertEqual(plain({'contentType': 'html', 'content': '<p>你好 &amp; 世界</p><script>secret</script><p>下一段<br>换行</p>'}), '你好 & 世界\n\n下一段\n换行')
        self.ms.graph.return_value = dict(self.message(), body={'contentType': 'text', 'content': '第一段\n\n第二段'})
        body = self.mail.body('1', True)
        self.assertEqual(body['text'], '第一段\n\n第二段')
        self.assertEqual(self.ms.graph.call_args.kwargs['headers']['Prefer'], 'outlook.body-content-type="text"')
        self.ms.graph.reset_mock()
        self.assertEqual(self.mail.body('1'), body)
        self.ms.graph.assert_not_called()
        cache = {str(i): {'accessed': i, 'text': 'text'} for i in range(100)}
        self.store.write(self.mail.filename('bodies'), cache)
        self.mail.body('new', True)
        self.assertEqual(len(self.mail.read('bodies', {})), 100)
        self.assertNotIn('0', self.mail.read('bodies', {}))

    def test_oversized_body_not_cached(self):
        self.ms.graph.return_value = dict(self.message(), body={'contentType': 'text', 'content': 'x'*(1024*1024+1)})
        with self.assertRaisesRegex(ServiceError, '未完整加载'):
            self.mail.body('1', True)
        self.assertIsNone(self.mail.body('1'))

    def test_stream_size_limit_and_closed_response(self):
        session = Mock()
        response = session.request.return_value
        response.status_code = 200
        response.iter_content.return_value = [b'x'*20]
        with self.assertRaises(ServiceError):
            HTTP(session).request('GET', 'https://graph.microsoft.com/v1.0/me', max_bytes=10)
        response.close.assert_called_once()
        self.assertTrue(session.request.call_args.kwargs['stream'])


class AuthorizationTests(unittest.TestCase):
    setUp = MailTests.setUp
    tearDown = MailTests.tearDown
    def prepare(self, identity='one'):
        http = Mock()
        ms = Microsoft('client', self.store, http)
        ms.upgrade_identity = identity
        http.session.post.return_value.status_code = 200
        http.session.post.return_value.json.return_value = {'access_token': 'NEW-PRIVATE', 'refresh_token': 'NEW-REFRESH', 'scope': MAIL_SCOPES}
        http.request.return_value = {'id': identity, 'mail': 'PRIVATE@example.com'}
        return ms, http

    def test_upgrade_success_and_refresh_retains_scopes(self):
        ms, http = self.prepare()
        self.assertEqual(ms.poll_login('code', mail=True), 'success')
        token = self.store.read('token.json')
        self.assertEqual(token['account_id'], 'one')
        self.assertIn('Mail.ReadWrite', token['scope'])
        http.request.return_value = {'access_token': 'REFRESHED'}
        ms.token(force=True)
        self.assertIn('Mail.ReadWrite', http.request.call_args.kwargs['data']['scope'])

    def test_wrong_account_and_missing_scope_preserve_token(self):
        ms, http = self.prepare()
        previous = self.store.read('token.json')
        http.request.return_value = {'id': 'different'}
        with self.assertRaises(ServiceError):
            ms.poll_login('code', mail=True)
        self.assertEqual(self.store.read('token.json'), previous)
        http.session.post.return_value.json.return_value['scope'] = 'User.Read'
        with self.assertRaises(ServiceError):
            ms.poll_login('code', mail=True)
        self.assertEqual(self.store.read('token.json'), previous)

    def test_cancel_inflight_preserves_old_token(self):
        ms, http = self.prepare()
        previous = self.store.read('token.json')
        def profile(*args, **kwargs):
            ms.cancel_login()
            return {'id': 'one'}
        http.request.side_effect = profile
        self.assertEqual(ms.poll_login('code', mail=True), 'cancelled')
        self.assertEqual(self.store.read('token.json'), previous)

    def test_begin_requests_mail_scope_without_altering_old_token(self):
        ms, http = self.prepare()
        previous = self.store.read('token.json')
        ms.begin_mail_login()
        self.assertIn('Mail.ReadWrite', http.request.call_args.kwargs['data']['scope'])
        self.assertEqual(self.store.read('token.json'), previous)


class MailControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = scratch(ROOT/'.scratch')
        self.path = self.temp.__enter__()
        self.app = make_demo(ROOT/'todoclock', self.path)
        self.app.page = 'mail'
        self.font = font_path(self.app.root, self.app.config['font'], desktop=True)

    def tearDown(self):
        self.app.close()
        self.app.worker.shutdown(wait=True)
        self.app.status_worker.shutdown(wait=True)
        self.temp.__exit__(None, None, None)

    def test_pagination_keys_folder_reset_and_context(self):
        before = context(self.app)
        self.app.key(1)
        self.assertEqual(self.app.mail_page, 1)
        self.assertNotEqual(context(self.app), before)
        self.app.action(('mail_folder', 'other'))
        self.assertEqual(self.app.mail_page, 0)
        self.app.key(-1)
        self.assertEqual(self.app.mail_page, 0)

    def test_old_body_result_cannot_replace_new_selection(self):
        self.app.action(('mail_open', 'focused-0'))
        self.app.action(('mail_open', 'focused-1'))
        self.app.mail_finished('mail_body', {'id': 'focused-0', 'text': 'old'})
        self.assertEqual(self.app.mail_detail['id'], 'focused-1')
        self.assertNotIn('text', self.app.mail_detail)
        self.assertEqual(self.app.mail_queue, [])

    def test_offline_open_preserves_page_and_unread(self):
        self.app.key(1)
        self.app.status['wifi_on'] = False
        self.app.action(('mail_open', 'focused-6'))
        self.assertIn('尚未缓存', self.app.notice)
        self.assertFalse(self.app.mail_detail['isRead'])
        self.app.action(('mail_read', 'focused-6'))
        self.assertEqual(len(self.app.mail_queue), 1)
        self.assertFalse(self.app.mail_detail['isRead'])
        self.app.mail.flush()
        self.app.mail_snapshot()
        self.assertTrue(self.app.mail_detail['isRead'])
        self.app.action(('mail_back',))
        self.assertEqual(self.app.mail_page, 1)

    def test_new_mail_and_settings_hits_all_rotations(self):
        self.assertEqual([x[0] for x in PAGES], ['todo', 'mail', 'calendar', 'weather', 'timer', 'settings'])
        for rotation in (0, 90, 180, 270):
            self.app.config['rotation'] = rotation
            for page in ('mail', 'settings'):
                self.app.page, self.app.settings_page = page, 1
                image, hits = render(self.app, self.font)
                for (x1,y1,x2,y2), action in hits:
                    self.assertTrue(0 <= x1 < x2 <= image.width and 0 <= y1 < y2 <= image.height, action)
                if page == 'mail':
                    self.assertEqual(sum(action[0] == 'mail_folder' for _, action in hits), 6)
                    self.assertEqual(sum(action[0] == 'mail_open' for _, action in hits), self.app.mail_capacity)

    def test_renderer_never_reads_mail_store_or_network(self):
        with patch.object(self.app.store, 'read', side_effect=AssertionError('renderer I/O')), patch.object(HTTP, 'request', side_effect=AssertionError('renderer network')):
            render(self.app, self.font)

    def test_unbound_mail_never_schedules_requests(self):
        self.app.mail_ready = False
        with patch.object(self.app, 'submit') as submit:
            self.app.mail_tick(True)
            submit.assert_not_called()

    def test_frequency_manual_and_persistence(self):
        self.app.config['mail_minutes'] = 120
        self.app.action(('frequency', 'mail_minutes'))
        self.assertEqual(self.app.config['mail_minutes'], 0)
        self.assertEqual(self.app.store.read('preferences.json')['mail_minutes'], 0)
        self.app.mail.enqueue('focused-0')
        self.app.mail_snapshot()
        with patch.object(self.app, 'submit', return_value=True) as submit:
            self.app.mail_tick(True)
            self.assertEqual(submit.call_args.args[0], 'mail_flush')

    def test_body_pages_and_return(self):
        self.app.mail.body('focused-0', True)
        self.app.action(('mail_open', 'focused-0'))
        self.app.key(1)
        self.assertEqual(self.app.mail_body_page, 1)
        self.app.key(1000)
        self.assertEqual(self.app.mail_body_page, self.app.mail_body_pages-1)
        self.app.action(('mail_back',))
        self.assertIsNone(self.app.mail_detail)

    def test_next_page_failure_does_not_advance_or_cross_folder(self):
        self.app.mail_page = 7
        self.app.status['wifi_on'] = False
        self.app.key(1)
        self.assertEqual(self.app.mail_page, 7)
        self.app.action(('mail_folder', 'other'))
        self.app.mail_finished('mail_more', ('focused', 0, 8))
        self.assertEqual(self.app.mail_page, 0)

    def test_unexpected_error_log_does_not_expose_mail(self):
        from concurrent.futures import Future
        future = Future()
        future.set_exception(RuntimeError('PRIVATE SUBJECT secret@example.com'))
        self.app.jobs['mail_body'] = future
        with self.assertLogs('todoclock', level='WARNING') as captured:
            self.app.tick()
        self.assertNotIn('PRIVATE', ''.join(captured.output))
        self.assertNotIn('secret@', ''.join(captured.output))


if __name__ == '__main__':
    unittest.main()
