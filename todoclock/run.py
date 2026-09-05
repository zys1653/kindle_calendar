#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description='TodoClock')
    parser.add_argument('mode', choices=['simulate', 'preview', 'start', 'stop', 'diagnose', 'calibrate', 'calibration', 'kindle', 'once'])
    parser.add_argument('--runtime', type=Path, default=Path('/tmp/todoclock-runtime'))
    parser.add_argument('--output', default=str(ROOT.parent / 'preview'))
    args = parser.parse_args()
    from app.storage import Store, configuration, setup_logging
    state = Store(ROOT / 'state')
    logger = setup_logging(state.root)
    try:
        if args.mode == 'stop':
            from app.lifecycle import stop
            stop()
            return
        config, _ = configuration(ROOT)
        if args.mode in ('simulate', 'preview'):
            from app.simulator import simulate, export
            simulate(ROOT) if args.mode == 'simulate' else export(ROOT, args.output)
        elif args.mode in ('start', 'calibrate'):
            from app.lifecycle import launch
            launch(ROOT, 'calibration' if args.mode == 'calibrate' else 'kindle')
        elif args.mode == 'diagnose':
            from app.diagnostics import diagnose
            result = diagnose(ROOT, config)
            state.write('diagnostics.json', result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.mode == 'calibration':
            from app.calibrate import calibrate
            calibrate(ROOT, config, args.runtime)
        elif args.mode == 'once':
            from app.device import Kindle
            from app.controller import Controller
            from app.render import render, font_path
            device = Kindle(config, args.runtime)
            device.probe()
            app = Controller(ROOT, device)
            try:
                device.show(render(app, font_path(ROOT, config['font']))[0], True)
            finally:
                app.close()
        else:
            from app.main import run
            run(ROOT, config, args.runtime)
    except Exception as exc:
        # Safe error type only: no traceback containing network URLs or credentials.
        logger.error('startup_failed %s', type(exc).__name__)
        if isinstance(exc, (RuntimeError, ValueError)):
            print(str(exc), file=sys.stderr)
            logger.error('startup_reason %s', str(exc))
        else:
            print('启动失败: '+type(exc).__name__, file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
