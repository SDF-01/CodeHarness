"""Write the stack skeleton before the model edits it."""

from __future__ import annotations

from pathlib import Path

from codeharness.stack import STACK_PORT

_SERVER = f'''"""Local page and API. The harness wrote this skeleton."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = {STACK_PORT}
ROOT = Path(__file__).resolve().parent


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            body = json.dumps({{"ok": True}}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        rel = "index.html" if path in {{"", "/"}} else path.lstrip("/")
        target = (ROOT / rel).resolve()
        if target != ROOT and ROOT not in target.parents:
            self.send_error(404)
            return
        if not target.is_file():
            self.send_error(404)
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
'''

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>App</title>
</head>
<body>
  <p id="health">Loading</p>
  <script>
    fetch("/api/health").then(function (response) { return response.json(); }).then(function (data) {
      document.getElementById("health").textContent = JSON.stringify(data);
    });
  </script>
</body>
</html>
"""


def write_stack_skeleton(root: Path) -> str:
    """Create server.py and index.html when the folder has no server yet."""
    server = root / "server.py"
    if server.is_file():
        return ""
    root.mkdir(parents=True, exist_ok=True)
    server.write_text(_SERVER, encoding="utf-8", newline="\n")
    page = root / "index.html"
    if not page.is_file():
        page.write_text(_PAGE, encoding="utf-8", newline="\n")
    return "Wrote server.py and index.html."
