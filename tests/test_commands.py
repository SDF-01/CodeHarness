from codeharness.commands import handle_turn
from codeharness.config import HarnessConfig
from codeharness.errors import TurnStopped
from codeharness.model import Completion, ToolCall
from codeharness.session import SessionStore, database_path
from tests.fakes import ScriptedModel


def test_handoff_plans_then_builds(tmp_path) -> None:
    model = ScriptedModel(
        [
            Completion(content="Write app.py that prints ok.", tool_calls=[], prompt_tokens=1, completion_tokens=1),
            Completion(
                content="",
                tool_calls=[
                    ToolCall(id="w1", name="write_file", arguments={"path": "app.py", "content": "print('ok')\n"})
                ],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="Built app.py.", tool_calls=[], prompt_tokens=1, completion_tokens=1),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    code = handle_turn(
        store,
        session,
        "handoff build a printer",
        model,
        HarnessConfig(
            project_root=tmp_path,
            model="test",
            permissions={**HarnessConfig().permissions, "write_file": "allow"},
        ),
        ask=lambda name, detail: False,
    )
    text = "\n".join(message.content for message in session.messages)
    agent = store.get_agent(session)
    store.close()
    assert code == 0
    assert agent == "build"
    assert (tmp_path / "projects" / "printer" / "app.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert "Implement this plan:" in text
    assert "Write app.py that prints ok." in text


def test_empty_plan_does_not_build(tmp_path) -> None:
    model = ScriptedModel(
        [
            Completion(content="", tool_calls=[], prompt_tokens=1, completion_tokens=1),
            Completion(content="should not run", tool_calls=[], prompt_tokens=1, completion_tokens=1),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    events: list[str] = []
    handle_turn(
        store,
        session,
        "handoff build a clock",
        model,
        HarnessConfig(project_root=tmp_path, model="test"),
        ask=lambda name, detail: False,
        on_event=lambda event: events.append(event.text),
    )
    store.close()
    assert model.steps
    assert model.steps[0].content == "should not run"
    assert any("Build did not start" in text for text in events)
    assert not (tmp_path / "app.py").exists()


def test_a_build_asks_before_a_realistic_web_app(tmp_path) -> None:
    asked: list[str] = []

    def ask(name, detail):
        asked.append(name)
        return True

    model = ScriptedModel(
        [Completion(content="Web app noted.", tool_calls=[], prompt_tokens=1, completion_tokens=1)]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    handle_turn(
        store,
        session,
        "build an atm",
        model,
        HarnessConfig(project_root=tmp_path, model="test", open_windows=False),
        ask=ask,
        reply=lambda question: "a website",
    )
    text = session.messages[0].content
    store.close()
    assert asked == []
    assert "shadcn" in text
    assert "index.html" in text


def test_declining_a_web_app_stays_local(tmp_path) -> None:
    model = ScriptedModel(
        [Completion(content="Local program.", tool_calls=[], prompt_tokens=1, completion_tokens=1)]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    handle_turn(
        store,
        session,
        "build an atm",
        model,
        HarnessConfig(project_root=tmp_path, model="test"),
        ask=lambda name, detail: False,
        reply=lambda question: "a desktop app",
    )
    text = session.messages[0].content
    store.close()
    assert "Do not create a website." in text
    assert "React" not in text


def test_an_explicit_stack_skips_the_web_question(tmp_path) -> None:
    asked: list[str] = []
    model = ScriptedModel(
        [Completion(content="React dashboard.", tool_calls=[], prompt_tokens=1, completion_tokens=1)]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    handle_turn(
        store,
        session,
        "build a react dashboard",
        model,
        HarnessConfig(project_root=tmp_path, model="test", open_windows=False),
        ask=lambda name, detail: asked.append(name) or True,
    )
    store.close()
    assert asked == []


def test_plan_and_build_switch_the_agent(tmp_path) -> None:
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    config = HarnessConfig(project_root=tmp_path, model="test")
    model = ScriptedModel([])
    handle_turn(store, session, "plan", model, config, ask=lambda name, detail: False)
    assert store.get_agent(session) == "plan"
    handle_turn(store, session, "build", model, config, ask=lambda name, detail: False)
    assert store.get_agent(session) == "build"
    store.close()


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


def test_a_full_stack_task_runs_separate_branches(tmp_path) -> None:
    model = ScriptedModel(
        [
            Completion(content="page, api, review", tool_calls=[], prompt_tokens=1, completion_tokens=1),
            Completion(
                content="",
                tool_calls=[ToolCall(id="w1", name="write_file", arguments={"path": "index.html", "content": _PAGE})],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="wrote the page", tool_calls=[], prompt_tokens=1, completion_tokens=1),
            Completion(
                content="",
                tool_calls=[ToolCall(id="w2", name="write_file", arguments={"path": "server.py", "content": _SERVER})],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="wrote the api", tool_calls=[], prompt_tokens=1, completion_tokens=1),
            Completion(content="checked", tool_calls=[], prompt_tokens=1, completion_tokens=1),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    code = handle_turn(
        store,
        session,
        "handoff build a full stack notes app in html",
        model,
        HarnessConfig(
            project_root=tmp_path,
            model="test",
            open_windows=False,
            permissions={**HarnessConfig().permissions, "write_file": "allow", "edit_file": "allow"},
        ),
        ask=lambda name, detail: False,
    )
    folder = tmp_path / "projects" / "full-stack-notes"
    parent = "\n".join(message.content for message in session.messages)
    api_prompt = next(
        batch
        for batch in model.seen_messages
        if any((item.get("content") or "").startswith("Write server.py") for item in batch)
    )
    page_prompt = next(
        batch
        for batch in model.seen_messages
        if any((item.get("content") or "").startswith("Write index.html") for item in batch)
    )
    route_tools = [tool["function"]["name"] for tool in model.seen_tools[0]]
    page_tools = [tool["function"]["name"] for tool in model.seen_tools[1]]
    store.close()
    assert code == 0
    assert (folder / "index.html").read_text(encoding="utf-8") == _PAGE
    assert (folder / "server.py").read_text(encoding="utf-8") == _SERVER
    assert "Page: wrote the page" in parent
    assert "API: wrote the api" in parent
    assert "Health passed." in parent
    assert "index.html" in (folder / "PLAN.md").read_text(encoding="utf-8")
    assert "server.py" in (folder / "PLAN.md").read_text(encoding="utf-8")
    assert "fetch" not in "\n".join(item.get("content") or "" for item in api_prompt)
    assert all(item.get("role") != "tool" for item in api_prompt)
    assert "Active skills: web-app" in page_prompt[0]["content"]
    assert "Active skills: fullstack" in api_prompt[0]["content"]
    assert "Active skills: web-app, " not in page_prompt[0]["content"]
    assert "write_file" not in route_tools
    assert "write_file" in page_tools
    assert "git_commit" not in page_tools


def test_an_empty_route_does_not_start_branches(tmp_path) -> None:
    model = ScriptedModel(
        [
            Completion(content="", tool_calls=[], prompt_tokens=1, completion_tokens=1),
            Completion(content="should not run", tool_calls=[], prompt_tokens=1, completion_tokens=1),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    events: list[str] = []
    handle_turn(
        store,
        session,
        "handoff build a full stack notes app in html",
        model,
        HarnessConfig(project_root=tmp_path, model="test"),
        ask=lambda name, detail: False,
        on_event=lambda event: events.append(event.text),
    )
    store.close()
    assert model.steps
    assert model.steps[0].content == "should not run"
    assert any("Branches did not start" in text for text in events)
    assert not (tmp_path / "projects" / "full-stack-notes" / "index.html").exists()


def test_a_greeting_cannot_create_a_file(tmp_path) -> None:
    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[
                    ToolCall(id="w1", name="write_file", arguments={"path": "main.py", "content": "print(1)\n"})
                ],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="should not run", tool_calls=[], prompt_tokens=1, completion_tokens=1),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    events: list[str] = []
    handle_turn(
        store,
        session,
        "what up g",
        model,
        HarnessConfig(project_root=tmp_path, model="test"),
        ask=lambda name, detail: True,
        on_event=lambda event: events.append(event.body or event.text),
    )
    text = "\n".join(message.content for message in session.messages)
    store.close()
    assert model.seen_tools[0] == []
    assert not (tmp_path / "main.py").exists()
    assert "Say what you want built." in text
    assert any("Say what you want built." in item for item in events)


def test_a_sentence_at_approval_becomes_the_request(tmp_path) -> None:
    raised = {"done": False}

    def ask(name, detail):
        if not raised["done"]:
            raised["done"] = True
            raise TurnStopped("build me your mom")
        return True

    picked: list[str] = []

    def choose(folders, suggested):
        picked.append(suggested)
        return tmp_path / "projects" / "mom-app"

    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[
                    ToolCall(id="w1", name="write_file", arguments={"path": "main.py", "content": "print(1)\n"}),
                    ToolCall(id="s1", name="shell", arguments={"command": "python main.py"}),
                ],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="Ready.", tool_calls=[], prompt_tokens=1, completion_tokens=1),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    events: list[str] = []
    handle_turn(
        store,
        session,
        "fix the menu",
        model,
        HarnessConfig(project_root=tmp_path, model="test"),
        ask=ask,
        on_event=lambda event: events.append(event.body or event.text),
        choose=choose,
    )
    text = "\n".join(message.content for message in session.messages)
    store.close()
    assert not (tmp_path / "main.py").exists()
    assert picked == ["your-mom"]
    assert (tmp_path / "projects" / "mom-app").is_dir()
    assert "Ready." in text
    assert any("Using that as the request." in item for item in events)
    assert not any("Denied." in item for item in events)
    assert not any("Open main.py" in item for item in events)


def test_a_missing_file_is_not_opened(tmp_path) -> None:
    asked: list[str] = []

    def ask(name, detail):
        asked.append(name)
        return False

    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[
                    ToolCall(id="w1", name="write_file", arguments={"path": "main.py", "content": "print(1)\n"}),
                    ToolCall(id="s1", name="shell", arguments={"command": "python main.py"}),
                ],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="stopped", tool_calls=[], prompt_tokens=1, completion_tokens=1),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    events: list[str] = []
    handle_turn(
        store,
        session,
        "fix the menu",
        model,
        HarnessConfig(project_root=tmp_path, model="test"),
        ask=ask,
        on_event=lambda event: events.append(event.body or event.text),
    )
    store.close()
    assert asked == ["write_file"]
    assert any("That file is not there yet." in item for item in events)
    assert not (tmp_path / "main.py").exists()


def test_a_build_compiles_and_launches(tmp_path) -> None:
    asked: list[str] = []

    def ask(name, detail):
        asked.append(name)
        return True

    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[
                    ToolCall(
                        id="w1",
                        name="write_file",
                        arguments={"path": "main.py", "content": "print('hello from harness')\n"},
                    )
                ],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="built", tool_calls=[], prompt_tokens=1, completion_tokens=1),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    events: list[str] = []
    handle_turn(
        store,
        session,
        "build a clock",
        model,
        HarnessConfig(project_root=tmp_path, model="test", open_windows=True),
        ask=ask,
        on_event=lambda event: events.append(event.body or event.text),
        choose=lambda folders, suggested: tmp_path / "projects" / "clock",
    )
    program = tmp_path / "projects" / "clock" / "main.py"
    store.close()
    assert asked == ["build_go"]
    assert program.read_text(encoding="utf-8") == "print('hello from harness')\n"
    assert any("Compiling." in item for item in events)
    assert any("Launch passed." in item for item in events)
    assert any("hello from harness" in item for item in events)


def test_declining_the_build_does_not_write(tmp_path) -> None:
    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[
                    ToolCall(id="w1", name="write_file", arguments={"path": "main.py", "content": "print(1)\n"})
                ],
                prompt_tokens=1,
                completion_tokens=1,
            )
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    events: list[str] = []
    handle_turn(
        store,
        session,
        "build a clock",
        model,
        HarnessConfig(project_root=tmp_path, model="test"),
        ask=lambda name, detail: False,
        on_event=lambda event: events.append(event.body or event.text),
        choose=lambda folders, suggested: tmp_path / "projects" / "clock",
    )
    store.close()
    assert model.seen_messages
    assert not any("Launch passed." in item for item in events)
    assert not (tmp_path / "projects" / "clock" / "main.py").exists()


def test_cancelling_the_folder_box_does_not_build(tmp_path) -> None:
    model = ScriptedModel(
        [Completion(content="should not run", tool_calls=[], prompt_tokens=1, completion_tokens=1)]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    events: list[str] = []
    handle_turn(
        store,
        session,
        "build a clock",
        model,
        HarnessConfig(project_root=tmp_path, model="test"),
        ask=lambda name, detail: True,
        on_event=lambda event: events.append(event.body or event.text),
        choose=lambda folders, suggested: None,
    )
    store.close()
    assert model.steps[0].content == "should not run"
    assert any("No folder chosen." in item for item in events)
    assert not (tmp_path / "projects" / "clock").exists()
