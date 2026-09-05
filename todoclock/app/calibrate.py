import math
import time
from PIL import Image, ImageDraw
from .input import Inputs, apply_calibration
from .lifecycle import enter
from .render import font, font_path
from .storage import Store


def solve_affine(raw, target):
    def solve(values):
        rows = [[float(raw[i][0]), float(raw[i][1]), 1., float(values[i])] for i in range(3)]
        for col in range(3):
            pivot = max(range(col, 3), key=lambda row: abs(rows[row][col]))
            rows[col], rows[pivot] = rows[pivot], rows[col]
            scale = rows[col][col]
            if abs(scale) < 1e-8:
                raise ValueError('校准点过于接近，请重新校准')
            rows[col] = [v/scale for v in rows[col]]
            for row in range(3):
                if row != col:
                    scale = rows[row][col]
                    rows[row] = [rows[row][j]-scale*rows[col][j] for j in range(4)]
        return [rows[i][3] for i in range(3)]
    return solve([p[0] for p in target]) + solve([p[1] for p in target])


def calibrate(root, config, runtime):
    path = font_path(root, config['font'])
    config = dict(config, rotation=0)
    device = enter(root, config, runtime, calibration=True)
    inputs = Inputs(grab=True)
    points = [(110, 180), (960, 180), (110, 1260), (535, 720)]
    raw, index = [], 0
    last_draw = -1
    start = time.monotonic()
    store = Store(runtime)
    try:
        while index < 4 and time.monotonic()-start < 120:
            (store.root / 'heartbeat').touch()
            if (store.root / 'stop').exists():
                return
            if last_draw != index:
                image = Image.new('L', (1072, 1448), 255)
                draw = ImageDraw.Draw(image)
                draw.text((55, 55), '请依次点击十字中心 {}/4'.format(index+1), font=font(path, 32), fill=0)
                x, y = points[index]
                draw.line((x-40, y, x+40, y), fill=0, width=4)
                draw.line((x, y-40, x, y+40), fill=0, width=4)
                draw.text((55, 1350), '电源键或双翻页键长按可退出；120 秒超时', font=font(path, 24), fill=0)
                device.show(image, True)
                last_draw = index
            for kind, value in inputs.poll(0, raw=True):
                if kind == 'exit':
                    return
                if kind == 'tap':
                    raw.append(value)
                    index += 1
                    break
            time.sleep(0.03)
        if index != 4:
            raise RuntimeError('校准超时，未保存')
        matrix = solve_affine(raw[:3], points[:3])
        record = {'matrix': matrix, 'device': inputs.devices['touch']['name']}
        measured = apply_calibration(*raw[3], record)
        if math.hypot(measured[0]-points[3][0], measured[1]-points[3][1]) > 60:
            raise RuntimeError('校准验证失败，请重试')
        Store(root / 'state').write('calibration.json', record)
    finally:
        inputs.close()
