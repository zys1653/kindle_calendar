import signal
import time
from .controller import Controller
from .input import Inputs
from .lifecycle import enter
from .render import font_path, hit_test, render
from .storage import Store


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
        while not stopping[0] and not app.exiting and not (runtime / 'stop').exists():
            (runtime / 'heartbeat').touch()
            for kind, data in inputs.poll(app.config['rotation']):
                if kind == 'exit':
                    stopping[0] = True
                elif kind == 'key':
                    app.key(data)
                    last_key = None
                else:
                    action = hit_test(hits, *data)
                    if action:
                        app.action(action)
                        last_key = None
            app.tick()
            device.config = app.config
            # Re-render once per second only when needed; network/UI changes also invalidate.
            running = app.page == 'timer' and getattr(app, app.timer_tab).started is not None
            key = (app.now.strftime('%Y-%m-%d %H:%M'), int(time.monotonic()) if running else 0,
                   app.revision, tuple(app.jobs))
            if key != last_key or app.force_refresh or time.monotonic()-device.last_full >= app.config['full_refresh_minutes']*60:
                image, hits = render(app, path)
                device.show(image, app.force_refresh)
                app.force_refresh = False
                last_key = key
            time.sleep(0.05)
    finally:
        inputs.close()
        if app:
            app.close()
