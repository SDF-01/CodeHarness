from codeharness.commands import handle_turn
from codeharness.config import HarnessConfig
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
        HarnessConfig(project_root=tmp_path, model="test"),
        ask=ask,
    )
    text = session.messages[0].content
    store.close()
    assert asked == ["web_gui"]
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
        HarnessConfig(project_root=tmp_path, model="test"),
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
