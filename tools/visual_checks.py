"""Export release-specific visual cases using isolated fake data only."""
from pathlib import Path
import sys
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'todoclock'))
from app.scratch import scratch
from app.simulator import make_demo
from app.render import render, font_path
from app.icons import icon
from app.interaction import Feedback, context


def export():
    output = ROOT/'preview'
    output.mkdir(exist_ok=True)
    with scratch(ROOT/'.scratch') as state:
        app = make_demo(ROOT/'todoclock', state)
        try:
            path = font_path(ROOT/'todoclock', app.config['font'], desktop=True)
            app.page, app.show_hours = 'weather', True
            image, hits = render(app, path)
            box, _ = next(h for h in hits if h[1] == ('weather_page', 1))
            feedback = Feedback()
            feedback.event('press', (box[0]+20, box[1]+20), hits, context(app))
            feedback.paint(image).save(output/'weather-pressed.png')
            for rotation in (90, 0):
                app.config['rotation'] = rotation
                app.weather_view['current'].update(text='强雷阵雨伴有冰雹及强阵风', code='304', temp=-18.5)
                for hour in app.weather_view['hours']:
                    hour.update(text='强雷阵雨伴有冰雹及强阵风', code='future', pop=None, temp=-18.5)
                render(app, path)[0].save(output/('weather-long-{}.png'.format(rotation)))
        finally:
            app.close()
    kinds = ('sun', 'moon', 'partly', 'cloud', 'rain', 'thunder', 'snow', 'sleet', 'fog',
             'temperature', 'humidity', 'wind', 'unknown')
    gallery = Image.new('L', (700, 640), 255)
    draw = ImageDraw.Draw(gallery)
    for i, kind in enumerate(kinds):
        x, y = (i % 5)*140, (i//5)*210
        gallery.paste(icon(kind, 128), (x, y))
        draw.text((x+10, y+142), kind, fill=0)
    gallery.save(output/'icons.png')
    print('PASS: pressed, long/unknown weather in both orientations, icon gallery exported')


if __name__ == '__main__':
    export()
