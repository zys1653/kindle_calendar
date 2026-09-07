"""Opt-in mailbox Tk interaction smoke, fake data and no network."""
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
    app.page = 'mail'
    app.config.update(mail_minutes=0, todo_minutes=0, weather_minutes=0)
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
                assert app.mail_folder == 'other'
            if index == 2:
                assert app.mail_page == 1
            if index == 3:
                assert app.mail_detail and app.mail_detail['id'] == 'other-6'
                app.status['wifi_on'] = False
                app.device.data['wifi_on'] = False
            if index == 4:
                assert app.mail_queue and not app.mail_detail['isRead']
            if index == 5:
                assert app.mail_detail is None and app.mail_page == 1
                app.device.data['wifi_on'] = True
                app.status['wifi_on'] = True
                window.after(500, lambda: step(6))
                return
            if index == 6:
                assert not app.mail_queue
                assert bool(screen.cget('image'))
                window.tk.call(window.protocol('WM_DELETE_WINDOW'))
                return
            actions = [('mail_folder', 'other'), ('mail_page', 1), ('mail_open', 'other-6'),
                       ('mail_read', 'other-6'), ('mail_back',)]
            image, hits = render(app, font_path(app.root, app.config['font'], desktop=True))
            box = next(box for box, action in hits if action == actions[index])
            scale = min(.72, (window.winfo_screenheight()-160)/image.height, (window.winfo_screenwidth()-80)/image.width)
            x, y = int((box[0]+box[2])/2*scale), int((box[1]+box[3])/2*scale)
            screen.event_generate('<ButtonPress-1>', x=x, y=y)
            screen.event_generate('<ButtonRelease-1>', x=x, y=y)
            window.after(300, lambda: step(index+1))
        except Exception as exc:
            failures.append((index, type(exc).__name__))
            window.tk.call(window.protocol('WM_DELETE_WINDOW'))
    window.after(400, step)
    return window


with patch.object(tk, 'Tk', hidden), patch.object(simulator, 'make_demo', demo), patch.object(HTTP, 'request', side_effect=AssertionError('Network prohibited')):
    simulator.simulate(ROOT/'todoclock')
assert not failures, failures
print('PASS: mailbox Tk touch folders, page, body, offline read queue, return position, online flush, clean exit')

