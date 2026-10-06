"""Score a stack app by asking GET /api/health. Missing files fail before a server starts."""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
server = ROOT / "server.py"
page = ROOT / "index.html"
if not server.is_file() or not page.is_file():
    raise SystemExit("server.py and index.html are required")

source = server.read_text(encoding="utf-8", errors="replace")
port = 8766
for line in source.splitlines():
    stripped = line.replace(" ", "")
    if stripped.startswith("PORT="):
        digits = "".join(ch for ch in stripped.split("=", 1)[1] if ch.isdigit())
        if digits:
            port = int(digits)
            break

process = subprocess.Popen(
    [sys.executable, str(server)],
    cwd=ROOT,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
url = f"http://127.0.0.1:{port}/api/health"
try:
    deadline = time.monotonic() + 3
    last = "server.py did not answer"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"server.py stopped with exit {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=0.4) as response:
                raw = response.read()
                if response.status != 200:
                    raise SystemExit(f"GET /api/health returned HTTP {response.status}")
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise SystemExit("GET /api/health did not return a JSON object")
                sys.exit(0)
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"GET /api/health returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError) as exc:
            last = str(exc)
            time.sleep(0.1)
    raise SystemExit(f"GET /api/health did not return JSON. {last}")
finally:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)
