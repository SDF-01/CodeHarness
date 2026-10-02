import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from codeharness.config import HarnessConfig
from codeharness.errors import ModelError
from codeharness.model import OpenAICompatibleClient, completion_from_stream, parse_completion, tool_calls_from_text
import pytest


class _Server(ThreadingHTTPServer):
    def __init__(self, body: bytes, status: int = 200) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.body = body
        self.status = status
        self.last_auth = None


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.server.last_auth = self.headers.get("Authorization")
        payload = self.server.body
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:
        return


def test_client_parses_tool_calls_and_usage(tmp_path) -> None:
    payload = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "hello",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": "{\"path\": \"a.py\"}"},
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }
    ).encode("utf-8")
    server = _Server(payload)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = HarnessConfig(
            project_root=tmp_path,
            model="local-coder",
            api_key="test-key",
            base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
            request_timeout=5,
        )
        completion = OpenAICompatibleClient(config).complete([{"role": "user", "content": "hi"}], [])
    finally:
        server.shutdown()
        server.server_close()
    assert completion.content == "hello"
    assert completion.tool_calls[0].name == "read_file"
    assert completion.tool_calls[0].arguments == {"path": "a.py"}
    assert completion.prompt_tokens == 10
    assert server.last_auth == "Bearer test-key"


def test_client_reports_http_errors(tmp_path) -> None:
    server = _Server(b"nope", status=500)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = HarnessConfig(
            project_root=tmp_path,
            model="local-coder",
            base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
            request_timeout=5,
        )
        with pytest.raises(ModelError):
            OpenAICompatibleClient(config).complete([{"role": "user", "content": "hi"}], [])
    finally:
        server.shutdown()
        server.server_close()


def test_text_tool_call_is_treated_as_a_tool_call() -> None:
    completion = parse_completion(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"name": "read_file", "arguments": {"path": "math_utils.py"}}',
                    }
                }
            ],
            "usage": {"prompt_tokens": 155, "completion_tokens": 19},
        }
    )
    assert completion.content == ""
    assert completion.tool_calls[0].name == "read_file"
    assert completion.tool_calls[0].arguments == {"path": "math_utils.py"}


def test_stream_prints_prose_and_hides_tool_json() -> None:
    prose = "\n".join(
        [
            'data: {"choices":[{"delta":{"content":"The file is ready."}}]}',
            "data: [DONE]",
        ]
    )
    completion, visible = completion_from_stream(prose)
    assert completion.content == "The file is ready."
    assert visible == ["The file is ready."]
    tool_stream = "\n".join(
        [
            'data: {"choices":[{"delta":{"content":"{\\"name\\": \\"write_file\\""}}]}',
            "data: [DONE]",
        ]
    )
    _hidden, pieces = completion_from_stream(tool_stream)
    assert pieces == []


def test_windows_path_tool_call_still_runs() -> None:
    calls = tool_calls_from_text(
        r'{"name": "shell", "arguments": {"command": ".\.venv\Scripts\python.exe -m pytest -q"}}'
    )
    assert calls[0].name == "shell"
    assert "pytest" in calls[0].arguments["command"]
