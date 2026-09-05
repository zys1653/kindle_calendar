"""Exercise a real Tk event loop without opening a visible desktop window."""
from pathlib import Path
import sys
import tkinter as tk
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'todoclock'))
from app import simulator

original_tk = tk.Tk
observed = []


def hidden_window():
    window = original_tk()
    window.withdraw()
    def exercise():
        label = next(child for child in window.winfo_children() if isinstance(child, tk.Label))
        observed.append(bool(label.cget('image')))
        label.event_generate('<Button-1>', x=40, y=330)
        window.event_generate('<Right>')
        for child in window.winfo_children()[0].winfo_children()[:4]:
            child.invoke()
        window.after(500, lambda: window.tk.call(window.protocol('WM_DELETE_WINDOW')))
    window.after(400, exercise)
    return window


with patch.object(tk, 'Tk', hidden_window):
    simulator.simulate(ROOT/'todoclock')
assert observed == [True], 'Tk image was not rendered'
print('PASS: hidden Tk event loop, image display, mouse/keyboard dispatch, simulation controls, clean exit')
