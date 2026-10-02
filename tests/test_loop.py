import sys
from pathlib import Path

from codeharness.config import HarnessConfig
from codeharness.loop import run_turn
from codeharness.model import Completion, ToolCall
from codeharness.session import SessionStore, database_path
from tests.fakes import ScriptedModel


def _config(tmp_path: Path, **kwargs) -> HarnessConfig:
    return HarnessConfig(project_root=tmp_path, model="test", **kwargs)


def test_loop_reads_a_file_then_answers(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("alpha\n", encoding="utf-8")
    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "a.py"})],
                prompt_tokens=10,
                completion_tokens=4,
            ),
            Completion(content="The file says alpha.", tool_calls=[], prompt_tokens=20, completion_tokens=6),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    result = run_turn(
        store=store,
        session=session,
        user_text="what is in a.py",
        model=model,
        config=_config(tmp_path),
        ask=lambda name, detail: False,
    )
    store.close()
    assert result.text == "The file says alpha."
    assert "alpha" in str(model.seen_messages[1])
    assert result.usage.prompt_tokens == 30
    assert result.usage.tool_calls == 1
    assert result.usage.estimated is False


def test_denied_shell_is_omitted_and_not_run(tmp_path: Path) -> None:
    permissions = dict(HarnessConfig().permissions)
    permissions["shell"] = "deny"
    model = ScriptedModel(
        [Completion(content="done", tool_calls=[], prompt_tokens=1, completion_tokens=1)]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    run_turn(
        store=store,
        session=session,
        user_text="hi",
        model=model,
        config=_config(tmp_path, permissions=permissions),
        ask=lambda name, detail: False,
    )
    store.close()
    names = [tool["function"]["name"] for tool in model.seen_tools[0]]
    assert "shell" not in names
    assert "edit_file" in names


def test_doom_loop_stops_the_third_identical_call(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("alpha\n", encoding="utf-8")
    steps = [
        Completion(
            content="",
            tool_calls=[ToolCall(id=f"c{index}", name="read_file", arguments={"path": "a.py"})],
            prompt_tokens=3,
            completion_tokens=2,
        )
        for index in range(3)
    ]
    model = ScriptedModel(steps)
    events: list[str] = []
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    run_turn(
        store=store,
        session=session,
        user_text="read it",
        model=model,
        config=_config(tmp_path, max_steps=3, doom_repeat_limit=3),
        ask=lambda name, detail: False,
        on_event=lambda event: events.append(event.text),
    )
    store.close()
    assert sum(text.startswith("tool: read_file") for text in events) == 2
    assert any(text.startswith("denied: repeated read_file") for text in events)


def test_a_new_program_is_not_judged_by_repo_tests(tmp_path: Path) -> None:
    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[
                    ToolCall(id="w1", name="write_file", arguments={"path": "app.py", "content": "print('ok')\n"})
                ],
                prompt_tokens=5,
                completion_tokens=5,
            ),
            Completion(content="Wrote app.py. Check passed.", tool_calls=[], prompt_tokens=8, completion_tokens=4),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    result = run_turn(
        store=store,
        session=session,
        user_text="add a script",
        model=model,
        config=HarnessConfig(
            project_root=tmp_path,
            model="test",
            permissions={**HarnessConfig().permissions, "write_file": "allow"},
            check_command=f"\"{sys.executable}\" -c \"import pathlib; assert pathlib.Path('app.py').exists()\"",
            shell_timeout=10,
        ),
        ask=lambda name, detail: False,
    )
    store.close()
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert not any(message.content.startswith("Check result:") for message in session.messages)
    assert "print('ok')" not in result.text


def test_invalid_python_is_sent_back_for_a_fix(tmp_path: Path) -> None:
    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[ToolCall(id="w1", name="write_file", arguments={"path": "app.py", "content": "def broken("})],
                prompt_tokens=4,
                completion_tokens=4,
            ),
            Completion(content="I think it is done.", tool_calls=[], prompt_tokens=4, completion_tokens=4),
            Completion(
                content="",
                tool_calls=[ToolCall(id="w2", name="write_file", arguments={"path": "app.py", "content": "print('ok')\n"})],
                prompt_tokens=4,
                completion_tokens=4,
            ),
            Completion(content="app.py is valid.", tool_calls=[], prompt_tokens=4, completion_tokens=4),
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
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert result.text == "app.py is valid."
    assert any(message.content.startswith("Review failed.") for message in session.messages)


