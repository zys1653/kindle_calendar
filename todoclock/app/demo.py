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

    def poll_login(self, _):
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
