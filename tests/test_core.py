import copy
from datetime import datetime
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from concurrent.futures import Future

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'todoclock'))
from app.storage import Store, configuration
from app.scratch import scratch
from app.network import HTTP, ServiceError
from app.weather import QWeather, api_origin, percent, normalize, view
from app.microsoft import Microsoft, due_date, tasks_for
from app.timers import Timer
from app.device import dimensions, logical_to_native, native_to_logical, SimDevice
from app.input import TouchFrame
from app.calibrate import solve_affine
from app.controller import Controller
from app.simulator import make_demo
from app.render import render, font_path, hit_test
from app import guardian


class WorkingDirectory(unittest.TestCase):
    def setUp(self):
        self.context = scratch(ROOT / '.scratch')
        self.path = self.context.__enter__()
        self.store = Store(self.path)

    def tearDown(self):
        self.context.__exit__(None, None, None)


class StorageTests(WorkingDirectory):
    def test_defaults_are_independent(self):
        default = {'a': []}
        self.store.read('missing.json', default)['a'].append(1)
        self.assertEqual(default, {'a': []})

    def test_failed_replace_preserves_queue(self):
        self.store.write('queue.json', [1])
        with patch('app.storage.os.replace', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.store.write('queue.json', [2])
        self.assertEqual(Store(self.path).read('queue.json'), [1])
        self.assertFalse(list(self.path.glob('.write-*')))

    def test_corrupt_json_does_not_reset(self):
        (self.path / 'queue.json').write_text('{broken')
        with self.assertRaises(RuntimeError):
            self.store.read('queue.json', [])

    def test_recursive_config_and_validation(self):
        config = Store(self.path / 'config')
        config.write('default.json', json.loads((ROOT/'todoclock/config/default.json').read_text(encoding='utf-8')))
        config.write('local.json', {'device': {'verified': True}})
        loaded, _ = configuration(self.path)
        self.assertTrue(loaded['device']['verified'])
        self.assertEqual(loaded['device']['frontlight_max'], 24)
        config.write('local.json', {'rotation': 45})
        with self.assertRaises(ValueError):
            configuration(self.path)


class HTTPTests(unittest.TestCase):
    def test_redirect_rejected_and_timeouts_set(self):
        session = Mock()
        session.request.return_value = Mock(status_code=302, headers={})
        with self.assertRaises(ServiceError):
            HTTP(session).request('GET', 'https://example.org')
        self.assertFalse(session.request.call_args.kwargs['allow_redirects'])
        self.assertEqual(session.request.call_args.kwargs['timeout'], (8, 20))

    def test_network_error_does_not_leak(self):
        import requests
        session = Mock()
        session.request.side_effect = requests.ConnectionError('SECRET_KEY')
        with self.assertRaises(ServiceError) as error:
            HTTP(session).request('GET', 'https://example.org')
        self.assertNotIn('SECRET', str(error.exception))

    def test_throttle_delay(self):
        session = Mock()
        session.request.return_value = Mock(status_code=429, headers={'Retry-After': '300'})
        with self.assertRaises(ServiceError) as error:
            HTTP(session).request('GET', 'https://example.org')
        self.assertEqual(error.exception.retry_after, 300)

    def test_empty_success(self):
        session = Mock()
        session.request.return_value = Mock(status_code=204)
        self.assertEqual(HTTP(session).request('PATCH', 'https://example.org'), {})


class WeatherTests(WorkingDirectory):
    def setUp(self):
        super().setUp()
        self.place = {'latitude': 39.14, 'longitude': 117.15}
        self.http = Mock()
        self.service = QWeather({'api_host': 'abc.qweatherapi.com', 'api_key': 'PRIVATE'}, self.store, self.http)

    def test_host_validation(self):
        self.assertEqual(api_origin(' https://abc.qweatherapi.com/ '), 'https://abc.qweatherapi.com')
        for value in ['http://abc.qweatherapi.com', 'abc.qweatherapi.com@evil.org', 'abc.qweatherapi.com.evil.org', 'abc.qweatherapi.com/path', 'localhost', 'abc.qweatherapi.com?key=x']:
            with self.subTest(value=value), self.assertRaises(ServiceError):
                api_origin(value)

    def test_probabilities_and_units(self):
        self.assertEqual(percent(.31), 31)
        self.assertEqual(percent(0), 0)
        for value in [None, 31, -1, 'unknown']:
            self.assertIsNone(percent(value))
        data = normalize({'temperature': {'value': 12, 'unit': '°C'}, 'humidity': .65,
                          'wind': {'speed': {'value': 2, 'unit': 'm/s'}}})
        self.assertEqual(data['humidity'], 65)
        self.assertEqual(data['wind_unit'], 'm/s')
        self.assertIsNone(data['pop'])

    def test_three_endpoints_and_header_auth(self):
        self.http.request.side_effect = [{'temperature': {'value': 20}}, {'days': []}, {'hours': []}]
        self.service.sync(self.place)
        calls = self.http.request.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertIn('/weather/v1/current/39.14/117.15', calls[0].args[1])
        for call in calls:
            self.assertEqual(call.kwargs['headers'], {'X-QW-Api-Key': 'PRIVATE'})
            self.assertNotIn('key', call.kwargs['params'])
            self.assertNotIn('PRIVATE', call.args[1])
        self.assertEqual(calls[1].kwargs['params']['days'], 3)
        self.assertEqual(calls[2].kwargs['params']['hours'], 24)

    def test_partial_failure_keeps_old_part(self):
        previous = {'hourly': {'data': {'hours': [{'forecastTime': 'old'}]}, 'fetched': 10}}
        self.store.write('weather.json', {self.service.cache_key(self.place): previous})
        self.http.request.side_effect = [{'temperature': {'value': 20}}, {'days': []}, ServiceError('offline')]
        with self.assertRaises(ServiceError):
            self.service.sync(self.place)
        result = self.service.cached(self.place)
        self.assertEqual(result['hourly']['fetched'], 10)
        self.assertIn('hourly_error', result)
        self.assertEqual(result['current']['data']['temperature']['value'], 20)

    def test_credential_failure_stops_further_requests(self):
        self.http.request.side_effect = ServiceError('auth', 401)
        with self.assertRaises(ServiceError):
            self.service.sync(self.place)
        self.assertEqual(self.http.request.call_count, 1)

    def test_location_cache_isolation(self):
        self.http.request.side_effect = [{'temperature': {}}, {'days': []}, {'hours': []}]
        self.service.sync(self.place)
        self.assertEqual(self.service.cached({'latitude': 39.54, 'longitude': 116.68}), {})

    def test_weather_day_selects_daytime_over_interval_start(self):
        today = {'forecastStartTime': '2026-09-04T22:00+08:00',
                 'daytime': {'forecastStartTime': '2026-09-05T07:00+08:00', 'precipitation': {'type': 'rain'}},
                 'temperatureMax': {'value': 26}}
        tomorrow = {'forecastStartTime': '2026-09-05T22:00+08:00',
                    'daytime': {'forecastStartTime': '2026-09-06T07:00+08:00'}, 'temperatureMax': {'value': 31}}
        data = view({'daily': {'data': {'days': [today, tomorrow]}}}, '2026-09-05')
        self.assertEqual(data['max'], 26)
        self.assertTrue(data['rain'])

    def test_rain_probability_trigger_and_missing_values(self):
        cache = {'hourly': {'data': {'hours': [{'forecastTime': '2026-09-05T14:00+08:00', 'precipitation': {'probability': .3}}]}}}
        self.assertTrue(view(cache, '2026-09-05')['rain'])
        self.assertFalse(view(cache, '2026-09-06')['rain'])
        self.assertIsNone(view({}, '2026-09-05')['current']['temp'])


class MicrosoftTests(WorkingDirectory):
    def setUp(self):
        super().setUp()
        self.service = Microsoft('client', self.store, Mock())
        self.task = {'id': 'task', 'title': 'private title', 'status': 'notStarted', 'lastModifiedDateTime': 'v1'}
        self.store.write('todo.json', {'lists': [{'id': 'list', 'displayName': 'List'}], 'tasks': {'list': [self.task]}})

    def test_queue_deduplicates_and_survives_reopen(self):
        self.service.enqueue('list', self.task)
        self.service.enqueue('list', self.task)
        self.assertEqual(len(Store(self.path).read('outbox.json')), 1)
        self.assertNotIn('private title', (self.path/'outbox.json').read_text())

    def test_completion_verifies_then_patches(self):
        self.service.enqueue('list', self.task)
        self.service.graph = Mock(side_effect=[dict(self.task, **{'@odata.etag': 'etag'}), {}])
        self.service.flush()
        self.assertEqual(self.store.read('outbox.json'), [])
        self.assertEqual(self.store.read('todo.json')['tasks']['list'][0]['status'], 'completed')
        call = self.service.graph.call_args_list[1]
        self.assertEqual(call.args[0], 'PATCH')
        self.assertEqual(call.kwargs['headers'], {'If-Match': 'etag'})

    def test_already_completed_never_patches(self):
        self.service.enqueue('list', self.task)
        self.service.graph = Mock(return_value=dict(self.task, status='completed'))
        self.service.flush()
        self.assertEqual(self.service.graph.call_count, 1)

    def test_remote_change_conflicts_without_patch(self):
        self.service.enqueue('list', self.task)
        self.service.graph = Mock(return_value=dict(self.task, lastModifiedDateTime='v2'))
        self.service.flush()
        self.assertEqual(self.service.graph.call_count, 1)
        self.assertEqual(self.store.read('outbox.json')[0]['state'], 'conflict')

    def test_missing_task_conflicts(self):
        self.service.enqueue('list', self.task)
        self.service.graph = Mock(side_effect=ServiceError('missing', 404))
        self.service.flush()
        self.assertEqual(self.store.read('outbox.json')[0]['state'], 'conflict')

    def test_network_failure_preserves_pending(self):
        self.service.enqueue('list', self.task)
        self.service.graph = Mock(side_effect=ServiceError('offline'))
        with self.assertRaises(ServiceError):
            self.service.flush()
        self.assertEqual(self.store.read('outbox.json')[0]['state'], 'pending')

    def test_partial_list_failure_preserves_cached_tasks(self):
        self.service.pages = Mock(side_effect=[[{'id': 'list', 'displayName': 'List'}, {'id': 'new', 'displayName': 'New'}], ServiceError('offline'), []])
        with self.assertRaises(ServiceError):
            self.service.sync()
        self.assertEqual(self.store.read('todo.json')['tasks']['list'], [self.task])
        self.assertEqual(self.store.read('todo.json')['tasks']['new'], [])

    def test_pagination(self):
        self.service.graph = Mock(side_effect=[{'value': [1], '@odata.nextLink': 'https://graph.microsoft.com/v1.0/next'}, {'value': [2]}])
        self.assertEqual(self.service.pages('/me/todo/lists'), [1, 2])

    def test_pagination_cycle(self):
        self.service.graph = Mock(return_value={'value': [], '@odata.nextLink': '/me/todo/lists'})
        with self.assertRaises(ServiceError):
            self.service.pages('/me/todo/lists')

    def test_pagination_cannot_exfiltrate_token(self):
        self.service.token = Mock(return_value='private')
        with self.assertRaises(ServiceError):
            self.service.graph('GET', 'https://evil.example/v1.0/tasks')
        self.service.token.assert_not_called()

    def test_refresh_retains_refresh_token_and_client_binding(self):
        self.store.write('token.json', {'client_id': 'client', 'refresh_token': 'refresh', 'expires_at': 0})
        self.service.http.request.return_value = {'access_token': 'access', 'expires_in': 3600}
        self.assertEqual(self.service.token(), 'access')
        self.assertEqual(self.store.read('token.json')['refresh_token'], 'refresh')
        other = Microsoft('different', self.store, Mock())
        with self.assertRaises(ServiceError):
            other.token()

    def test_401_refresh_preserves_if_match(self):
        self.service.token = Mock(side_effect=['old', 'new'])
        self.service.http.request.side_effect = [ServiceError('expired', 401), {}]
        self.service.graph('PATCH', '/me/todo/lists/list/tasks/task', headers={'If-Match': 'etag'}, json={'status': 'completed'})
        self.assertEqual(self.service.http.request.call_args.kwargs['headers']['If-Match'], 'etag')

    def test_device_poll_pending_and_slow_down(self):
        for state in ('authorization_pending', 'slow_down'):
            self.service.http.session.post.return_value = Mock(status_code=400, json=lambda: {'error': state})
            self.assertEqual(self.service.poll_login('device-code'), state)
        self.assertFalse((self.path/'token.json').exists())

    def test_deadline_is_calendar_date(self):
        self.assertEqual(due_date({'dueDateTime': {'dateTime': '2026-09-05T00:00:00', 'timeZone': 'Pacific Standard Time'}}), '2026-09-05')
        self.assertEqual(due_date({'dueDateTime': None}), '')
        task = dict(self.task, dueDateTime=None, importance='high')
        self.assertEqual(len(tasks_for({'tasks': {'list': [task]}}, '@important')), 1)
        self.assertEqual(tasks_for({'tasks': {'list': [task]}}, '@planned'), [])


class TimerInputTests(unittest.TestCase):
    def test_countdown_pauses_and_finishes_once(self):
        clock = Mock(return_value=10)
        timer = Timer(True, 5, clock)
        timer.toggle()
        clock.return_value = 12
        timer.toggle()
        clock.return_value = 100
        self.assertEqual(timer.seconds(), 3)
        timer.toggle()
        clock.return_value = 104
        self.assertTrue(timer.tick())
        self.assertFalse(timer.tick())
        self.assertEqual(timer.seconds(), 0)

    def test_stopwatch_and_countdown_are_independent(self):
        clock = Mock(return_value=0)
        first, second = Timer(True, 25, clock), Timer(False, 0, clock)
        first.toggle()
        clock.return_value = 5
        second.toggle()
        clock.return_value = 10
        self.assertEqual((first.seconds(), second.seconds()), (15, 5))

    def test_four_direction_roundtrips(self):
        for angle in (0, 90, 180, 270):
            width, height = dimensions(angle)
            for point in [(0, 0), (width-1, height-1), (width//2, height//2)]:
                self.assertEqual(native_to_logical(*logical_to_native(*point, angle), angle), point)

    def test_calibration_affine_and_degenerate(self):
        matrix = solve_affine([(10, 20), (110, 20), (10, 120)], [(0, 0), (200, 0), (0, 300)])
        self.assertAlmostEqual(matrix[0], 2)
        self.assertAlmostEqual(matrix[4], 3)
        with self.assertRaises(ValueError):
            solve_affine([(1, 1)]*3, [(1, 1)]*3)

    def test_touch_emits_only_after_release_frame(self):
        frame = TouchFrame()
        for event in [(3, 57, 5), (3, 53, 100), (3, 54, 200), (0, 0, 0), (3, 57, -1)]:
            self.assertIsNone(frame.feed(*event))
        self.assertEqual(frame.feed(0, 0, 0), (100, 200))
        self.assertIsNone(frame.feed(0, 0, 0))

    def test_dropped_touch_never_completes_task(self):
        frame = TouchFrame()
        for event in [(3, 57, 5), (3, 53, 100), (3, 54, 200), (0, 0, 0), (0, 3, 0), (0, 0, 0), (3, 57, -1)]:
            frame.feed(*event)
        self.assertIsNone(frame.feed(0, 0, 0))


class RecoveryTests(WorkingDirectory):
    def test_direct_takeover_without_supervisor_is_refused(self):
        from app.lifecycle import require_supervisor
        with patch('app.lifecycle.time.sleep'):
            with self.assertRaises(RuntimeError):
                require_supervisor(self.path)

    def test_restore_only_same_process_and_is_idempotent(self):
        process = {'pid': 123, 'start': '100', 'name': 'awesome'}
        other = {'pid': 456, 'start': '200', 'name': 'awesome'}
        self.store.write('journal.json', {'processes': [process, other], 'properties': [{'service': 'powerd', 'name': 'preventScreenSaver', 'value': 0}], 'fbink': '/usr/bin/fbink'})
        with patch('app.guardian.same', side_effect=lambda item: item == process), patch('app.guardian.os.kill') as kill, patch('app.guardian.run', return_value=True) as run:
            with patch('app.guardian.signal.SIGCONT', 18, create=True):
                self.assertTrue(guardian.restore(self.path))
                self.assertEqual(kill.call_count, 1)
                self.assertEqual(kill.call_args.args[0], 123)
                self.assertTrue(guardian.restore(self.path))
                self.assertEqual(run.call_count, 1)

    def test_failed_recovery_journal_remains_retryable(self):
        self.store.write('journal.json', {'properties': [{'service': 'powerd', 'name': 'x', 'value': 0}]})
        with patch('app.guardian.run', return_value=False):
            self.assertFalse(guardian.restore(self.path))
        self.assertFalse(self.store.read('journal.json').get('restored', False))


class ControllerRenderTests(WorkingDirectory):
    def setUp(self):
        super().setUp()
        self.app = make_demo(ROOT/'todoclock', self.path)
        self.app.config.update(todo_minutes=0, weather_minutes=0)
        self.app.status['wifi_on'] = False
        self.app.device.data['wifi_on'] = False
        self.app.last_status = float('inf')

    def tearDown(self):
        self.app.close()
        self.app.worker.shutdown(wait=True)
        super().tearDown()

    def test_all_pages_all_rotations_have_valid_hits(self):
        path = font_path(ROOT/'todoclock', self.app.config['font'], desktop=True)
        for angle in (0, 90, 180, 270):
            self.app.config['rotation'] = angle
            for page in ('todo', 'calendar', 'weather', 'timer', 'settings'):
                with self.subTest(angle=angle, page=page):
                    self.app.page = page
                    image, hits = render(self.app, path)
                    self.assertEqual(image.mode, 'L')
                    self.assertEqual(image.size, dimensions(angle))
                    for (left, top, right, bottom), action in hits:
                        self.assertTrue(0 <= left < right <= image.width)
                        self.assertTrue(0 <= top < bottom <= image.height)
                        self.assertIsNotNone(hit_test(hits, (left+right)/2, (top+bottom)/2))

    def test_rotation_timeout_and_persistence(self):
        original = self.app.config['rotation']
        self.app.action(('rotate',))
        self.app.save_preferences()
        self.assertEqual(self.store.read('preferences.json')['rotation'], original)
        self.app.rotation_pending = (original, -1)
        self.app.tick()
        self.assertEqual(self.app.config['rotation'], original)

    def test_offline_completion_and_restart(self):
        self.app.action(('complete', 0))
        self.app.tick()
        self.assertEqual(len(self.store.read('outbox.json')), 1)
        self.assertEqual(self.store.read('outbox.json')[0]['state'], 'pending')

    def test_leap_calendar_and_month_navigation(self):
        self.app.year, self.app.month = 2024, 2
        self.app.page = 'calendar'
        path = font_path(ROOT/'todoclock', self.app.config['font'], desktop=True)
        _, hits = render(self.app, path)
        self.assertIn(('date', '2024-02-29'), [action for _, action in hits])
        self.app.year, self.app.month = 2026, 12
        self.app.move_month(1)
        self.assertEqual((self.app.year, self.app.month), (2027, 1))

    def test_countdown_alert_on_another_page(self):
        self.app.page = 'weather'
        self.app.countdown.reset(.01)
        self.app.countdown.started = self.app.countdown.clock()-1
        self.app.tick()
        self.assertEqual(self.app.modal[0], '计时完成')

    def test_modal_prevents_click_through(self):
        self.app.modal = ('详情', '正文', [])
        path = font_path(ROOT/'todoclock', self.app.config['font'], desktop=True)
        _, hits = render(self.app, path)
        self.assertIsNone(hit_test(hits, 50, 200))
        self.assertFalse(any(action[0] == 'complete' for _, action in hits))

    def test_repeated_next_does_not_trap_pagination(self):
        for _ in range(20):
            self.app.action(('weather_page', 1))
        old = self.app.weather_page
        self.app.action(('weather_page', -1))
        self.assertEqual(self.app.weather_page, old-1)

    def test_login_poll_uses_server_interval(self):
        self.app.login = {'device_code': 'demo', 'interval': 5, 'deadline': 1000, 'next_poll': 100}
        future = Future()
        future.set_result('authorization_pending')
        self.app.jobs['login_poll'] = future
        with patch('app.controller.time.monotonic', return_value=100):
            self.app.tick()
        self.assertEqual(self.app.login['next_poll'], 105)
        self.assertEqual(self.app.next_due['login_poll'], 100)

    def test_timer_restart_is_paused_with_notice(self):
        self.app.countdown.toggle()
        self.app.save_timers()
        reopened = Controller(ROOT/'todoclock', SimDevice(), self.store, demo=True)
        try:
            self.assertIsNone(reopened.countdown.started)
            self.assertIn('暂停', reopened.notice)
        finally:
            reopened.close()

    def test_successful_online_flush_invalidates_display(self):
        self.app.action(('complete', 0))
        count = len(self.app.tasks)
        revision = self.app.revision
        self.app.device.set_wifi(True)
        self.app.microsoft.flush()
        future = Future()
        future.set_result(None)
        self.app.jobs['flush'] = future
        self.app.tick()
        self.assertEqual(len(self.app.tasks), count-1)
        self.assertGreater(self.app.revision, revision)

    def test_orphan_conflict_can_be_cleared(self):
        self.store.write('outbox.json', [{'list_id': 'deleted', 'task_id': 'missing', 'state': 'conflict', 'error': 'missing'}])
        self.app.refresh_cache()
        self.app.action(('outbox',))
        self.assertEqual(self.app.modal[0], '同步队列')
        self.app.action(('clear_conflicts',))
        self.assertEqual(self.store.read('outbox.json'), [])


class PackageTests(unittest.TestCase):
    def test_release_allowlist_excludes_all_private_state(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('package', ROOT/'tools/package.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        names = [name for _, name in module.release_files()]
        self.assertFalse(any('/state/' in name for name in names))
        self.assertNotIn('todoclock/config/secrets.json', names)
        self.assertNotIn('todoclock/config/local.json', names)
        self.assertIn('todoclock/config/secrets.example.json', names)
        self.assertTrue(all(source.is_file() for source, _ in module.release_files()))


if __name__ == '__main__':
    unittest.main()