def test_edit_runs_only_after_approval(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("old\n", encoding="utf-8")
    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[
                    ToolCall(
                        id="e1",
                        name="edit_file",
                        arguments={"path": "note.txt", "old_string": "old\n", "new_string": "new\n"},
                    )
                ],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="updated", tool_calls=[], prompt_tokens=1, completion_tokens=1),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    run_turn(
        store=store,
        session=session,
        user_text="change it",
        model=model,
        config=_config(tmp_path),
        ask=lambda name, detail: True,
    )
    store.close()
    assert target.read_text(encoding="utf-8") == "new\n"


def test_failed_shell_blocks_the_answer_until_a_later_command_works(tmp_path: Path) -> None:
    permissions = {**HarnessConfig().permissions, "shell": "allow"}
    fail = f"\"{sys.executable}\" -c \"import sys; raise SystemExit(1)\""
    ok = f"\"{sys.executable}\" -c \"print(1)\""
    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[ToolCall(id="s1", name="shell", arguments={"command": fail})],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="all good", tool_calls=[], prompt_tokens=1, completion_tokens=1),
            Completion(
                content="",
                tool_calls=[ToolCall(id="s2", name="shell", arguments={"command": ok})],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="fixed", tool_calls=[], prompt_tokens=1, completion_tokens=1),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    result = run_turn(
        store=store,
        session=session,
        user_text="run a command",
        model=model,
        config=_config(tmp_path, permissions=permissions, shell_timeout=10),
        ask=lambda name, detail: False,
    )
    lessons = store.lessons(session)
    store.close()
    assert result.text == "fixed"
    assert any(message.content.startswith("Shell failed.") for message in session.messages)
    assert lessons


def test_plan_agent_does_not_write(tmp_path: Path) -> None:
    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[ToolCall(id="w1", name="write_file", arguments={"path": "app.py", "content": "print('no')\n"})],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="A plan only.", tool_calls=[], prompt_tokens=1, completion_tokens=1),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    store.set_agent(session, "plan")
    result = run_turn(
        store=store,
        session=session,
        user_text="write the app",
        model=model,
        config=_config(tmp_path, permissions={**HarnessConfig().permissions, "write_file": "allow"}),
        ask=lambda name, detail: True,
    )
    seen = model.seen_tools[0]
    store.close()
    assert result.text == "A plan only."
    assert not (tmp_path / "app.py").exists()
    names = [tool["function"]["name"] for tool in seen]
    assert "write_file" not in names
    assert "git_add" not in names
    assert "git_commit" not in names


def test_a_closed_window_is_sent_back(tmp_path: Path) -> None:
    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[
                    ToolCall(
                        id="w1",
                        name="write_file",
                        arguments={"path": "atm_gui.py", "content": "import tkinter\nprint('gone')\n"},
                    )
                ],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="done", tool_calls=[], prompt_tokens=1, completion_tokens=1),
            Completion(
                content="",
                tool_calls=[
                    ToolCall(id="w2", name="write_file", arguments={"path": "atm_gui.py", "content": "print('ok')\n"})
                ],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="ready", tool_calls=[], prompt_tokens=1, completion_tokens=1),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    result = run_turn(
        store=store,
        session=session,
        user_text="build a window",
        model=model,
        config=_config(tmp_path, permissions={**HarnessConfig().permissions, "write_file": "allow"}),
        ask=lambda name, detail: False,
    )
    lessons = store.lessons(session)
    store.close()
    assert result.text == "ready"
    assert any(message.content.startswith("Window failed.") for message in session.messages)
    assert any("mainloop" in lesson for lesson in lessons)
    assert "mainloop" in model.seen_messages[2][0]["content"]
