from codeharness.config import HarnessConfig
from codeharness.loop import run_turn
from codeharness.model import Completion, ToolCall
from codeharness.review import undefined_problem
from codeharness.session import SessionStore, database_path
from tests.fakes import ScriptedModel


def test_undefined_name_is_reported(tmp_path) -> None:
    path = tmp_path / "app.py"
    path.write_text("print(missing_name)\n", encoding="utf-8")
    problem = undefined_problem(path)
    assert problem is not None
    assert "missing_name" in problem


def test_defined_names_and_imports_are_allowed(tmp_path) -> None:
    path = tmp_path / "app.py"
    path.write_text("import sys\n\ndef main(value):\n    print(value)\n    return sys.argv\n", encoding="utf-8")
    assert undefined_problem(path) is None


def test_undefined_name_is_sent_back(tmp_path) -> None:
    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[
                    ToolCall(id="w1", name="write_file", arguments={"path": "app.py", "content": "print(missing_name)\n"})
                ],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="done", tool_calls=[], prompt_tokens=1, completion_tokens=1),
            Completion(
                content="",
                tool_calls=[
                    ToolCall(id="w2", name="write_file", arguments={"path": "app.py", "content": "print('ok')\n"})
                ],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="fixed names", tool_calls=[], prompt_tokens=1, completion_tokens=1),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    result = run_turn(
        store=store,
        session=session,
        user_text="build a script",
        model=model,
        config=HarnessConfig(
            project_root=tmp_path,
            model="test",
            permissions={**HarnessConfig().permissions, "write_file": "allow"},
        ),
        ask=lambda name, detail: False,
    )
    lessons = store.lessons(session)
    store.close()
    assert result.text == "fixed names"
    assert any(message.content.startswith("Review failed.") for message in session.messages)
    assert any("Define or import" in lesson for lesson in lessons)


def test_runtime_failure_is_sent_back(tmp_path) -> None:
    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[
                    ToolCall(
                        id="w1",
                        name="write_file",
                        arguments={"path": "app.py", "content": "raise SystemExit('boom-runtime')\n"},
                    )
                ],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="done", tool_calls=[], prompt_tokens=1, completion_tokens=1),
            Completion(
                content="",
                tool_calls=[
                    ToolCall(id="w2", name="write_file", arguments={"path": "app.py", "content": "print('ok')\n"})
                ],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="fixed run", tool_calls=[], prompt_tokens=1, completion_tokens=1),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    result = run_turn(
        store=store,
        session=session,
        user_text="build a script",
        model=model,
        config=HarnessConfig(
            project_root=tmp_path,
            model="test",
            permissions={**HarnessConfig().permissions, "write_file": "allow"},
        ),
        ask=lambda name, detail: False,
    )
    store.close()
    assert result.text == "fixed run"
    assert any("boom-runtime" in message.content for message in session.messages)
    assert any(message.content.startswith("Run failed.") for message in session.messages)
