import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from math_utils import add

if add(2, 3) != 5:
    raise SystemExit("add(2, 3) should be 5")
