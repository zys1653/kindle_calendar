import signal
import time
from .controller import Controller
from .input import Inputs
from .lifecycle import enter
from .render import font_path, render
from .storage import Store
from .interaction import Feedback, context
from .display import Display


def run(root, config, runtime):
    path = font_path(root, config['font'])
    device = enter(root, config, runtime)
    inputs = Inputs(Store(root / 'state').read('calibration.json'), grab=True)
    app = None
    display = Display(device)
    stopping = [False]
    def stop(*_):
        stopping[0] = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        app = Controller(root, device)
        last_key, hits = None, []
        feedback = Feedback()
        next_full = time.monotonic() + app.config['full_refresh_minutes']*60
        while not stopping[0] and not app.exiting and not (runtime / 'stop').exists():
            (runtime / 'heartbeat').touch()
            shown_context, shown_hits = display.snapshot()
            for kind, data in inputs.poll(app.config['rotation']):
                if kind == 'exit':
                    stopping[0] = True
                elif kind == 'key':
                    feedback.clear()
                    app.key(data)
                    last_key = None
                else:
                    if shown_context == context(app):
                        action = feedback.event(kind, data, shown_hits, shown_context)
                        if action:
                            app.action(action)
                    else:
                        feedback.clear()
            app.tick()
            feedback.expire(context(app))
            # Re-render once per second only when needed; network/UI changes also invalidate.
            running = app.page == 'timer' and getattr(app, app.timer_tab).started is not None
            key = (app.now.strftime('%Y-%m-%d %H:%M'), int(time.monotonic()) if running else 0,
                   app.revision, tuple(app.jobs), feedback.region)
            full_due = time.monotonic() >= next_full
            if key != last_key or app.force_refresh or full_due:
                image, hits = render(app, path)
                display.submit(feedback.paint(image), app.config, app.force_refresh or full_due, context(app), hits)
                if app.force_refresh or full_due:
                    next_full = time.monotonic() + app.config['full_refresh_minutes']*60
                app.force_refresh = False
                last_key = key
            time.sleep(0.05)
    finally:
        inputs.close()
        try:
            display.close()
        finally:
            if app:
                app.close()
