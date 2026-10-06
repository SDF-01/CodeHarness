"""A full stack app is a page plus a local Python API."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

STACK_PORT = 8766


def expects_stack(task: str) -> bool:
    """True when this turn should finish with index.html and server.py."""
    lowered = task.lower()
    if "do not create a website" in lowered:
        return False
    if "full stack" in lowered or "fullstack" in lowered:
        return True
    return "server.py" in lowered and "index.html" in lowered


def serves_http(path: Path) -> bool:
    """True when a Python file is a server that should stay running."""
    if path.suffix.lower() != ".py" or not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    return "HTTPServer" in text or "http.server" in text


def listen_port(source: str) -> int:
    match = re.search(r"\bPORT\s*=\s*(\d{2,5})\b", source)
    if match is None:
        return STACK_PORT
    return int(match.group(1))


def stack_problem(root: Path, task: str) -> str:
    """Missing page, missing API, or a page that never calls the API."""
    if not expects_stack(task):
        return ""
    page = root / "index.html"
    server = root / "server.py"
    missing = [name for name, path in (("index.html", page), ("server.py", server)) if not path.is_file()]
    if missing:
        names = " and ".join(missing)
        return f"error: a full stack app needs {names}."
    page_text = page.read_text(encoding="utf-8", errors="replace")
    source = server.read_text(encoding="utf-8", errors="replace")
    problems: list[str] = []
    if "HTTPServer" not in source and "http.server" not in source:
        problems.append("error: server.py must serve HTTP with the Python standard library.")
    if "/api/" not in source:
        problems.append("error: server.py must answer a /api/ route.")
    if "/api/" not in page_text:
        problems.append("error: index.html must call the /api/ route.")
    return "\n".join(problems)


def probe_health(root: Path) -> str:
    """Start server.py, require JSON from GET /api/health, then stop it."""
    server = root / "server.py"
    if not server.is_file():
        return ""
    source = server.read_text(encoding="utf-8", errors="replace")
    port = listen_port(source)
    process = subprocess.Popen(
        [sys.executable, str(server)],
        cwd=root.resolve(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        deadline = time.monotonic() + 3
        last = "server.py did not answer"
        while time.monotonic() < deadline:
            if process.poll() is not None:
                err = (process.stderr.read() if process.stderr else "") or ""
                line = err.strip().splitlines()
                last = line[-1] if line else f"exit {process.returncode}"
                return f"error: server.py stopped. {last}"
            try:
                with urllib.request.urlopen(url, timeout=0.4) as response:
                    raw = response.read()
                    if response.status != 200:
                        return f"error: GET /api/health returned HTTP {response.status}."
                    json.loads(raw.decode("utf-8"))
                    return ""
            except urllib.error.HTTPError as exc:
                return f"error: GET /api/health returned HTTP {exc.code}."
            except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError) as exc:
                last = str(exc)
                time.sleep(0.1)
        return f"error: GET /api/health did not return JSON. {last}"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
