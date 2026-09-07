"""Bounded Graph mail operations and account-isolated atomic caches."""
import hashlib
import time
import uuid
from html.parser import HTMLParser
from urllib.parse import quote, urlencode
from .network import ServiceError

FOLDERS = [('focused', '重点'), ('other', '其他'), ('junkemail', '垃圾邮件'),
           ('sentitems', '已发送'), ('drafts', '草稿'), ('deleteditems', '已删除')]
FIELDS = 'id,subject,from,toRecipients,receivedDateTime,isRead,bodyPreview,hasAttachments,inferenceClassification'


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if not self.hidden and tag in ('p', 'div', 'br', 'li', 'tr', 'h1', 'h2'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden-1)
        elif not self.hidden and tag in ('p', 'div', 'li', 'tr'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(body):
    content = body.get('content', '')
    if body.get('contentType', '').lower() == 'html':
        parser = PlainText()
        parser.feed(content)
        return ''.join(parser.parts).strip()
    return content


class Mail:
    def __init__(self, microsoft, store):
        self.microsoft, self.store = microsoft, store

    def identity(self):
        token = self.store.read('token.json', {})
        if token.get('client_id') != self.microsoft.client_id:
            return ''
        return token.get('account_id', '')

    def ready(self):
        token = self.store.read('token.json', {})
        return bool(self.identity() and 'Mail.ReadWrite' in token.get('scope', '').split())

    def filename(self, part):
        identity = self.identity()
        if not identity:
            raise ServiceError('请在设置第三页授权邮箱', 401)
        return 'mail-' + hashlib.sha256(identity.encode()).hexdigest()[:24] + '-' + part + '.json'

    def read(self, part, default):
        return self.store.read(self.filename(part), default) if self.identity() else default

    def cached(self, folder):
        with self.store.lock:
            return self._cached(folder)

    def _cached(self, folder):
        cache = self.read(folder, {'items': [], 'next': None, 'synced': 0})
        if 'batches' in cache:
            cache['items'] = []
            for batch in cache['batches']:
                if not batch.startswith(self.filename(folder)[:-5] + '-batch-') or '/' in batch or '\\' in batch:
                    raise ServiceError('邮箱缓存索引无效')
                items = self.store.read(batch, None)
                if not isinstance(items, list):
                    raise ServiceError('邮箱缓存批次缺失，请重新同步')
                cache['items'].extend(items)
        return cache

    def save_cache(self, folder, cache):
        with self.store.lock:
            self._save_cache(folder, cache)

    def _save_cache(self, folder, cache):
        # Commit immutable batches first, then atomically publish their index.
        old = self.read(folder, {})
        snapshot = dict(cache)
        items = snapshot.pop('items', [])
        prefix = self.filename(folder)[:-5] + '-batch-'
        generation = uuid.uuid4().hex
        batches = []
        for offset in range(0, len(items), 30):
            name = prefix + generation + '-' + str(offset//30) + '.json'
            self.store.write(name, items[offset:offset+30])
            batches.append(name)
        snapshot['batches'] = batches
        self.store.write(self.filename(folder), snapshot)
        for name in old.get('batches', []):
            if name.startswith(prefix) and '/' not in name and '\\' not in name:
                try:
                    (self.store.root / name).unlink()
                except FileNotFoundError:
                    pass

    def queue(self):
        return self.read('queue', [])

    def require(self):
        if not self.ready():
            raise ServiceError('请在设置第三页授权邮箱', 401)

    def fetch(self, folder, more=False):
        self.require()
        old = self.cached(folder)
        if more and not old.get('next'):
            return old
        physical = 'inbox' if folder in ('focused', 'other') else folder
        params = {'$top': 30, '$select': FIELDS, '$orderby': 'receivedDateTime desc'}
        if folder in ('focused', 'other'):
            params['$filter'] = "receivedDateTime ge 1900-01-01T00:00:00Z and inferenceClassification eq '{}'".format(folder)
        path = old['next'] if more else '/me/mailFolders/' + physical + '/messages?' + urlencode(params)
        seen = old.get('seen', []) if more else []
        if path in seen:
            raise ServiceError('邮箱分页循环，请重新同步')
        try:
            data = self.microsoft.graph('GET', path, max_bytes=2 * 1024 * 1024)
            items = data.get('value')
            if not isinstance(items, list) or any(not isinstance(x, dict) or not x.get('id') for x in items):
                raise ServiceError('邮件列表格式错误')
            next_link = data.get('@odata.nextLink')
            if next_link:
                self.microsoft.validate_graph_url(next_link)
                if next_link in seen + [path]:
                    raise ServiceError('邮箱分页循环，请重新同步')
            merged = {x['id']: x for x in (old['items'] if more else [])}
            merged.update((x['id'], {k: x[k] for k in FIELDS.split(',') if k in x}) for x in items)
            result = {'items': sorted(merged.values(), key=lambda x: x.get('receivedDateTime', ''), reverse=True),
                      'next': next_link, 'seen': seen + [path], 'synced': time.time()}
            self.save_cache(folder, result)
            return result
        except ServiceError:
            old['error'] = '加载失败，显示上次缓存'
            self.save_cache(folder, old)
            raise

    def sync(self):
        self.require()
        failure = None
        for folder, _ in FOLDERS:
            try:
                self.fetch(folder)
            except ServiceError as exc:
                failure = exc
                if exc.status in (401, 403, 429):
                    break
        if failure:
            raise failure

    def body_file(self, message_id):
        return self.filename('body-' + hashlib.sha256(message_id.encode()).hexdigest())

    def body(self, message_id, fetch=False):
        with self.store.lock:
            index = self.read('bodies', {})
            if message_id in index:
                result = self.store.read(self.body_file(message_id), None)
                if result is not None:
                    if fetch:
                        index[message_id]['accessed'] = time.time()
                        self.store.write(self.filename('bodies'), index)
                    return result
        if not fetch:
            return None
        self.require()
        data = self.microsoft.graph('GET', '/me/messages/' + quote(message_id, safe=''),
                                    params={'$select': FIELDS + ',body'},
                                    headers={'Prefer': 'outlook.body-content-type="text"'},
                                    max_bytes=2 * 1024 * 1024)
        text = plain(data.get('body', {}))
        if len(text.encode('utf-8')) > 1024 * 1024:
            raise ServiceError('正文超过 1 MiB，未完整加载，请在 Outlook 阅读')
        if not data.get('id') or not isinstance(data.get('body'), dict):
            raise ServiceError('邮件正文格式错误')
        result = {k: data[k] for k in FIELDS.split(',') if k in data}
        result.update(text=text, accessed=time.time())
        with self.store.lock:
            index = self.read('bodies', {})
            index[message_id] = {'accessed': time.time()}
            keep = dict(sorted(index.items(), key=lambda x: x[1]['accessed'], reverse=True)[:100])
            self.store.write(self.body_file(message_id), result)
            self.store.write(self.filename('bodies'), keep)
            for removed in set(index)-set(keep):
                path = self.store.root / self.body_file(removed)
                if path.exists():
                    path.unlink()
        return result

    def touch_body(self, message_id):
        with self.store.lock:
            index = self.read('bodies', {})
            if message_id in index:
                index[message_id]['accessed'] = time.time()
                self.store.write(self.filename('bodies'), index)

    def enqueue(self, message_id):
        self.require()
        def add(queue):
            if not any(x['id'] == message_id for x in queue):
                queue.append({'id': message_id, 'state': 'pending'})
        self.store.update(self.filename('queue'), [], add)

    def cancel(self, message_id):
        self.store.update(self.filename('queue'), [], lambda q: q.__setitem__(slice(None), [x for x in q if x['id'] != message_id]))

    def flush(self):
        self.require()
        for item in self.queue():
            if item['state'] != 'pending':
                continue
            try:
                self.microsoft.graph('PATCH', '/me/messages/' + quote(item['id'], safe=''), json={'isRead': True})
                for folder, _ in FOLDERS:
                    cache = self.cached(folder)
                    for message in cache['items']:
                        if message['id'] == item['id']:
                            message['isRead'] = True
                    self.save_cache(folder, cache)
                body = self.body(item['id'])
                if body:
                    body['isRead'] = True
                    self.store.write(self.body_file(item['id']), body)
                self.cancel(item['id'])
            except ServiceError as exc:
                if exc.status in (403, 404, 409, 412):
                    def fail(queue):
                        for entry in queue:
                            if entry['id'] == item['id']:
                                entry.update(state='failed', error='邮件不存在或无法修改，请取消操作')
                    self.store.update(self.filename('queue'), [], fail)
                else:
                    raise

    def clear(self):
        if self.identity():
            # Includes abandoned batches from an interrupted atomic commit.
            prefix = self.filename('')[:-5]
            with self.store.lock:
                for path in self.store.root.glob(prefix + '*.json'):
                    path.unlink()
