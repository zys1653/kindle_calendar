"""Opt-in 0.1.6 Tk settings/queue acceptance; no real network."""
from pathlib import Path
import sys
import tkinter as tk
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'todoclock'))
from app import simulator
from app.render import render, font_path
from app.network import HTTP

original_tk, original_demo = tk.Tk, simulator.make_demo
captured, failures = {}, []


def demo(root, state):
    app = original_demo(root, state)
    app.page = 'settings'
    app.config.update(mail_minutes=0, todo_minutes=0, weather_minutes=0)
    app.device.data['wifi_on'] = False
    app.status['wifi_on'] = False
    app.store.write('outbox.json', [{'list_id':'personal','task_id':'0','state':'pending'}, {'list_id':'personal','task_id':'1','state':'conflict'}])
    app.store.write(app.mail.filename('queue'), [{'id':'focused-0','state':'pending'}, {'id':'focused-1','state':'failed'}])
    app.refresh_cache()
    captured['app'] = app
    return app


def hidden():
    window = original_tk()
    window.attributes('-alpha', 0.0)
    def step(index=0):
        app = captured['app']
        screen = next(x for x in window.winfo_children() if isinstance(x, tk.Label))
        try:
            if index == 1:
                assert app.config['mail_minutes'] == 5
            if index == 2:
                assert app.settings_page == 1 and app.queue_label == '等待同步 2 · 需处理 2'
            if index == 3:
                assert app.modal[0] == '等待同步'
            if index == 4:
                assert app.queue_index == 1
            if index == 5:
                assert app.queue_failed == 1 and app.queue_pending == 2
            if index == 6:
                assert app.queue_failed == 0 and app.queue_pending == 2
            if index == 7:
                assert app.modal is None
            if index == 8:
                assert app.settings_page == 0
            actions = [('frequency','mail_minutes'), ('settings_page',1), ('outbox',), ('modal_page',1),
                       ('queue_cancel','todo','personal','1'), ('clear_conflicts',), ('close',), ('settings_page',0), ('exit',)]
            image,hits = render(app,font_path(app.root,app.config['font'],desktop=True))
            box = next(box for box, action in hits if action == actions[index])
            scale = min(.72,(window.winfo_screenheight()-160)/image.height,(window.winfo_screenwidth()-80)/image.width)
            x,y = int((box[0]+box[2])/2*scale),int((box[1]+box[3])/2*scale)
            screen.event_generate('<ButtonPress-1>',x=x,y=y)
            screen.event_generate('<ButtonRelease-1>',x=x,y=y)
            if index < len(actions)-1:
                window.after(250,lambda: step(index+1))
            else:
                assert app.exiting
        except Exception as exc:
            failures.append((index,type(exc).__name__))
            window.tk.call(window.protocol('WM_DELETE_WINDOW'))
    window.after(400,step)
    return window


with patch.object(tk,'Tk',hidden), patch.object(simulator,'make_demo',demo), patch.object(HTTP,'request',side_effect=AssertionError('Network prohibited')):
    simulator.simulate(ROOT/'todoclock')
assert not failures, failures
assert captured['app'].exiting
print('PASS: real Tk touches for settings pages, mail period, mixed queue paging/cancel/clear and first-page exit')
