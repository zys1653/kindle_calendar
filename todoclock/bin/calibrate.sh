#!/bin/sh
APP=/mnt/us/extensions/todoclock
PYTHON=$(command -v python3) || PYTHON=/mnt/us/python3/bin/python3
[ -x "$PYTHON" ] || exit 1
exec "$PYTHON" "$APP/run.py" calibrate
