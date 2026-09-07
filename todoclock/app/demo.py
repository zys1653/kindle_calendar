"""Isolated, deterministic-enough sample providers. Never sends network traffic."""
from datetime import datetime, timedelta
import time
from .microsoft import Microsoft
from .network import ServiceError
from .weather import QWeather, CHINA


def seed(store):
    if store.read('todo.json', None) is not None:
        return
    today = datetime.now(CHINA).date()
    tasks = []
    titles = ['整理本周项目计划', '阅读二十分钟', '给植物浇水', '为下周准备采购清单', '检查电子台历的横屏显示与触摸操作', '散步，暂时离开屏幕', '整理书架', '学习一个新的 Python 小技巧', '给家人打电话']
    for i, title in enumerate(titles):
        tasks.append({'id': str(i), 'title': title, 'status': 'notStarted',
                      'importance': 'high' if i % 2 == 0 else 'normal',
                      'lastModifiedDateTime': '2026-09-05T00:00:00Z',
                      'dueDateTime': {'dateTime': (today+timedelta(days=i%4)).isoformat()+'T00:00:00', 'timeZone': 'China Standard Time'},
                      'body': {'content': '这是电脑仿真任务。可以点击复选框，测试离线排队和恢复联网。\n所有操作仅保存在临时演示目录。'}})
    store.write('todo.json', {'lists': [{'id': 'personal', 'displayName': '生活'}, {'id': 'work', 'displayName': '工作'}],
                              'tasks': {'personal': tasks[:5], 'work': tasks[5:]}, 'synced': time.time()})


class DemoMicrosoft(Microsoft):
    def __init__(self, store, device):
        self.store, self.device = store, device
        seed(store)

    def online(self):
        if not self.device.data['wifi_on']:
            raise ServiceError('演示：网络已断开')

    def sync(self):
        self.online()
        return self.store.update('todo.json', {}, lambda cache: cache.update(synced=time.time()))

    def flush(self):
        self.online()
        for item in self.store.read('outbox.json', []):
            if item['state'] != 'pending':
                continue
            def mark(cache):
                for task in cache['tasks'].get(item['list_id'], []):
                    if task['id'] == item['task_id']:
                        task['status'] = 'completed'
            self.store.update('todo.json', {}, mark)
        self.store.write('outbox.json', [])

    def begin_login(self):
        return {'verification_uri': '演示模式，无需真实登录', 'user_code': 'DEMO-1234',
                'device_code': 'demo', 'expires_in': 900, 'interval': 5}

    def begin_mail_login(self):
        return self.begin_login()

    def poll_login(self, _, mail=False):
        return 'success'


class DemoWeather(QWeather):
    def __init__(self, store, device):
        self.store, self.device = store, device

    def sync(self, place):
        if not self.device.data['wifi_on']:
            raise ServiceError('演示：天气网络已断开')
        now = datetime.now(CHINA)
        def condition(hour):
            rain = 3 <= hour <= 8
            return {'forecastTime': (now+timedelta(hours=hour)).isoformat(),
                    'condition': {'text': '小雨' if rain else '多云', 'code': '305' if rain else '101'},
                    'temperature': {'value': 24-hour%5, 'unit': '°C'},
                    'feelsLike': {'value': 25, 'unit': '°C'}, 'humidity': 0.65,
                    'wind': {'speed': {'value': 2.5, 'unit': 'm/s'}},
                    'precipitation': {'type': 'rain' if rain else 'none', 'probability': 0.75 if rain else 0.1}}
        metadata = {'attributions': ['https://developer.qweather.com/attribution.html']}
        current = condition(0)
        current['metadata'] = metadata
        day = {'forecastStartTime': now.replace(hour=0, minute=0).isoformat(),
               'temperatureMax': {'value': 27}, 'temperatureMin': {'value': 19},
               'daytime': condition(5), 'nighttime': condition(10)}
        cache = {part: {'data': data, 'fetched': time.time()} for part, data in (
            ('current', current), ('daily', {'days': [day], 'metadata': metadata}),
            ('hourly', {'hours': [condition(i) for i in range(24)], 'metadata': metadata}))}
        self.store.update('weather.json', {}, lambda data: data.update({self.cache_key(place): cache}))
        return cache

from .mail import Mail, FOLDERS
from urllib.parse import urlsplit, parse_qs


class DemoMail(Mail):
    def __init__(self, microsoft, store):
        # A separate fake Graph transport keeps all simulator mailbox calls offline.
        class Transport:
            client_id = 'demo-mail'
            validate_graph_url = staticmethod(Microsoft.validate_graph_url)

            def graph(transport, method, path, **kwargs):
                microsoft.online()
                if method == 'PATCH':
                    return {}
                query = parse_qs(urlsplit(path).query)
                if '/mailFolders/' in path:
                    folder = path.split('/mailFolders/')[1].split('/')[0]
                    if folder == 'inbox':
                        folder = 'focused' if "'focused'" in query.get('$filter', [''])[0] else 'other'
                    start = int(query.get('offset', ['0'])[0])
                    items = [sample(folder, i) for i in range(start, min(42, start+30))]
                    result = {'value': items}
                    if start+30 < 42:
                        result['@odata.nextLink'] = 'https://graph.microsoft.com/v1.0/me/mailFolders/'+folder+'/messages?offset=30'
                    return result
                mid = path.split('/messages/')[-1]
                folder, index = mid.rsplit('-', 1)
                result = sample(folder, int(index))
                result['body'] = {'contentType': 'text', 'content': ('你好，\n\n这是邮箱功能的隔离演示正文。此内容不是你的真实邮件。\n可以使用实体键翻页，也可以手动标为已读。\n\n' * 20)}
                return result

        def sample(folder, index):
            return {'id': folder+'-'+str(index), 'subject': ['周末阅读计划与书单分享', '项目进展：本周事项汇总', '一封很长很长的中文邮件主题，用于验证墨水屏列表截断和留白效果'][index%3],
                    'from': {'emailAddress': {'name': '阅读伙伴', 'address': 'sample@example.com'}},
                    'toRecipients': [{'emailAddress': {'name': '我', 'address': 'demo@hotmail.com'}}],
                    'receivedDateTime': (datetime.now(CHINA)-timedelta(hours=index)).isoformat(),
                    'isRead': bool(index%3), 'bodyPreview': '你好，这是两行邮件内容摘要。保持清晰、安静的阅读体验，稍后可以点开查看完整文字。',
                    'hasAttachments': index%4 == 0, 'inferenceClassification': folder if folder in ('focused', 'other') else 'other'}
        super().__init__(Transport(), store)
        if not store.read('token.json', {}):
            store.write('token.json', {'client_id': 'demo-mail', 'account_id': 'demo', 'scope': 'Tasks.ReadWrite Mail.ReadWrite User.Read offline_access', 'account_label': 'demo@hotmail.com'})
        if not self.cached('focused').get('synced'):
            self.sync()
