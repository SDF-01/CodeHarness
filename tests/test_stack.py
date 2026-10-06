from codeharness.config import HarnessConfig
from codeharness.loop import run_turn
from codeharness.model import Completion, ToolCall
from codeharness.run import compile_and_launch
from codeharness.session import SessionStore, database_path
from codeharness.stack import expects_stack, stack_problem
from tests.fakes import ScriptedModel

_PAGE = "<html><body><script>fetch(\"/api/health\")</script></body></html>\n"
_SERVER = (
    "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
    "PORT = 8766\n"
    "class Handler(BaseHTTPRequestHandler):\n"
    "    def do_GET(self):\n"
    "        if self.path.startswith('/api/health'):\n"
    "            body = b'{\"ok\": true}'\n"
    "            self.send_response(200)\n"
    "            self.end_headers()\n"
    "            self.wfile.write(body)\n"
    "            return\n"
    "        self.send_error(404)\n"
    "if __name__ == '__main__':\n"
    "    HTTPServer(('127.0.0.1', PORT), Handler).serve_forever()\n"
)


def test_a_local_program_is_not_a_stack() -> None:
    assert not expects_stack("build an atm\n\nBuild a local Python or Java program. Do not create a website.")


def test_a_stack_needs_a_page_and_an_api(tmp_path) -> None:
    task = "Build a full stack app. Write index.html and server.py."
    assert "index.html" in stack_problem(tmp_path, task)
    (tmp_path / "index.html").write_text("<html><body>Hi</body></html>\n", encoding="utf-8")
    (tmp_path / "server.py").write_text(_SERVER, encoding="utf-8")
    assert "must call the /api/ route" in stack_problem(tmp_path, task)
    (tmp_path / "index.html").write_text(_PAGE, encoding="utf-8")
    assert stack_problem(tmp_path, task) == ""


def test_a_missing_api_is_sent_back(tmp_path) -> None:
    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[
                    ToolCall(id="w1", name="write_file", arguments={"path": "index.html", "content": _PAGE})
                ],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="done", tool_calls=[], prompt_tokens=1, completion_tokens=1),
            Completion(
                content="",
                tool_calls=[
                    ToolCall(id="w2", name="write_file", arguments={"path": "server.py", "content": _SERVER}),
                    ToolCall(id="w3", name="write_file", arguments={"path": "index.html", "content": _PAGE}),
                ],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="stack ready", tool_calls=[], prompt_tokens=1, completion_tokens=1),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    result = run_turn(
        store=store,
        session=session,
        user_text="Build a full stack app. Write index.html and server.py.",
        model=model,
        config=HarnessConfig(
            project_root=tmp_path,
            model="test",
            permissions={**HarnessConfig().permissions, "write_file": "allow"},
        ),
        ask=lambda name, detail: False,
    )
    store.close()
    assert result.text == "stack ready"
    assert any(message.content.startswith("Stack failed.") for message in session.messages)


def test_launch_opens_the_api(tmp_path, monkeypatch) -> None:
    (tmp_path / "index.html").write_text(_PAGE, encoding="utf-8")
    (tmp_path / "server.py").write_text(_SERVER, encoding="utf-8")
    opened: list[str] = []
    monkeypatch.setattr("codeharness.run.subprocess.Popen", lambda *args, **kwargs: object())
    monkeypatch.setattr("codeharness.run.webbrowser.open", lambda url: opened.append(url) or True)
    code, report = compile_and_launch(HarnessConfig(project_root=tmp_path, model="test"))
    assert code == 0
    assert "http://127.0.0.1:8766/" in report
    assert opened == ["http://127.0.0.1:8766/"]
