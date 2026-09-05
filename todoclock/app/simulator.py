from datetime import timedelta
from pathlib import Path
from .controller import Controller
from .device import SimDevice
from .render import font_path, hit_test, render
from .storage import Store
from .scratch import scratch


def make_demo(root, state):
    device = SimDevice()
    app = Controller(root, device, Store(state), demo=True)
    app.weather.sync(app.place)
    app.refresh_cache()
    return app


def export(root, output):
    with scratch(Path(root).parent / '.scratch') as state:
        app = make_demo(root, state)
        path = font_path(root, app.config['font'], desktop=True)
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        try:
            for rotation in (90, 0, 180, 270):
                app.config['rotation'] = rotation
                for page in ('todo', 'calendar', 'weather', 'timer', 'settings'):
                    app.page = page
                    render(app, path)[0].save(output / '{}-{}.png'.format(page, rotation))
        finally:
            app.close()


def simulate(root):
    import tkinter as tk
    from PIL import ImageTk
    workspace = scratch(Path(root).parent / '.scratch')
    state_path = workspace.__enter__()
    app = make_demo(root, state_path)
    path = font_path(root, app.config['font'], desktop=True)
    window = tk.Tk()
    window.title('TodoClock 仿真 · 假数据 · 不联网')
    toolbar = tk.Frame(window)
    toolbar.pack(fill='x')
    screen = tk.Label(window)
    screen.pack()
    state = {'hits': [], 'scale': 0.65, 'image': None, 'last': None}
    def offline():
        app.device.set_wifi(not app.device.data['wifi_on'])
        app.status = app.device.status()
        app.next_due = {'weather': 0, 'todo': 0, 'flush': 0}
    def cover():
        app.device.data['cover_present'] = not app.device.data['cover_present']
        app.status = app.device.status()
    def battery():
        app.device.data.update(battery=8 if app.device.data['battery'] > 10 else 82,
                               charging=not app.device.data['charging'])
        app.status = app.device.status()
    def advance():
        app.time_shift += timedelta(days=1)
    def save():
        from tkinter.filedialog import asksaveasfilename
        filename = asksaveasfilename(defaultextension='.png', filetypes=[('PNG', '*.png')])
        if filename:
            render(app, path)[0].save(filename)
    for label, callback in [('断网 / 联网', offline), ('封皮插拔', cover), ('电量 / 充电', battery), ('日期 +1 天', advance), ('导出 PNG', save)]:
        tk.Button(toolbar, text=label, command=callback).pack(side='left')
    def click(event):
        action = hit_test(state['hits'], event.x/state['scale'], event.y/state['scale'])
        if action:
            app.action(action)
    screen.bind('<Button-1>', click)
    window.bind('<Left>', lambda _: app.key(-1))
    window.bind('<Right>', lambda _: app.key(1))
    def close():
        app.close()
        window.destroy()
    window.protocol('WM_DELETE_WINDOW', close)
    def update():
        if app.exiting:
            close()
            return
        app.tick()
        image, state['hits'] = render(app, path)
        state['scale'] = min(0.72, (window.winfo_screenheight()-160)/image.height, (window.winfo_screenwidth()-80)/image.width)
        size = tuple(int(v*state['scale']) for v in image.size)
        state['image'] = ImageTk.PhotoImage(image.resize(size))
        screen.configure(image=state['image'])
        window.after(200, update)
    update()
    window.mainloop()
    workspace.__exit__(None, None, None)
