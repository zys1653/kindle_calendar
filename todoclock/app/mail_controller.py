"""Mailbox controller behavior; background I/O never enters renderers."""
import time
from .mail import FOLDERS
from .device import dimensions


class MailActions:
    def mail_init(self):
        self.mail_folder, self.mail_page = 'focused', 0
        self.mail_detail, self.mail_body_page = None, 0
        self.mail_body_pages = 1
        self.mail_request = None
        self.mail_generation = 0

    @property
    def mail_capacity(self):
        w, h = dimensions(self.config['rotation'])
        return 4 if w > h else 6

    def mail_snapshot(self):
        self.mail_ready = self.mail.ready()
        self.mail_summary = self.mail.summary()
        self.mail_synced = self.mail_summary['synced']
        self.mail_account = self.store.read('token.json', {}).get('account_label', '') if not self.demo else 'demo@hotmail.com'
        self.mail_cache = self.mail.cached(self.mail_folder)
        self.mail_queue = self.mail.queue()
        self.queue_snapshot()
        self.mail_page = min(self.mail_page, max(0, (len(self.mail_cache['items'])-1)//self.mail_capacity))
        if self.mail_detail:
            body = self.mail.body(self.mail_detail['id'])
            self.mail_detail = body or self.mail_detail
            overview = next((m for m in self.mail_cache['items'] if m['id'] == self.mail_detail['id']), {})
            self.mail_detail.update(overview)

    def sync_mail(self, manual=False):
        if not self.mail_ready:
            self.notice = '请在设置第二页登录微软账户'
            return
        if any(key in self.jobs for key in ('mail', 'mail_more')):
            return
        if manual and not self.errors.get('mail'):
            self.next_due['mail'] = 0
        if self.submit('mail', self.mail.sync):
            self.mail_generation += 1

    def mail_move(self, delta):
        if self.mail_detail:
            from .mail_render import body_layout
            from .render import font_path
            w, h = dimensions(self.config['rotation'])
            path = font_path(self.root, self.config['font'], desktop=self.demo)
            self.mail_body_pages = body_layout(self.mail_detail, path, w-220, h)[2]
            self.mail_body_page = max(0, min(self.mail_body_pages-1, self.mail_body_page+delta))
            return
        target = max(0, self.mail_page+delta)
        if target*self.mail_capacity < len(self.mail_cache['items']):
            self.mail_page = target
        elif delta > 0 and self.mail_cache.get('next'):
            if self.status.get('wifi_on') is False:
                self.notice = '离线：后续邮件尚未缓存'
                return
            if 'mail' in self.jobs:
                self.notice = '请等待邮箱同步完成'
                return
            if not self.mail_cache['items']:
                target = 0
            folder, generation = self.mail_folder, self.mail_generation
            def fetch():
                self.mail.fetch(folder, more=True)
                return folder, generation, target
            self.submit('mail_more', fetch)

    def mail_finished(self, name, result):
        if name == 'mail_more':
            folder, generation, target = result
            if folder == self.mail_folder and generation == self.mail_generation and not self.mail_detail:
                self.mail_page = target
        elif name == 'mail_body':
            if self.mail_detail and self.mail_detail['id'] == result['id']:
                self.mail_detail = result
        elif name == 'mail':
            self.mail_page = 0
            self.notice = '邮箱更新成功'

    def mail_action(self, name, args):
        if name == 'mail_folder':
            if args[0] not in dict(FOLDERS):
                return
            self.mail_folder, self.mail_page = args[0], 0
            self.mail_generation += 1
            self.mail_detail, self.mail_request = None, None
            self.mail_snapshot()
            if not self.mail_cache.get('synced') and self.mail_ready:
                self.sync_mail(True)
        elif name == 'mail_page':
            self.mail_move(args[0])
        elif name == 'mail_sync':
            self.sync_mail(True)
        elif name == 'mail_open':
            message = next((x for x in self.mail_cache['items'] if x['id'] == args[0]), None)
            if message is None:
                self.notice = '邮件列表已变化，请重新选择'
                return
            self.mail_request = None
            self.mail_detail = dict(message)
            self.mail_body_page = 0
            body = self.mail.body(args[0], fetch=False)
            if body:
                self.mail.touch_body(args[0])
                self.mail_detail = dict(body, **message)
            elif self.status.get('wifi_on') is False:
                self.notice = '离线：正文尚未缓存'
            else:
                self.mail_request = args[0]
        elif name == 'mail_back':
            self.mail_detail = None
            self.mail_request = None
        elif name == 'mail_read':
            if self.mail_detail and self.mail_detail['id'] == args[0]:
                self.mail.enqueue(args[0])
                self.next_due['mail_flush'] = 0
                self.notice = '已读待提交：联网成功后确认'
                self.mail_snapshot()
        elif name == 'mail_cancel':
            self.mail.cancel(args[0])
            self.mail_snapshot()
        elif name == 'mail_login':  # Older UI action compatibility.
            self._action('login', [])
        elif name == 'mail_queue':
            self.show_queue()

    def mail_tick(self, online):
        if not online or not self.mail_ready:
            return
        if self.config['mail_minutes'] and time.monotonic() >= self.next_due.get('mail', 0):
            self.sync_mail()
        if any(x['state'] == 'pending' for x in self.mail_queue):
            self.submit('mail_flush', self.mail.flush)
        if self.mail_request and 'mail_body' not in self.jobs:
            message_id = self.mail_request
            if self.submit('mail_body', lambda: self.mail.body(message_id, fetch=True)):
                self.mail_request = None
