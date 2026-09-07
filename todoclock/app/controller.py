"""Application state and scheduling. Renderers consume this state, never mutate it."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import logging
from pathlib import Path
import time
from .microsoft import Microsoft, task_views, tasks_for
from .network import ServiceError
from .storage import Store, configuration
from .timers import Timer
from .layout import hour_page
from .weather import CHINA, QWeather, view


from .mail import Mail
from .mail_controller import MailActions


class Controller(MailActions):
    def __init__(self, root, device, store=None, demo=False):
        self.root, self.device, self.demo = Path(root), device, demo
        self.config, self.secrets = configuration(self.root)
        self.store = store or Store(self.root / 'state')
        self.logger = logging.getLogger('todoclock')
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='sync')
        self.status_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='device-status')
        self.status_future = None
        self.jobs = {}
        self.next_due = {'weather': 0, 'todo': 0, 'flush': 0, 'mail': 0}
        self.errors, self.failures = {}, {}
        self.page, self.list_index, self.task_page = 'todo', 0, 0
        self.weather_page, self.show_hours = 0, None
        self.settings_page, self.timer_tab = 0, 'countdown'
        self.now = datetime.now(CHINA)
        self.time_shift = timedelta()
        self.year, self.month = self.now.year, self.now.month
        saved = self.store.read('timers.json', {})
        self.duration = saved.get('duration', 1500)
        self.countdown = Timer(True, saved.get('countdown', {}).get('seconds', self.duration))
        self.stopwatch = Timer(False, saved.get('stopwatch', {}).get('seconds', 0))
        self.modal = None
        self.modal_page = 0
        self.notice = '计时已恢复为暂停' if any(saved.get(k, {}).get('was_running') for k in ('countdown', 'stopwatch')) else ''
        self.login = None
        self.rotation_pending = None
        self.exiting = False
        self.force_refresh = True
        self.revision = 0
        self.last_save = time.monotonic()
        self.last_status = 0
        self.last_power = 0
        self.debug_until = 0
        self.status = device.status()
        self.mail_init()
        self.build_services()
        self.refresh_cache()

    def build_services(self):
        if self.demo:
            from .demo import DemoMicrosoft, DemoWeather
            self.microsoft = DemoMicrosoft(self.store, self.device)
            self.weather = DemoWeather(self.store, self.device)
        else:
            self.microsoft = Microsoft(self.config['microsoft']['client_id'], self.store)
            self.weather = QWeather(self.secrets.get('qweather', {}), self.store)
        if self.demo:
            from .demo import DemoMail
            self.mail = DemoMail(self.microsoft, self.store)
        else:
            self.mail = Mail(self.microsoft, self.store)

    @property
    def place(self):
        return self.config['locations'][self.config['location']]

    def refresh_cache(self):
        self.todo = self.store.read('todo.json', {'lists': [], 'tasks': {}})
        self.outbox = self.store.read('outbox.json', [])
        weather_cache = self.weather.cached(self.place)
        self.weather_view = view(weather_cache, self.now.date().isoformat())
        self.weather_synced = min(weather_cache.get(part, {}).get('fetched', 0) for part in ('current', 'daily', 'hourly'))
        self.mail_snapshot()
        self.views = task_views(self.todo)
        self.list_index %= len(self.views)
        self.tasks = tasks_for(self.todo, self.views[self.list_index][0])
        self.revision += 1

    def save_preferences(self):
        keys = ('rotation', 'location', 'todo_minutes', 'weather_minutes', 'full_refresh_minutes', 'mail_minutes')
        preferences = {k: self.config[k] for k in keys}
        if self.rotation_pending:
            preferences['rotation'] = self.rotation_pending[0]
        self.store.write('preferences.json', preferences)

    def save_timers(self):
        self.store.write('timers.json', {'countdown': self.countdown.snapshot(),
                                        'stopwatch': self.stopwatch.snapshot(), 'duration': self.duration})
        self.last_save = time.monotonic()

    def submit(self, name, function):
        if name in self.jobs or time.monotonic() < self.next_due.get(name, 0):
            return False
        self.jobs[name] = self.worker.submit(function)
        return True

    def sync_weather(self, manual=False):
        if manual and not self.errors.get('weather'):
            self.next_due['weather'] = 0
        place = dict(self.place)
        self.submit('weather', lambda: self.weather.sync(place))

    def sync_todo(self, manual=False):
        if manual and not self.errors.get('todo'):
            self.next_due['todo'] = 0
        def sync():
            self.microsoft.flush()
            return self.microsoft.sync()
        self.submit('todo', sync)

    def tick(self):
        mono = time.monotonic()
        if mono - self.last_power >= 1:
            power = self.device.power_status()
            if any(self.status.get(k) != v for k, v in power.items()):
                self.status.update(power)
                self.revision += 1
            self.last_power = mono
        old_day = self.now.date()
        self.now = datetime.now(CHINA) + self.time_shift
        if self.now.date() != old_day:
            self.refresh_cache()
        if self.countdown.tick():
            self.modal = ('计时完成', '专注时间已结束。', [])
            self.modal_page = 0
            self.force_refresh = True
            self.save_timers()
            self.revision += 1
        if self.rotation_pending and mono >= self.rotation_pending[1]:
            self.config['rotation'] = self.rotation_pending[0]
            self.rotation_pending, self.modal = None, None
            self.force_refresh = True
            self.revision += 1
        if self.status_future is not None and self.status_future.done():
            try:
                status = self.status_future.result()
            except Exception:
                status = {'wifi': '未知', 'wifi_on': None, 'light': None}
            self.status_future = None
            # Never overwrite fresh power readings with an older background snapshot.
            status = {k: v for k, v in status.items() if k in ('wifi', 'wifi_on', 'light')}
            if any(self.status.get(k) != v for k, v in status.items()):
                self.status.update(status)
                self.revision += 1
        if mono - self.last_status >= 20 and self.status_future is None:
            self.status_future = self.status_worker.submit(self.device.status)
            self.last_status = mono
        if self.debug_until and mono >= self.debug_until:
            self.logger.setLevel(logging.INFO)
            self.debug_until = 0
        for name, future in list(self.jobs.items()):
            if not future.done():
                continue
            del self.jobs[name]
            try:
                result = future.result()
                self.errors.pop(name, None)
                self.failures[name] = 0
                if name == 'login_begin':
                    self.login = dict(result, deadline=mono + result['expires_in'], next_poll=mono + result.get('interval', 5))
                    self.modal = ('微软登录', '{}\n验证码：{}\n请在手机或电脑完成授权。'.format(result['verification_uri'], result['user_code']), [])
                elif name == 'login_poll':
                    if result == 'success':
                        self.login, self.modal = None, None
                        self.notice = '微软登录成功'
                        self.next_due['todo'] = 0
                    elif self.login:
                        if result == 'slow_down':
                            self.login['interval'] = self.login.get('interval', 5) + 5
                        self.login['next_poll'] = mono + self.login.get('interval', 5)
                elif name in ('weather', 'todo'):
                    self.notice = ('天气' if name == 'weather' else '待办') + '更新成功'
                self.mail_finished(name, result)
                self.next_due[name] = mono if name.startswith('login') else (mono if name in ('mail_more', 'mail_body', 'mail_flush') else mono + (self.config.get(name + '_minutes', 1) * 60 or 86400))
                self.logger.info('job_success %s', name)
            except Exception as exc:
                safe = exc.diagnostic() if isinstance(exc, ServiceError) else '内部错误，请查看诊断'
                self.errors[name] = safe
                self.notice = safe
                self.failures[name] = self.failures.get(name, 0) + 1
                retry = max(getattr(exc, 'retry_after', 60), min(1800, 30 * 2 ** min(6, self.failures[name])))
                self.next_due[name] = mono + retry
                self.logger.warning('job_failed %s kind=%s stage=%s status=%s', name,
                                    getattr(exc, 'kind', 'INTERNAL_ERROR'),
                                    getattr(exc, 'stage', 'request'), getattr(exc, 'status', 0))
                if name.startswith('login'):
                    self.login = None
                    self.modal = ('登录失败', safe, [])
            self.refresh_cache()
        online = self.status.get('wifi_on') is not False
        self.mail_tick(online)
        if online:
            for name in ('weather', 'todo'):
                if self.config[name + '_minutes'] and mono >= self.next_due[name]:
                    (self.sync_weather if name == 'weather' else self.sync_todo)()
            if any(q['state'] == 'pending' for q in self.outbox) and 'todo' not in self.jobs:
                self.submit('flush', self.microsoft.flush)
            if self.login and not self.exiting:
                if mono >= self.login['deadline']:
                    if not self.demo:
                        self.microsoft.cancel_login()
                    self.login = None
                    self.modal = ('登录过期', '请重新开始登录。', [])
                    self.revision += 1
                elif mono >= self.login['next_poll']:
                    code = self.login['device_code']
                    mail_login = self.mail_login
                    self.submit('login_poll', lambda: self.microsoft.poll_login(code, mail=True) if mail_login else self.microsoft.poll_login(code))
        if mono - self.last_save > 15 and (self.countdown.started is not None or self.stopwatch.started is not None):
            self.save_timers()

    def move_month(self, delta):
        total = self.year * 12 + self.month - 1 + delta
        self.year, month = divmod(total, 12)
        self.year = max(1900, min(9998, self.year))
        self.month = month + 1

    def key(self, delta):
        self.revision += 1
        if self.modal:
            self.modal_page = max(0, self.modal_page + delta)
        elif self.page == 'todo':
            self.list_index = (self.list_index + delta) % len(self.views)
            self.task_page = 0
            self.refresh_cache()
        elif self.page == 'calendar':
            self.move_month(delta)
        elif self.page == 'mail':
            self.mail_move(delta)
        elif self.page == 'settings':
            self.settings_page = max(0, min(2, self.settings_page + delta))
        elif self.page == 'weather' and (self.show_hours if self.show_hours is not None else self.weather_view['rain']):
            self.weather_page = hour_page(self.weather_page + delta, len(self.weather_view['hours']))[0]

    def action(self, action):
        self.revision += 1
        name, *args = action
        self.logger.debug('ui_action %s', name)
        try:
            self._action(name, args)
        except Exception as exc:
            self.notice = str(exc) if isinstance(exc, (ServiceError, ValueError, RuntimeError)) else '操作失败'
            self.logger.warning('action_failed %s %s', name, type(exc).__name__)

    def _action(self, name, args):
        if name.startswith('mail_'):
            self.mail_action(name, args)
        elif name == 'page':
            self.page, self.modal = args[0], None
        elif name == 'close':
            if self.login:
                if not self.demo:
                    self.microsoft.cancel_login()
                self.login = None
            self.modal = None
            self.modal_page = 0
        elif name == 'modal_page':
            self.modal_page = max(0, self.modal_page + args[0])
        elif name == 'list':
            self.key(args[0])
        elif name == 'task_page':
            from .device import dimensions
            capacity = max(1, (dimensions(self.config['rotation'])[1]-400)//112)
            pages = max(1, (len(self.tasks)+capacity-1)//capacity)
            self.task_page = max(0, min(pages-1, self.task_page + args[0]))
        elif name == 'todo_sync':
            self.sync_todo(True)
        elif name in ('complete', 'detail'):
            if len(args) == 1:  # Compatibility for internal callers; UI uses stable IDs.
                list_id, task = self.tasks[args[0]]
            else:
                match = next(((lid, t) for lid, t in self.tasks if (lid, t['id']) == tuple(args)), None)
                if match is None:
                    self.notice = '此任务已变化，请重新选择'
                    return
                list_id, task = match
            if name == 'complete':
                self.microsoft.enqueue(list_id, task)
                self.next_due['flush'] = 0
                self.notice = '待同步：联网后提交完成操作'
                self.refresh_cache()
            else:
                from html import unescape
                import re
                body = task.get('body', {}).get('content', '')
                body = unescape(re.sub('<[^>]+>', '', body))
                conflicts = [q for q in self.outbox if q['list_id'] == list_id and q['task_id'] == task['id'] and q['state'] == 'conflict']
                buttons = [('取消待提交操作', ('cancel_operation', list_id, task['id']))] if conflicts else []
                self.modal = (task.get('title', ''), body + ('\n' + conflicts[0]['error'] if conflicts else ''), buttons)
                self.modal_page = 0
        elif name == 'cancel_operation':
            self.store.update('outbox.json', [], lambda q: q.__setitem__(slice(None), [x for x in q if (x['list_id'], x['task_id']) != tuple(args)]))
            self.modal = None
            self.refresh_cache()
        elif name == 'month':
            self.move_month(args[0])
        elif name == 'today':
            self.year, self.month = self.now.year, self.now.month
        elif name == 'date':
            from .microsoft import due_date
            tasks = [t for ts in self.todo.get('tasks', {}).values() for t in ts if t.get('status') != 'completed' and due_date(t) == args[0]]
            self.modal = (args[0], '\n'.join('• ' + t['title'] for t in tasks) or '这一天没有待办。', [])
            self.modal_page = 0
        elif name == 'weather_sync':
            self.sync_weather(True)
        elif name == 'weather_info':
            from .render import timestamp
            cache = self.weather.cached(self.place)
            lines = ['数据来源：QWeather', 'https://www.qweather.com']
            for part, label in [('current', '实时'), ('daily', '每日'), ('hourly', '小时')]:
                lines.append('{}获取时间：{}'.format(label, timestamp(cache.get(part, {}).get('fetched', 0))))
                if part+'_error' in cache:
                    lines.append(label+'错误：'+cache[part+'_error'])
            lines.append('服务端更新时间：'+(self.weather_view['server_time'] or '此接口未提供；获取时间不代表观测时间'))
            lines.extend(self.weather_view['attributions'])
            self.modal, self.modal_page = ('天气数据与归因', '\n'.join(lines), []), 0
        elif name == 'hours':
            self.show_hours = not (self.show_hours if self.show_hours is not None else self.weather_view['rain'])
        elif name == 'weather_page':
            self.weather_page = hour_page(self.weather_page + args[0], len(self.weather_view['hours']))[0]
        elif name == 'location':
            keys = list(self.config['locations'])
            self.config['location'] = keys[(keys.index(self.config['location']) + 1) % len(keys)]
            self.show_hours, self.weather_page = None, 0
            self.save_preferences()
            self.refresh_cache()
            age = time.time() - self.weather_view['fetched']
            if age > (self.config['weather_minutes'] or 30) * 60:
                self.next_due['weather'] = 0
        elif name == 'timer_tab':
            self.timer_tab = args[0]
        elif name == 'timer_toggle':
            getattr(self, self.timer_tab).toggle()
            self.save_timers()
        elif name == 'timer_reset':
            getattr(self, self.timer_tab).reset(self.duration if self.timer_tab == 'countdown' else 0)
            self.save_timers()
        elif name == 'duration':
            if self.countdown.started is None:
                self.duration = max(1, min(359999, self.duration + args[0]))
                self.countdown.reset(self.duration)
                self.save_timers()
        elif name == 'wifi':
            self.device.set_wifi(not self.status.get('wifi_on', True))
            self.status = self.device.status()
        elif name == 'light':
            self.device.set_light((self.status.get('light') or 0) + args[0])
            self.status = self.device.status()
        elif name == 'frequency':
            key = args[0]
            values = (5, 15, 30, 60) if key == 'full_refresh_minutes' else ((0, 5, 15, 30, 60, 120) if key == 'mail_minutes' else (0, 15, 30, 60, 120))
            self.config[key] = values[(values.index(self.config[key]) + 1) % len(values)]
            self.save_preferences()
            self.next_due[key.replace('_minutes', '')] = 0
        elif name == 'settings_page':
            self.settings_page = max(0, min(2, int(args[0])))
        elif name == 'full_refresh':
            self.force_refresh = True
        elif name == 'rotate':
            self.rotation_pending = (self.config['rotation'], time.monotonic() + 15)
            self.config['rotation'] = (self.config['rotation'] + 90) % 360
            self.modal = ('确认方向', '15 秒内确认，否则自动恢复。', [('确认', ('rotation_confirm',))])
            self.force_refresh = True
        elif name == 'rotation_confirm':
            self.rotation_pending, self.modal = None, None
            self.save_preferences()
        elif name == 'reload':
            if self.jobs:
                self.notice = '请等待当前网络任务完成再重新加载'
                return
            new_config, new_secrets = configuration(self.root)
            new_config['rotation'] = self.config['rotation']
            self.config, self.secrets = new_config, new_secrets
            self.build_services()
            self.errors.clear()
            self.next_due = {'weather': 0, 'todo': 0, 'flush': 0}
            self.refresh_cache()
            self.notice = '配置已重新加载'
        elif name == 'login':
            self.mail_login = False
            if self.store.read('token.json', {}) or self.store.read('outbox.json', []):
                self.notice = '请先注销旧账户，避免混用账户数据'
            elif not self.login and 'login_begin' not in self.jobs and 'login_poll' not in self.jobs:
                self.submit('login_begin', self.microsoft.begin_login)
        elif name == 'outbox':
            text = '\n'.join('{}: {}'.format(i+1, q.get('error', '待同步')) for i, q in enumerate(self.outbox)) or '没有待提交操作'
            buttons = [('清除冲突操作', ('clear_conflicts',))] if any(q['state'] == 'conflict' for q in self.outbox) else []
            self.modal, self.modal_page = ('同步队列', text, buttons), 0
        elif name == 'clear_conflicts':
            self.store.update('outbox.json', [], lambda queue: queue.__setitem__(slice(None), [q for q in queue if q['state'] != 'conflict']))
            self.modal = None
            self.refresh_cache()
        elif name == 'logout':
            self.modal = ('注销微软账户', '将清除本机令牌、待办和邮箱缓存及全部未提交操作（包括已读队列）。请先联网提交需要保留的操作；不修改远端数据。', [('确认注销', ('logout_confirm',))])
        elif name == 'logout_confirm':
            if self.jobs:
                self.notice = '请等待当前网络任务结束后注销'
                return
            self.login = None
            self.mail.clear()
            self.mail_detail, self.mail_request = None, None
            for filename, empty in [('token.json', {}), ('todo.json', {'lists': [], 'tasks': {}}), ('outbox.json', [])]:
                self.store.write(filename, empty)
            self.modal = None
            self.refresh_cache()
        elif name in ('logs', 'diagnostics'):
            if name == 'diagnostics':
                from .diagnostics import diagnose
                report = diagnose(self.root, self.config, simulated=self.demo)
                self.store.write('diagnostics.json', report)
                import json
                text = json.dumps(report, ensure_ascii=False, indent=2)
            else:
                files = [self.store.root / ('app.log.' + str(i)) for i in range(4, 0, -1)] + [self.store.root / 'app.log']
                text = '\n'.join(p.read_text(encoding='utf-8', errors='replace') for p in files if p.exists()) or '暂无日志'
            self.modal, self.modal_page = (('诊断' if name == 'diagnostics' else '保留日志'), text, []), 0
        elif name == 'debug':
            self.logger.setLevel(logging.DEBUG)
            self.debug_until = time.monotonic() + 600
            self.notice = '详细日志已开启 10 分钟（不记录凭据或任务正文）'
        elif name == 'exit':
            self.exiting = True

    def close(self):
        self.exiting = True
        self.login = None
        if not self.demo:
            self.microsoft.cancel_login()
        self.save_timers()
        self.worker.shutdown(wait=False, cancel_futures=True)
        self.status_worker.shutdown(wait=False, cancel_futures=True)
