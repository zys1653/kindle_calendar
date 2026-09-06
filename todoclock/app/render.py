"""Pure grayscale renderer. Returns a Pillow image and action hit rectangles."""
import calendar
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from .device import dimensions
from .microsoft import due_date
from .timers import format_seconds
from .weather import CHINA
from .icons import draw_icon, weather_kind
from .layout import HOURS_PER_PAGE, hour_page
from .statusbar import body_battery, sync_ready

PAGES = [('todo', '待办'), ('calendar', '日历'), ('weather', '天气'), ('timer', '计时'), ('settings', '设置')]


def font_path(root, configured, desktop=False):
    path = Path(configured)
    if not path.is_absolute():
        path = Path(root) / path
    if path.is_file():
        return str(path)
    if desktop:
        for candidate in ('C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simhei.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
            if Path(candidate).is_file():
                return candidate
    raise RuntimeError('请将支持中文的字体放入 assets/fonts/regular.ttf')


@lru_cache(maxsize=40)
def font(path, size):
    return ImageFont.truetype(path, size)


def number(value, suffix=''):
    if value is None:
        return '—'
    return ('{:g}'.format(round(float(value), 1)) if isinstance(value, (int, float)) else str(value)) + suffix


def timestamp(value):
    return datetime.fromtimestamp(value, CHINA).strftime('%m-%d %H:%M') if value else '尚未同步'


def wrap(text, fnt, width):
    lines = []
    for paragraph in str(text).splitlines() or ['']:
        current = ''
        for char in paragraph:
            if fnt.getlength(current + char) > width and current:
                lines.append(current)
                current = ''
            current += char
        lines.append(current)
    return lines


@lru_cache(maxsize=2)
def modal_lines(text, path, width):
    return wrap(text, font(path, 26), width)


class Canvas:
    def __init__(self, size, path):
        self.image = Image.new('L', size, 255)
        self.draw = ImageDraw.Draw(self.image)
        self.path, self.hits = path, []

    def text(self, xy, value, size=30, fill=0):
        self.draw.text(xy, str(value), font=font(self.path, size), fill=fill)

    def lines(self, xy, text, width, size=28, limit=None):
        lines = wrap(text, font(self.path, size), width)
        if limit and len(lines) > limit:
            lines = lines[:limit]
            lines[-1] = lines[-1][:-1] + '…'
        for i, line in enumerate(lines):
            self.text((xy[0], xy[1] + i * (size + 10)), line, size)
        return len(lines) * (size + 10)

    def button(self, box, label, action, selected=False, size=27):
        self.draw.rectangle(box, fill=0 if selected else 255, outline=0, width=2)
        if label in ('−', '-', '+'):
            x, y = (box[0]+box[2])/2, (box[1]+box[3])/2
            self.draw.line((x-13, y, x+13, y), fill=255 if selected else 0, width=4)
            if label == '+':
                self.draw.line((x, y-13, x, y+13), fill=255 if selected else 0, width=4)
        elif label in ('‹', '›'):
            x, y = (box[0]+box[2])/2, (box[1]+box[3])/2
            sign = 1 if label == '›' else -1
            self.draw.line([(x-sign*7, y-12), (x+sign*7, y), (x-sign*7, y+12)],
                           fill=255 if selected else 0, width=4)
        else:
            fnt = font(self.path, size)
            width = fnt.getlength(label)
            self.text((box[0] + max(10, (box[2]-box[0]-width)/2), box[1] + (box[3]-box[1]-size)/2-4), label, size, 255 if selected else 0)
        if action:
            self.hits.append((box, action))


def render(app, path):
    w, h = dimensions(app.config['rotation'])
    c = Canvas((w, h), path)
    content_x, right = 192, w - 28
    width = right - content_x
    c.text((24, -4), app.now.strftime('%H:%M'), 120)
    date = app.now.strftime('%Y.%m.%d') + '  周' + '一二三四五六日'[app.now.weekday()]
    current = app.weather_view['current']
    battery = body_battery(app.status)
    if app.status.get('cover_present'):
        battery += ' / 封皮 ' + number(app.status.get('cover'), '%') + (' 充电' if app.status.get('cover_charging') else '')
    elif app.status.get('cover_present') is None:
        battery += ' / 封皮 未知'
    landscape = w > h
    wx, sx = (385, 815) if landscape else (355, 670)
    weather_width, available = sx-wx-24, w-sx-28
    c.lines((wx+8, 9), app.place['name'], weather_width-16, 25, 1)
    draw_icon(c.image, (wx, 44), weather_kind(current['code']), 76)
    temperature = number(current['temp'], current['temp_unit'])
    temp_size = 64 if landscape else 52
    while font(path, temp_size).getlength(temperature) > weather_width-86 and temp_size > 28:
        temp_size -= 2
    c.text((wx+88, 38), temperature, temp_size)
    c.lines((wx+8, 119), current['text'], weather_width-16, 25, 1)
    c.draw.line((sx-18, 15, sx-18, 143), fill=175, width=1)
    c.text((sx, 8), date, 26 if landscape else 23)
    c.lines((sx, 42), battery, available, 22 if landscape else 19, 1)
    c.lines((sx, 73), 'Wi-Fi ' + app.status.get('wifi', '未知'), available, 20, 1)
    ready = sync_ready(app)
    c.lines((sx, 101), '待办 ' + timestamp(app.todo.get('synced', 0)), available-52, 18, 1)
    c.lines((sx, 127), '天气 ' + timestamp(app.weather_synced), available-52, 18, 1)
    # Draw status marks directly so a missing font glyph cannot hide the result.
    ix, iy = w-49, 124
    c.draw.ellipse((ix-18, iy-18, ix+18, iy+18), outline=0, width=2)
    if ready:
        c.draw.line(((ix-10, iy), (ix-3, iy+7), (ix+10, iy-8)), fill=0, width=4)
    else:
        c.draw.line((ix, iy-10, ix, iy+3), fill=0, width=4)
        c.draw.ellipse((ix-2, iy+9, ix+2, iy+13), fill=0)
    c.draw.line((24, 157, w-24, 157), fill=0, width=3)
    for i, (key, label) in enumerate(PAGES):
        y = 195 + i * 122
        c.button((24, y, 151, y+88), label, ('page', key), app.page == key, 31)
    c.button((24, h-155, 163, h-103), 'QWeather', ('weather_info',), size=22)
    c.button((24, h-83, 163, h-28), '全刷', ('full_refresh',), size=26)
    c.lines((content_x, h-55), app.notice + ('  · 同步中' if app.jobs else ''), width, 20, 1)
    if app.page == 'todo':
        c.lines((content_x, 181), app.views[app.list_index][1], width-380, 38, 1)
        c.button((right-360, 180, right-270, 239), '‹', ('list', -1))
        c.button((right-255, 180, right-165, 239), '›', ('list', 1))
        c.button((right-150, 180, right, 239), '同步', ('todo_sync',))
        capacity = max(1, (h-400)//112)
        pages = max(1, (len(app.tasks)+capacity-1)//capacity)
        page = min(app.task_page, pages-1)
        if not app.tasks:
            c.text((content_x+35, 330), '此列表暂无未完成任务', 33)
            c.text((content_x+35, 390), '首次使用请在设置中登录微软账户。', 25)
        for row, (list_id, task) in enumerate(app.tasks[page*capacity:(page+1)*capacity]):
            index, y = page*capacity+row, 275+row*112
            pending = next((q for q in app.outbox if q['list_id'] == list_id and q['task_id'] == task['id']), None)
            c.button((content_x, y+10, content_x+58, y+68), '!' if pending and pending['state'] == 'conflict' else '·' if pending else '', ('detail', list_id, task['id']) if pending else ('complete', list_id, task['id']), size=35)
            c.lines((content_x+85, y), task.get('title', ''), width-105, 29, 2)
            label = pending.get('error', '待同步') if pending else due_date(task)
            c.lines((content_x+85, y+77), label, width-110, 19, 1)
            c.hits.append(((content_x+75, y, right, y+105), ('detail', list_id, task['id'])))
            c.draw.line((content_x+80, y+107, right, y+107), fill=180)
        c.button((content_x, h-135, content_x+150, h-75), '上一页', ('task_page', -1))
        c.text((content_x+180, h-123), '{}/{}'.format(page+1, pages), 26)
        c.button((right-150, h-135, right, h-75), '下一页', ('task_page', 1))
    elif app.page == 'calendar':
        c.text((content_x, 185), '{} 年 {} 月'.format(app.year, app.month), 38)
        c.button((right-330, 180, right-245, 240), '‹', ('month', -1))
        c.button((right-230, 180, right-110, 240), '本月', ('today',))
        c.button((right-95, 180, right, 240), '›', ('month', 1))
        cell_w = width / 7
        cell_h = (h-380) / 6
        marked = {due_date(t) for ts in app.todo.get('tasks', {}).values() for t in ts if t.get('status') != 'completed'}
        for col, weekday in enumerate('一二三四五六日'):
            c.text((content_x+col*cell_w+20, 268), weekday, 26)
        weeks = calendar.Calendar(0).monthdayscalendar(app.year, app.month)
        for row, week in enumerate(weeks):
            for col, day in enumerate(week):
                if not day:
                    continue
                x, y = content_x+col*cell_w, 320+row*cell_h
                iso = '{:04d}-{:02d}-{:02d}'.format(app.year, app.month, day)
                selected = iso == app.now.date().isoformat()
                box = (int(x+4), int(y+4), int(x+cell_w-8), int(y+cell_h-8))
                if selected:
                    c.draw.rectangle(box, fill=0)
                c.text((x+20, y+12), day, 36, 255 if selected else 0)
                if iso in marked:
                    c.draw.line((x+20, y+68, x+70, y+68), fill=255 if selected else 0, width=4)
                c.hits.append((box, ('date', iso)))
    elif app.page == 'weather':
        c.text((content_x, 178), app.place['name'], 38)
        c.button((right-325, 180, right-170, 240), '逐小时', ('hours',))
        c.button((right-155, 180, right, 240), '更新', ('weather_sync',))
        draw_icon(c.image, (content_x+12, 270), weather_kind(current['code']), 160)
        temperature = number(current['temp'], current['temp_unit'])
        temp_size = 128
        while font(path, temp_size).getlength(temperature) > width-220 and temp_size > 70:
            temp_size -= 2
        c.text((content_x+210, 243), temperature, temp_size)
        c.lines((content_x+215, 395), current['text'], width-225, 40, 1)
        c.draw.line((content_x, 464, right, 464), fill=0, width=2)
        metrics = [
            ('temperature', '最低 / 最高', '{}° / {}°'.format(number(app.weather_view['min']), number(app.weather_view['max']))),
            ('temperature', '体感温度', number(current['feels'], '°')),
            ('humidity', '湿度', number(current['humidity'], '%')),
            ('wind', '风速', number(current['wind'], ' '+current['wind_unit']))]
        metric_w = width/4
        for i, (symbol, label, value) in enumerate(metrics):
            x = content_x+i*metric_w
            draw_icon(c.image, (x+4, 482), symbol, 38)
            c.text((x+49, 484), label, 24 if landscape else 21)
            c.lines((x+8, 529), value, metric_w-16, 34 if landscape else 30, 1)
        expanded = app.show_hours if app.show_hours is not None else app.weather_view['rain']
        if expanded:
            hours = app.weather_view['hours']
            page, pages = hour_page(app.weather_page, len(hours))
            columns, rows = (4, 1) if landscape else (2, 2)
            gap, top, bottom = 16, 608, h-172
            cw, ch = (width-gap*(columns-1))/columns, (bottom-top-gap*(rows-1))/rows
            for i, item in enumerate(hours[page*HOURS_PER_PAGE:(page+1)*HOURS_PER_PAGE]):
                x = content_x+(i % columns)*(cw+gap)
                y = top+(i // columns)*(ch+gap)
                c.draw.rounded_rectangle((x, y, x+cw, y+ch), radius=10, outline=0, width=2)
                try:
                    dt = datetime.fromisoformat(item['time'].replace('Z', '+00:00')).astimezone(CHINA)
                    label = dt.strftime('%H:%M')
                    if dt.date() != app.now.date(): label = dt.strftime('%m/%d %H:%M')
                except (ValueError, TypeError):
                    label = '时间未知'
                c.text((x+18, y+12), label, 32)
                draw_icon(c.image, (x+18, y+62), weather_kind(item['code']), 78)
                c.lines((x+108, y+78), number(item['temp'], '°'), cw-118, 42, 1)
                c.lines((x+18, y+151), item['text'], cw-36, 32, 1)
                draw_icon(c.image, (x+16, y+ch-68), 'humidity', 32)
                c.text((x+57, y+ch-65), '降水 '+number(item['pop'], '%'), 28)
            if not hours:
                c.text((content_x+20, top+60), '暂无逐小时数据', 36)
            c.button((content_x, h-144, content_x+150, h-79), '上一页', ('weather_page', -1), size=28)
            c.text((content_x+180, h-132), '{}/{}'.format(page+1, pages), 28)
            c.button((right-150, h-144, right, h-79), '下一页', ('weather_page', 1), size=28)
        else:
            c.text((content_x+20, 660), '今日暂无明显降雨提示', 38)
            c.text((content_x+20, 723), '点击“逐小时”查看未来时段预报', 30)
    elif app.page == 'timer':
        c.button((content_x, 185, content_x+210, 250), '番茄钟', ('timer_tab', 'countdown'), app.timer_tab == 'countdown')
        c.button((content_x+230, 185, content_x+440, 250), '秒表', ('timer_tab', 'stopwatch'), app.timer_tab == 'stopwatch')
        timer = getattr(app, app.timer_tab)
        size = min(112, int(width/5.4))
        c.text((content_x+40, 340), format_seconds(timer.seconds()), size)
        c.text((content_x+45, 500), '运行中' if timer.started is not None else '已暂停', 30)
        c.button((content_x+40, 590, content_x+250, 680), '暂停' if timer.started is not None else '开始 / 继续', ('timer_toggle',))
        c.button((content_x+280, 590, content_x+470, 680), '重置', ('timer_reset',))
        if app.timer_tab == 'countdown':
            for i, (label, amount) in enumerate([('时', 3600), ('分', 60), ('秒', 1)]):
                x = content_x+40+i*220
                c.text((x+60, 730), label, 28)
                c.button((x, 785, x+80, 850), '−', ('duration', -amount))
                c.button((x+100, 785, x+180, 850), '+', ('duration', amount))
            c.text((content_x+40, 890), '暂停时可调整；切换页面后继续计时。', 24)
    elif app.page == 'settings':
        c.text((content_x, 180), '设置', 38)
        c.button((right-300, 180, right-235, 240), '‹', ('settings_page', max(0, app.settings_page-1)))
        c.button((right-220, 180, right-155, 240), '1', ('settings_page', 0), app.settings_page == 0)
        c.button((right-145, 180, right-80, 240), '2', ('settings_page', 1), app.settings_page == 1)
        c.button((right-65, 180, right, 240), '›', ('settings_page', min(1, app.settings_page+1)))
        def setting(row, label, buttons):
            y = 280+row*100
            c.text((content_x, y+10), label, 26)
            total = sum(b[2] for b in buttons) + (len(buttons)-1)*12
            x = right-total
            for text, action, bw in buttons:
                c.button((x, y, x+bw, y+65), text, action, size=24)
                x += bw+12
        if app.settings_page == 0:
            setting(0, 'Wi-Fi', [('开启' if app.status.get('wifi_on') else '关闭', ('wifi',), 150)])
            setting(1, '前光 '+number(app.status.get('light')), [('−', ('light', -1), 70), ('+', ('light', 1), 70)])
            setting(2, '天气地点', [(app.place['name'], ('location',), 230)])
            for row, key, label in [(3, 'todo_minutes', '待办同步'), (4, 'weather_minutes', '天气更新'), (5, 'full_refresh_minutes', '彻底刷新')]:
                setting(row, label, [(str(app.config[key])+' 分钟' if app.config[key] else '仅手动', ('frequency', key), 190)])
            setting(6, '屏幕方向', [('旋转 90°', ('rotate',), 190)])
        else:
            setting(0, '微软账户', [('登录', ('login',), 120), ('注销', ('logout',), 120)])
            setting(1, '和风配置', [('重新加载', ('reload',), 160), ('测试连接', ('weather_sync',), 160)])
            setting(2, '配置状态', [('演示配置' if app.demo else ('已填写' if app.secrets.get('qweather', {}).get('api_key') else '未填写 Key'), None, 230)])
            setting(3, '诊断与日志', [('诊断', ('diagnostics',), 120), ('日志', ('logs',), 120)])
            setting(4, '详细日志', [('开启 10 分钟', ('debug',), 230)])
            setting(5, '同步队列', [('查看待提交操作', ('outbox',), 230)])
            setting(6, '返回 Kindle', [('退出插件', ('exit',), 230)])
    if app.modal:
        # Modal captures all touch actions, preventing click-through.
        c.hits.clear()
        box = (180, 170, w-28, h-70)
        c.draw.rectangle(box, fill=255, outline=0, width=5)
        title, body, buttons = app.modal
        c.lines((210, 195), title, w-280, 34, 2)
        lines = modal_lines(body, path, w-300)
        capacity = max(1, (h-510)//36)
        pages = max(1, (len(lines)+capacity-1)//capacity)
        page = min(app.modal_page, pages-1)
        for i, line in enumerate(lines[page*capacity:(page+1)*capacity]):
            c.text((215, 295+i*36), line, 26)
        for i, (label, action) in enumerate(buttons):
            c.button((215+i*340, h-265, 525+i*340, h-200), label, action, size=24)
        c.button((215, h-160, 315, h-95), '‹', ('modal_page', -1))
        c.text((335, h-145), '{}/{}'.format(page+1, pages), 24)
        c.button((435, h-160, 535, h-95), '›', ('modal_page', 1))
        c.button((w-240, h-160, w-60, h-95), '关闭', ('close',))
    return c.image, c.hits


def hit_test(hits, x, y):
    for (left, top, right, bottom), action in reversed(hits):
        if left <= x <= right and top <= y <= bottom:
            return action
    return None
