import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from indexes import last_index

if last_index(["a", "b", "c"]) != 2:
    raise SystemExit("expected the last index")
