from pathlib import Path

from codeharness.commands import _finish_build, handle_turn
from codeharness.config import HarnessConfig
from codeharness.errors import TurnStopped
from codeharness.model import Completion, ToolCall
from codeharness.projects import task_slug
from codeharness.session import SessionStore, StoredMessage, database_path
from codeharness.taskrecord import begin_task, load_task, settle_task
from tests.fakes import ScriptedModel


def test_handoff_plans_then_builds(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "codeharness.run.open_window",
        lambda path, root: "Opened app.py. The window is running.",
    )
    model = ScriptedModel(
        [
            Completion(content="Write app.py that prints ok.", tool_calls=[], prompt_tokens=1, completion_tokens=1),
            Completion(
                content="",
                tool_calls=[
                    ToolCall(id="w1", name="write_file", arguments={"path": "logic.py", "content": _LOGIC}),
                    ToolCall(id="w2", name="write_file", arguments={"path": "app.py", "content": _WINDOW}),
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
    folder = tmp_path / "projects" / "printer"
    assert (folder / "logic.py").read_text(encoding="utf-8") == _LOGIC
    assert (folder / "app.py").read_text(encoding="utf-8") == _WINDOW.rstrip() + "\n\nmain()\n"
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
    assert asked == ["build_go"]
    assert "tkinter" in text
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
    assert asked == ["build_go"]


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


_LOGIC = "def load_notes():\n    return []\n"
_WINDOW = (
    "import logic\n"
    "import tkinter\n"
    "\n"
    "def main():\n"
    "    logic.load_notes()\n"
    "    root = tkinter.Tk()\n"
    "    root.title('Clock Printer')\n"
    "    root.configure(bg='#1e1e1e')\n"
    "    tkinter.Label(root, text='0', bg='#1e1e1e', fg='#ffffff').pack()\n"
    "    for label in ('1', '2', '3', '4'):\n"
    "        tkinter.Button(root, text=label, command=logic.load_notes, bg='#2d2d2d').pack()\n"
    "    root.mainloop()\n"
)
_PAGE = (
    "import logic\n"
    "import tkinter\n"
    "PORT = 8766\n"
    "def main():\n"
    "    logic.load_notes()\n"
    "    root = tkinter.Tk()\n"
    "    root.title('Notes')\n"
    "    root.configure(bg='#1e1e1e')\n"
    "    tkinter.Label(root, text='0', bg='#1e1e1e', fg='#ffffff').pack()\n"
    "    for label in ('1', '2', '3', '4'):\n"
    "        tkinter.Button(root, text=label, command=logic.load_notes, bg='#2d2d2d').pack()\n"
    "    root.mainloop()\n"
)
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
                tool_calls=[
                    ToolCall(id="w0", name="write_file", arguments={"path": "logic.py", "content": _LOGIC}),
                    ToolCall(id="w1", name="write_file", arguments={"path": "app.py", "content": _PAGE}),
                ],
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
        if any((item.get("content") or "").startswith("Write app.py") for item in batch)
    )
    route_tools = [tool["function"]["name"] for tool in model.seen_tools[0]]
    page_tools = [tool["function"]["name"] for tool in model.seen_tools[1]]
    store.close()
    assert code == 0
    assert (folder / "logic.py").read_text(encoding="utf-8") == _LOGIC
    assert (folder / "app.py").read_text(encoding="utf-8") == _PAGE
    assert (folder / "server.py").read_text(encoding="utf-8") == _SERVER
    assert "Page: wrote the page" in parent
    assert "API: wrote the api" in parent
    assert "Health passed." in parent
    assert "app.py" in (folder / "PLAN.md").read_text(encoding="utf-8")
    assert "server.py" in (folder / "PLAN.md").read_text(encoding="utf-8")
    assert "fetch" not in "\n".join(item.get("content") or "" for item in api_prompt)
    assert all(item.get("role") != "tool" for item in api_prompt)
    assert "Active skills: gui-app" in page_prompt[0]["content"]
    assert "Active skills: fullstack" in api_prompt[0]["content"]
    assert "Active skills: gui-app, " not in page_prompt[0]["content"]
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


def test_a_build_compiles_and_launches(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "codeharness.run.open_window",
        lambda path, root: "Opened app.py. The window is running.",
    )
    asked: list[str] = []

    def ask(name, detail):
        asked.append(name)
        return True

    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[
                    ToolCall(id="w1", name="write_file", arguments={"path": "logic.py", "content": _LOGIC}),
                    ToolCall(id="w2", name="write_file", arguments={"path": "app.py", "content": _WINDOW}),
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
    program = tmp_path / "projects" / "clock" / "app.py"
    store.close()
    assert asked == ["build_go"]
    assert (program.parent / "logic.py").is_file()
    assert "mainloop" in program.read_text(encoding="utf-8")
    assert any("Compiling." in item for item in events)
    assert any("Launch passed." in item for item in events)


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


def test_lets_build_a_desktop_plugin_ignores_the_previous_program(tmp_path) -> None:
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
                        arguments={
                            "path": "calculator.py",
                            "content": "import tkinter\nroot = tkinter.Tk()\nroot.mainloop()\n",
                        },
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
    store.append(session, StoredMessage(role="user", content="build me your mom"))
    store.append(session, StoredMessage(role="assistant", content="Created mom.py"))
    store.set_work_dir(session, tmp_path / "projects" / "pocket-watch")
    handle_turn(
        store,
        session,
        "new project lets build a scientific calculator",
        model,
        HarnessConfig(project_root=tmp_path, model="test", open_windows=False),
        ask=ask,
        reply=lambda question: "yes, but i want it to be an actual desktop plugin",
        choose=lambda folders, suggested: tmp_path / "projects" / suggested,
    )
    prompt = "\n".join(item.get("content") or "" for item in model.seen_messages[0])
    program = tmp_path / "projects" / "scientific-calculator" / "calculator.py"
    store.close()
    assert asked == ["build_go"]
    assert "mom" not in prompt
    assert "tkinter" in prompt
    assert "mainloop" in program.read_text(encoding="utf-8")
    assert not (tmp_path / "mom.py").exists()


def test_launch_opens_the_open_folder(tmp_path) -> None:
    watch = tmp_path / "projects" / "pocket-watch"
    watch.mkdir(parents=True)
    (watch / "index.html").write_text("<html>watch</html>\n", encoding="utf-8")
    calc = tmp_path / "projects" / "scientific-calculator"
    calc.mkdir(parents=True)
    (calc / "calculator.py").write_text("print('calc-window')\n", encoding="utf-8")
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    store.append(session, StoredMessage(role="user", content="lets build a pocket watch"))
    store.set_work_dir(session, calc.resolve())
    events: list[str] = []
    handle_turn(
        store,
        session,
        "launch it",
        ScriptedModel([]),
        HarnessConfig(project_root=tmp_path, model="test"),
        ask=lambda name, detail: True,
        on_event=lambda event: events.append(event.body or event.text),
    )
    store.close()
    assert any("calc-window" in item for item in events)
    assert any("scientific-calculator" in item for item in events)
    assert not any("pocket-watch" in item for item in events)
    assert not any("browser" in item.lower() for item in events)


def test_launch_does_not_open_a_blank_window(tmp_path, monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        "codeharness.run.open_window",
        lambda path, root: opened.append(path.name) or "Opened app.py. The window is running.",
    )
    folder = tmp_path / "projects" / "calculator"
    folder.mkdir(parents=True)
    (folder / "logic.py").write_text("def ready():\n    return 0\n", encoding="utf-8")
    (folder / "app.py").write_text(
        "import logic\nimport tkinter\n"
        "def main():\n"
        "    logic.ready()\n"
        "    root = tkinter.Tk()\n"
        "    root.title('My App')\n"
        "    root.mainloop()\n",
        encoding="utf-8",
    )
    begin_task(folder, "build a calculator")
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    store.set_work_dir(session, folder.resolve())
    events: list[str] = []
    handle_turn(
        store,
        session,
        "launch it",
        ScriptedModel([]),
        HarnessConfig(project_root=tmp_path, model="test"),
        ask=lambda name, detail: True,
        on_event=lambda event: events.append(event.body or event.text),
    )
    store.close()
    assert opened == []
    assert any("failed: logic" in item for item in events)


def test_the_kind_answer_is_applied_before_the_folder(tmp_path) -> None:
    request = "build an atm that rejects empty input"
    order: list[str] = []

    def reply(question: str) -> str:
        order.append("kind")
        assert "desktop app" in question
        assert "local API" in question
        assert "website" not in question
        assert not (tmp_path / "projects" / task_slug(request)).exists()
        return "a command line tool"

    def choose(folders, suggested: str):
        order.append(suggested)
        assert suggested == task_slug(request)
        assert not (tmp_path / "projects" / suggested).exists()
        return tmp_path / "projects" / suggested

    def ask(name: str, detail: str) -> bool:
        order.append(detail)
        assert name == "build_go"
        return False

    model = ScriptedModel(
        [Completion(content="noted", tool_calls=[], prompt_tokens=1, completion_tokens=1)]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    handle_turn(
        store,
        session,
        request,
        model,
        HarnessConfig(project_root=tmp_path, model="test", open_windows=False),
        ask=ask,
        reply=reply,
        choose=choose,
    )
    text = "\n".join(message.content for message in session.messages)
    store.close()
    assert order[0] == "kind"
    assert order[1] == task_slug(request)
    assert "command line" in order[2]
    assert f"projects/{task_slug(request)}" in order[2]
    assert "I will check that it runs and rejects empty input." in order[2]
    assert "reject empty input" in text
    record = load_task(tmp_path / "projects" / task_slug(request))
    assert record is not None
    assert "rejects empty input" in record["requirements"][0]["acceptance"]


def test_a_successful_launch_does_not_verify(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "codeharness.run.open_window",
        lambda path, root: "Opened app.py. The window is running.",
    )
    folder = tmp_path / "projects" / "clock"
    folder.mkdir(parents=True)
    (folder / "logic.py").write_text(_LOGIC, encoding="utf-8")
    (folder / "app.py").write_text(_WINDOW, encoding="utf-8")
    begin_task(folder, "build a clock")
    settle_task(folder, "built", [])
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    code = _finish_build(
        store,
        session,
        ScriptedModel([]),
        HarnessConfig(project_root=folder, model="test", open_windows=True),
        ask=lambda name, detail: False,
        on_event=None,
        mode="build",
    )
    store.close()
    record = load_task(folder)
    assert code == 0
    assert record is not None
    assert record["requirements"][0]["state"] == "unverified"
    assert "passed: launched" not in record["evidence"]


def test_a_missing_logic_module_is_not_launched(tmp_path, monkeypatch) -> None:
    launched: list[str] = []

    def refuse_launch(config):
        launched.append("launched")
        return 0, "Launch passed."

    monkeypatch.setattr("codeharness.commands.compile_and_launch", refuse_launch)
    folder = tmp_path / "projects" / "clock"
    folder.mkdir(parents=True)
    (folder / "app.py").write_text("import tkinter\nroot = tkinter.Tk()\nroot.mainloop()\n", encoding="utf-8")
    begin_task(folder, "build a clock")
    settle_task(folder, "built", [])
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    code = _finish_build(
        store,
        session,
        ScriptedModel([]),
        HarnessConfig(project_root=folder, model="test", open_windows=True),
        ask=lambda name, detail: False,
        on_event=None,
        mode="build",
        request="build a clock",
    )
    store.close()
    record = load_task(folder)
    assert launched == []
    assert code == 1
    assert record is not None
    assert record["requirements"][0]["state"] == "failed"
    assert "failed: logic" in record["evidence"]
    assert record["faults"]
    assert "missing" in record["faults"][0]["cause"]


def test_a_broken_calculator_is_repaired_then_launched(tmp_path, monkeypatch) -> None:
    launched: list[str] = []

    def launch(config):
        launched.append(config.project_root.name)
        return 0, "Launch passed."

    monkeypatch.setattr("codeharness.commands.compile_and_launch", launch)
    folder = tmp_path / "projects" / "calculator"
    folder.mkdir(parents=True)
    (folder / "logic.py").write_text(
        "def calculate(expression):\n    return eval(expression)\n",
        encoding="utf-8",
    )
    (folder / "app.py").write_text(
        "import logic\nimport tkinter as tk\n\n"
        "def main():\n"
        "    root = tk.Tk()\n"
        "    root.title('Calculator')\n"
        "    root.configure(bg='#1e1e1e')\n"
        "    shown = tk.StringVar(value='0')\n"
        "    tk.Label(root, textvariable=shown, bg='#1e1e1e').grid(row=0, column=0, columnspan=4)\n"
        "    labels = ('0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '.', 'AC', 'Backspace', '/', 'x', '-', '+', '=')\n"
        "    for index, label in enumerate(labels):\n"
        "        tk.Button(root, text=label, command=lambda name=label: shown.set(str(logic.apply(shown.get(), name)))).grid()\n"
        "    root.update()\n",
        encoding="utf-8",
    )
    begin_task(folder, "build a calculator")
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    code = _finish_build(
        store,
        session,
        ScriptedModel([]),
        HarnessConfig(project_root=folder, model="test", open_windows=True),
        ask=lambda name, detail: False,
        on_event=None,
        mode="build",
        request="build a calculator",
    )
    store.close()
    record = load_task(folder)
    window = (folder / "app.py").read_text(encoding="utf-8")
    assert code == 0
    assert launched == ["calculator"]
    assert "mainloop" in window
    assert "main()" in window
    assert "def apply" in (folder / "logic.py").read_text(encoding="utf-8")
    assert record is not None
    assert record["faults"]
    assert all(item["state"] == "fixed" for item in record["faults"])


def test_a_stuck_fault_keeps_the_cause(tmp_path, monkeypatch) -> None:
    launched: list[str] = []
    monkeypatch.setattr(
        "codeharness.commands.compile_and_launch",
        lambda config: launched.append("launched") or (0, "Launch passed."),
    )
    folder = tmp_path / "projects" / "printer"
    folder.mkdir(parents=True)
    (folder / "logic.py").write_text("def apply(current, name):\n    return current\n", encoding="utf-8")
    (folder / "app.py").write_text(
        "import logic\nimport tkinter\n\n"
        "def main():\n"
        "    root = tkinter.Tk()\n"
        "    root.title('Printer')\n"
        "    root.configure(bg='#1e1e1e')\n"
        "    tkinter.Label(root, text='Enter action:').pack()\n"
        "    tkinter.Entry(root).pack()\n"
        "    tkinter.Button(root, text='Check', command=logic.apply).pack()\n"
        "    root.mainloop()\n"
        "main()\n",
        encoding="utf-8",
    )
    begin_task(folder, "build a printer")
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    code = _finish_build(
        store,
        session,
        ScriptedModel([]),
        HarnessConfig(project_root=folder, model="test", open_windows=True),
        ask=lambda name, detail: False,
        on_event=None,
        mode="build",
        request="build a printer",
    )
    store.close()
    record = load_task(folder)
    assert launched == []
    assert code == 1
    assert record is not None
    text = "\n".join(record["evidence"])
    assert "command box" in text
    assert record["faults"][0]["symptom"]
    assert record["faults"][0]["cause"]


def test_an_open_project_keeps_the_next_request(tmp_path) -> None:
    folder = tmp_path / "projects" / "calculator"
    folder.mkdir(parents=True)
    (folder / "logic.py").write_text(_LOGIC, encoding="utf-8")
    (folder / "app.py").write_text(_WINDOW, encoding="utf-8")
    begin_task(folder, "build a calculator")
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    store.set_work_dir(session, folder.resolve())
    asked: list[str] = []
    model = ScriptedModel(
        [Completion(content="updated", tool_calls=[], prompt_tokens=1, completion_tokens=1)]
    )
    handle_turn(
        store,
        session,
        "make a history panel",
        model,
        HarnessConfig(project_root=tmp_path, model="test", open_windows=False),
        ask=lambda name, detail: asked.append(detail) or True,
        reply=lambda question: asked.append("kind") or "desktop",
        choose=lambda folders, suggested: asked.append("folder") or folder,
    )
    prompt = "\n".join(item.get("content") or "" for item in model.seen_messages[0])
    record = load_task(folder)
    opened = store.work_dir(session)
    store.close()
    assert "kind" not in asked
    assert "folder" not in asked
    assert any("Update this in projects/calculator?" in item for item in asked)
    assert Path(opened) == folder.resolve()
    assert "Do not create another product." in prompt
    assert "1+1" in prompt
    assert record is not None
    assert record["request"] == "build a calculator"
    assert record["amendments"] == ["make a history panel"]


def test_add_a_button_updates_the_open_project(tmp_path) -> None:
    folder = tmp_path / "projects" / "calculator"
    folder.mkdir(parents=True)
    (folder / "logic.py").write_text(_LOGIC, encoding="utf-8")
    (folder / "app.py").write_text(_WINDOW, encoding="utf-8")
    begin_task(folder, "build a calculator")
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    store.set_work_dir(session, folder.resolve())
    asked: list[str] = []
    model = ScriptedModel(
        [Completion(content="updated", tool_calls=[], prompt_tokens=1, completion_tokens=1)]
    )
    handle_turn(
        store,
        session,
        "add a backspace button",
        model,
        HarnessConfig(project_root=tmp_path, model="test", open_windows=False),
        ask=lambda name, detail: asked.append(detail) or True,
        reply=lambda question: asked.append("kind") or "desktop",
        choose=lambda folders, suggested: asked.append("folder") or folder,
    )
    prompt = "\n".join(item.get("content") or "" for item in model.seen_messages[0])
    record = load_task(folder)
    store.close()
    assert asked == ["Update this in projects/calculator? I will check that it runs."]
    assert "add a backspace button" in prompt
    assert "1+1" in prompt
    assert record is not None
    assert record["request"] == "build a calculator"
    assert record["amendments"] == ["add a backspace button"]


def test_a_new_project_asks_for_the_kind_and_the_folder(tmp_path) -> None:
    folder = tmp_path / "projects" / "calculator"
    folder.mkdir(parents=True)
    begin_task(folder, "build a calculator")
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    store.set_work_dir(session, folder.resolve())
    order: list[str] = []

    def reply(question: str) -> str:
        order.append("kind")
        assert "clock" in question
        return "a desktop app"

    def choose(folders, suggested: str):
        order.append(suggested)
        return tmp_path / "projects" / suggested

    handle_turn(
        store,
        session,
        "new project build a clock",
        ScriptedModel([Completion(content="started", tool_calls=[], prompt_tokens=1, completion_tokens=1)]),
        HarnessConfig(project_root=tmp_path, model="test", open_windows=False),
        ask=lambda name, detail: True,
        reply=reply,
        choose=choose,
    )
    store.close()
    assert order == ["kind", "clock"]
    assert session.title == "projects/clock"


def test_verify_stays_in_the_open_project(tmp_path) -> None:
    folder = tmp_path / "projects" / "calculator"
    folder.mkdir(parents=True)
    begin_task(folder, "build a calculator")
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    store.set_work_dir(session, folder.resolve())
    events: list[str] = []
    asked: list[str] = []
    handle_turn(
        store,
        session,
        "verify build",
        ScriptedModel([]),
        HarnessConfig(project_root=tmp_path, model="test", open_windows=False),
        ask=lambda name, detail: asked.append(name) or False,
        on_event=lambda event: events.append(event.body or event.text),
        reply=lambda question: asked.append(question) or "desktop",
        choose=lambda folders, suggested: asked.append("folder") or None,
    )
    store.close()
    assert asked == []
    assert session.title == "projects/calculator"
    assert any("logic.py" in item for item in events)
    assert not any("What kind of software" in item for item in events)
