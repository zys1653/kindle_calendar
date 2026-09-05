"""Original monochrome line artwork; no fonts or bitmap dependencies.

Code mapping: https://dev.qweather.com/docs/api/weather/weather-conditions/
"""
import math
from PIL import Image, ImageDraw
from functools import lru_cache


def weather_kind(code):
    code = str(code)
    if code == '100': return 'sun'
    if code == '150': return 'moon'
    if code in ('101', '102', '103', '151', '152', '153'): return 'partly'
    if code == '104': return 'cloud'
    if code in ('302', '303', '304'): return 'thunder'
    if code in ('404', '405', '406', '456'): return 'sleet'
    if code in ('400', '401', '402', '403', '407', '408', '409', '410', '457', '499'): return 'snow'
    if code in tuple(str(i) for i in range(300, 319)) + ('350', '351', '399'): return 'rain'
    if code in ('500', '501', '502', '503', '504', '507', '508', '509', '510', '511', '512', '513', '514', '515'): return 'fog'
    if code in ('900', '901'): return 'temperature'
    return 'unknown'


@lru_cache(maxsize=96)
def icon(kind, size):
    im = Image.new('L', (128, 128), 255)
    d = ImageDraw.Draw(im)
    def line(points, width=5): d.line(points, fill=0, width=width, joint='curve')
    def circle(box): d.ellipse(box, outline=0, width=5)
    def sun(cx=64, cy=64, r=23):
        circle((cx-r, cy-r, cx+r, cy+r))
        for i in range(8):
            a = i*math.pi/4
            line([(cx+(r+10)*math.cos(a), cy+(r+10)*math.sin(a)),
                  (cx+(r+21)*math.cos(a), cy+(r+21)*math.sin(a))])
    def cloud():
        # Filled overlapping circles form one clean outline, then mask inner seams.
        d.ellipse((19, 48, 65, 94), fill=255, outline=0, width=5)
        d.ellipse((40, 27, 96, 88), fill=255, outline=0, width=5)
        d.ellipse((75, 50, 115, 94), fill=255, outline=0, width=5)
        d.rectangle((40, 57, 93, 90), fill=255)
        line([(41, 92), (95, 92)])
    if kind == 'sun': sun()
    elif kind == 'moon':
        angle = math.acos(13.5/45)
        outer = [(64+45*math.cos(a), 64+45*math.sin(a))
                 for a in (angle+(2*math.pi-2*angle)*i/80 for i in range(81))]
        inner = [(91+45*math.cos(a), 64+45*math.sin(a))
                 for a in (math.pi+angle-2*angle*i/80 for i in range(81))]
        line(outer+inner+[outer[0]])
    elif kind in ('cloud', 'partly', 'rain', 'snow', 'sleet', 'thunder', 'fog'):
        if kind == 'partly': sun(37, 35, 14)
        cloud()
        if kind in ('rain', 'sleet'):
            for x in (35, 61, 87): line([(x+5, 102), (x, 115)], 4)
        if kind in ('snow', 'sleet'):
            for x in ((45, 83) if kind == 'snow' else (106,)):
                for a in (0, math.pi/3, 2*math.pi/3):
                    line([(x-7*math.cos(a), 110-7*math.sin(a)), (x+7*math.cos(a), 110+7*math.sin(a))], 3)
        if kind == 'thunder': line([(70, 70), (54, 101), (73, 101), (62, 123)], 6)
        if kind == 'fog':
            line([(17, 104), (106, 104)], 4); line([(32, 117), (118, 117)], 4)
    elif kind == 'temperature':
        d.rounded_rectangle((51, 14, 77, 89), radius=13, outline=0, width=5)
        d.ellipse((41, 76, 87, 121), fill=255, outline=0, width=5)
        line([(64, 42), (64, 96)], 6)
        d.ellipse((55, 89, 73, 108), fill=0)
        for y in (28, 43, 58): line([(88, y), (101, y)], 4)
    elif kind == 'humidity':
        line([(64, 13), (31, 64), (25, 82), (30, 101), (43, 115), (64, 120),
              (85, 115), (98, 101), (103, 82), (97, 64), (64, 13)])
        d.arc((39, 67, 89, 107), 30, 130, fill=0, width=4)
    elif kind == 'wind':
        for y, end in ((39, 91), (64, 113), (89, 83)):
            line([(15, y), (end-14, y)])
            d.arc((end-28, y-26, end, y), 180, 450, fill=0, width=5)
    else:
        circle((24, 24, 104, 104))
        line([(64, 44), (64, 73)])
        d.ellipse((61, 84, 67, 90), fill=0)
    return im.resize((size, size), Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS)


def draw_icon(image, xy, kind, size):
    image.paste(icon(kind, size), tuple(map(int, xy)))
