import signal
import time
from .controller import Controller
from .input import Inputs
from .lifecycle import enter
from .render import font_path, hit_test, render
from .storage import Store
from .interaction import Feedback


def run(root, config, runtime):
    path = font_path(root, config['font'])
    device = enter(root, config, runtime)
    inputs = Inputs(Store(root / 'state').read('calibration.json'), grab=True)
    app = None
    stopping = [False]
    def stop(*_):
        stopping[0] = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        app = Controller(root, device)
        last_key, hits = None, []
        feedback = Feedback()
        while not stopping[0] and not app.exiting and not (runtime / 'stop').exists():
            (runtime / 'heartbeat').touch()
            for kind, data in inputs.poll(app.config['rotation']):
                if kind == 'exit':
                    stopping[0] = True
                elif kind == 'key':
                    feedback.clear()
                    app.key(data)
                    last_key = None
                else:
                    feedback.event(kind, data, hits, app.revision)
            app.tick()
            action = feedback.take(app.revision)
            if action:
                app.action(action)
            device.config = app.config
            # Re-render once per second only when needed; network/UI changes also invalidate.
            running = app.page == 'timer' and getattr(app, app.timer_tab).started is not None
            key = (app.now.strftime('%Y-%m-%d %H:%M'), int(time.monotonic()) if running else 0,
                   app.revision, tuple(app.jobs), feedback.region)
            if key != last_key or app.force_refresh or time.monotonic()-device.last_full >= app.config['full_refresh_minutes']*60:
                image, hits = render(app, path)
                device.show(feedback.paint(image), app.force_refresh)
                feedback.shown()
                app.force_refresh = False
                last_key = key
            time.sleep(0.05)
    finally:
        inputs.close()
        if app:
            app.close()
