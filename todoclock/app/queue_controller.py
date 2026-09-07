"""One UI for the two independently persisted operation queues."""
class QueueActions:
    def queue_snapshot(self):
        # A canceled last operation must not leave an unretryable flush error.
        for queue, job in ((self.outbox, 'flush'), (self.mail_queue, 'mail_flush')):
            if not queue and job not in self.jobs:
                self.errors.pop(job, None)
        self.queue_entries = [('todo', q) for q in self.outbox] + [('mail', q) for q in self.mail_queue]
        self.queue_pending = sum(q['state'] == 'pending' for _, q in self.queue_entries)
        self.queue_failed = len(self.queue_entries)-self.queue_pending
        self.queue_label = '等待同步 {}'.format(self.queue_pending)
        if self.queue_failed:
            self.queue_label += ' · 需处理 {}'.format(self.queue_failed)

    def show_queue(self, delta=0):
        self.queue_snapshot()
        self.queue_index = max(0, min(max(0, len(self.queue_entries)-1), getattr(self, 'queue_index', 0)+delta))
        text = self.queue_label + '\n\n'
        buttons = []
        if self.queue_entries:
            kind, item = self.queue_entries[self.queue_index]
            text += ('待办完成' if kind == 'todo' else '邮件已读') + '\n'
            text += '等待提交' if item['state'] == 'pending' else '需处理：' + item.get('error', '操作未完成')
            if kind == 'todo':
                task = next((t for t in self.todo.get('tasks', {}).get(item['list_id'], []) if t['id'] == item['task_id']), {})
                title = task.get('title', '待办内容不在缓存中')
                action = ('queue_cancel', kind, item['list_id'], item['task_id'])
            else:
                from .mail import FOLDERS
                title = '邮件内容不在缓存中'
                for folder, _ in FOLDERS:
                    match = next((m for m in self.mail.cached(folder)['items'] if m['id'] == item['id']), None)
                    if match:
                        title = match.get('subject') or '（无主题）'
                        break
                action = ('queue_cancel', kind, item['id'])
            text += '\n\n' + ' '.join(title.splitlines())[:160] + '\n\n左右键切换操作。取消不会撤销已经提交到云端的更改。'
            buttons.append(('取消此项', action))
        else:
            text += '没有等待同步或需要处理的操作。'
        if self.queue_failed:
            buttons.append(('清除失败操作', ('clear_conflicts',)))
        self.modal, self.modal_page = ('等待同步', text, buttons), 0

    def cancel_queue_item(self, args):
        if args[0] == 'todo':
            self.store.update('outbox.json', [], lambda q: q.__setitem__(slice(None), [x for x in q if (x['list_id'], x['task_id']) != tuple(args[1:])]))
        elif args[0] == 'mail':
            self.mail.cancel(args[1])
        self.refresh_cache()
        self.show_queue()
