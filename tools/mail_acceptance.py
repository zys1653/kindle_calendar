"""Explicit, opt-in 0.1.6 mailbox visual fixtures; never part of unit discovery."""
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'todoclock'))
from app.simulator import make_demo
from app.render import render, font_path
from app.scratch import scratch
from app.network import HTTP


def export():
    output = ROOT/'preview'/'0.1.6-mail'
    output.mkdir(parents=True, exist_ok=True)
    with scratch(ROOT/'.scratch') as state, patch.object(HTTP, 'request', side_effect=AssertionError('Real HTTP prohibited')):
        app = make_demo(ROOT/'todoclock', state)
        path = font_path(app.root, app.config['font'], desktop=True)
        try:
            for rotation in (0, 90):
                app.config['rotation'], app.page = rotation, 'mail'
                for entry in app.mail.queue():
                    app.mail.cancel(entry['id'])
                app.mail_snapshot()
                app.notice = ''
                app.mail_detail = None
                render(app, path)[0].save(output/('list-'+str(rotation)+'.png'))
                app.mail.body('focused-0', True)
                app.action(('mail_open', 'focused-0'))
                render(app, path)[0].save(output/('body-'+str(rotation)+'.png'))
                app.status['wifi_on'] = False
                app.action(('mail_read', 'focused-0'))
                render(app, path)[0].save(output/('pending-'+str(rotation)+'.png'))
                app.action(('mail_back',))
                app.mail_cache.update(error='加载失败，显示上次缓存')
                render(app, path)[0].save(output/('error-'+str(rotation)+'.png'))
                app.mail_cache.update(items=[], next=None)
                app.mail_cache.pop('error', None)
                app.notice = ''
                render(app, path)[0].save(output/('empty-'+str(rotation)+'.png'))
                app.mail_snapshot()
                app.page, app.settings_page = 'settings', 1
                render(app, path)[0].save(output/('settings-'+str(rotation)+'.png'))
                app.status['wifi_on'] = True
        finally:
            app.close()
            app.worker.shutdown(wait=True)
            app.status_worker.shutdown(wait=True)
    print('Exported 12 current-version mailbox fixtures to', output)


if __name__ == '__main__':
    export()
