"""Local page that shows each harness step as it happens."""

from __future__ import annotations

import json
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from codeharness.commands import handle_turn
from codeharness.config import HarnessConfig
from codeharness.loop import LoopEvent
from codeharness.model import ChatModel, OpenAICompatibleClient
from codeharness.session import SessionStore, database_path

PAGE = (Path(__file__).resolve().parent / "ui.html").read_text(encoding="utf-8")
_TOKEN_LINE = re.compile(
    r"prompt=(\d+) completion=(\d+) tool_calls=(\d+) source=(\w+)"
)


class Board:
    """One session, one turn at a time, with a place for the page to answer permissions."""

    def __init__(self, config: HarnessConfig, model: ChatModel) -> None:
        self.config = config
        self.model = model
        self.store = SessionStore(database_path(config.project_root))
        self.session = self.store.create(config.project_root)
        self._lock = threading.Lock()
        self.events: list[dict] = []
        self.busy = False
        self.error = ""
        self.latest_prompt = 0
        self.completion_tokens = 0
        self.tool_calls = 0
        self.token_source = "api"
        self._pending: dict | None = None
        self._next_id = 1

    def close(self) -> None:
        self.store.close()

    def snapshot(self) -> dict:
        with self._lock:
            pending = None
            if self._pending is not None:
                pending = {"tool": self._pending["tool"], "detail": self._pending["detail"]}
            return {
                "model": self.config.model,
                "server": self.config.base_url,
                "root": str(self.config.project_root),
                "session_id": self.session.id,
                "agent": self.store.get_agent(self.session),
                "context_limit": self.config.context_limit,
                "response_reserve": self.config.response_reserve,
                "busy": self.busy,
                "error": self.error,
                "latest_prompt": self.latest_prompt,
                "completion_tokens": self.completion_tokens,
                "tool_calls": self.tool_calls,
                "token_source": self.token_source,
                "pending": pending,
                "events": list(self.events),
            }

    def start_turn(self, text: str) -> str | None:
        cleaned = text.strip()
        if not cleaned:
            return "Type a task first."
        with self._lock:
            if self.busy:
                return "A turn is already running."
            self.busy = True
            self.error = ""
            self.latest_prompt = 0
            self.completion_tokens = 0
            self.tool_calls = 0
        self._add("user", cleaned)
        threading.Thread(target=self._run, args=(cleaned,), daemon=True).start()
        return None

    def decide(self, allow: bool) -> str | None:
        with self._lock:
            pending = self._pending
            if pending is None:
                return "Nothing is waiting for approval."
            pending["allow"] = allow
            pending["ready"].set()
        return None

    def ask(self, tool_name: str, detail: str) -> bool:
        ready = threading.Event()
        with self._lock:
            self._pending = {"tool": tool_name, "detail": detail, "allow": False, "ready": ready}
        self._add("permission", f"Waiting for approval: {tool_name}" + (f" ({detail})" if detail else ""))
        ready.wait(timeout=600)
        with self._lock:
            pending = self._pending
            self._pending = None
            allow = bool(pending and pending["allow"] and ready.is_set())
        self._add("status", f"Allowed {tool_name}." if allow else f"Denied {tool_name}.")
        return allow

    def _run(self, text: str) -> None:
        try:
            handle_turn(
                self.store,
                self.session,
                text,
                self.model,
                self.config,
                self.ask,
                self._on_event,
            )
        except Exception as exc:
            self._add("error", str(exc))
            with self._lock:
                self.error = str(exc)
        finally:
            with self._lock:
                self.busy = False
                if self._pending is not None:
                    self._pending["ready"].set()
                    self._pending = None

    def _on_event(self, event: LoopEvent) -> None:
        if event.kind == "tokens" and event.text.startswith("tokens:"):
            self._note_tokens(event.text)
        self._add(event.kind, event.text)

    def _note_tokens(self, text: str) -> None:
        match = _TOKEN_LINE.search(text)
        if match is None:
            return
        with self._lock:
            self.latest_prompt = int(match.group(1))
            self.completion_tokens += int(match.group(2))
            self.tool_calls += int(match.group(3))
            self.token_source = match.group(4)

    def _add(self, kind: str, text: str) -> None:
        with self._lock:
            self.events.append({"id": self._next_id, "kind": kind, "text": text})
            self._next_id += 1


def serve_ui(config: HarnessConfig, model: ChatModel | None = None, port: int = 8765, open_browser: bool = True) -> None:
    client = model if model is not None else OpenAICompatibleClient(config)
    board = Board(config, client)
    handler = _handler_for(board)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Harness view: {url}")
    print("This page stays on this computer. Close the terminal to stop it.")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
        board.close()


def _handler_for(board: Board) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/api/state":
                body = json.dumps(board.snapshot()).encode("utf-8")
                self._send(200, body, "application/json")
                return
            self._send(404, b"not found", "text/plain; charset=utf-8")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            payload = self._read_json()
            if payload is None:
                return
            if path == "/api/turn":
                problem = board.start_turn(str(payload.get("text") or ""))
                self._send_result(problem)
                return
            if path == "/api/permission":
                problem = board.decide(bool(payload.get("allow")))
                self._send_result(problem)
                return
            self._send(404, b"not found", "text/plain; charset=utf-8")

        def log_message(self, fmt: str, *args) -> None:
            return

        def _read_json(self) -> dict | None:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 100_000:
                self._send(413, b"request is too large", "text/plain; charset=utf-8")
                return None
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send(400, b"send JSON", "text/plain; charset=utf-8")
                return None
            if not isinstance(payload, dict):
                self._send(400, b"send a JSON object", "text/plain; charset=utf-8")
                return None
            return payload

        def _send_result(self, problem: str | None) -> None:
            if problem:
                self._send(409, json.dumps({"error": problem}).encode("utf-8"), "application/json")
                return
            self._send(202, b"{}", "application/json")

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler
