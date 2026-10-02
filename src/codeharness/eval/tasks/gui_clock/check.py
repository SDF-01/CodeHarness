"""Score a clock file without opening a window."""

import py_compile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
clock = ROOT / "clock.py"
if not clock.is_file():
    raise SystemExit("clock.py was not written")
source = clock.read_text(encoding="utf-8")
if "tkinter" not in source or "mainloop" not in source:
    raise SystemExit("clock.py must use tkinter and call mainloop")
py_compile.compile(str(clock), doraise=True)
sys.exit(0)
