"""Explicit 0.1.6 visual acceptance and unchanged-header comparison."""
from pathlib import Path
import subprocess
import sys
import types
from unittest.mock import patch
from PIL import ImageChops

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'todoclock'))
from app.simulator import make_demo
from app.render import render, font_path
from app.scratch import scratch
from app.network import HTTP


def export():
    baseline = types.ModuleType('app.release_015_render')
    baseline.__package__ = 'app'
    source = subprocess.check_output(['git', 'show', '47b903d:todoclock/app/render.py'], cwd=ROOT).decode('utf-8')
    exec(compile(source, '<0.1.5-render>', 'exec'), baseline.__dict__)
    output = ROOT/'preview'/'0.1.6'
    output.mkdir(parents=True, exist_ok=True)
    with scratch(ROOT/'.scratch') as state, patch.object(HTTP, 'request', side_effect=AssertionError('Real HTTP prohibited')):
        app = make_demo(ROOT/'todoclock', state)
        path = font_path(app.root, app.config['font'], desktop=True)
        try:
            app.page = 'todo'
            for rotation in (0, 90, 180, 270):
                app.config['rotation'] = rotation
                old, new = baseline.render(app, path)[0], render(app, path)[0]
                # Includes the complete clock and weather, up to their right divider.
                right = 797 if new.width > new.height else 652
                assert ImageChops.difference(old.crop((0,0,right,157)), new.crop((0,0,right,157))).getbbox() is None
            for rotation in (0, 90):
                app.config['rotation'] = rotation
                app.status.update(battery=100, cover=100, cover_present=True, charging=True, cover_charging=True)
                app.mail_summary = app.mail.summary()
                app.mail_ready = True
                app.errors.clear()
                app.refresh_cache()
                for page in (0, 1):
                    app.page, app.settings_page = 'settings', page
                    render(app,path)[0].save(output/('settings-'+str(page+1)+'-'+str(rotation)+'.png'))
                app.mail_summary = {'synced': app.mail_synced, 'error': 'partial'}
                render(app,path)[0].save(output/('partial-'+str(rotation)+'.png'))
                app.mail_ready, app.mail_synced = False, 0
                render(app,path)[0].save(output/('unauthorized-'+str(rotation)+'.png'))
                app.store.write('outbox.json', [{'list_id': 'personal', 'task_id': '0', 'state': 'pending'}, {'list_id': 'personal', 'task_id': '1', 'state': 'conflict', 'error': '任务已在其他端改变'}])
                app.store.write(app.mail.filename('queue'), [{'id': 'focused-0', 'state': 'pending'}, {'id': 'focused-1', 'state': 'failed', 'error': '邮件不存在或无权限'}])
                app.refresh_cache()
                render(app,path)[0].save(output/('mixed-'+str(rotation)+'.png'))
                app.action(('outbox',))
                render(app,path)[0].save(output/('queue-'+str(rotation)+'.png'))
                app.action(('close',))
                app.store.write('outbox.json', [])
                app.store.write(app.mail.filename('queue'), [])
                app.refresh_cache()
        finally:
            app.close()
            app.worker.shutdown(wait=True)
            app.status_worker.shutdown(wait=True)
    print('PASS: unchanged clock/weather pixels in all 4 rotations; exported 12 0.1.6 fixtures:', output)


if __name__ == '__main__':
    export()
