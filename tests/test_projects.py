from codeharness.commands import handle_turn
from codeharness.config import HarnessConfig
from codeharness.model import Completion, ToolCall
from codeharness.projects import task_slug
from codeharness.run import compile_and_launch
from codeharness.session import SessionStore, database_path
from tests.fakes import ScriptedModel


def _write(path: str, content: str) -> Completion:
    return Completion(
        content="",
        tool_calls=[ToolCall(id="w", name="write_file", arguments={"path": path, "content": content})],
        prompt_tokens=1,
        completion_tokens=1,
    )


def _edit(path: str, old: str, new: str) -> Completion:
    return Completion(
        content="",
        tool_calls=[
            ToolCall(
                id="e",
                name="edit_file",
                arguments={"path": path, "old_string": old, "new_string": new},
            )
        ],
        prompt_tokens=1,
        completion_tokens=1,
    )


def test_task_slug_uses_the_program_name() -> None:
    assert task_slug("build an atm") == "atm"
    assert task_slug("handoff build a digital clock") == "digital-clock"
    assert task_slug("make it red") == "it-red"
    assert task_slug("lets build a scientific calculator") == "scientific-calculator"
    noted = "build an atm\n\nBuild a full stack app. Write index.html and server.py."
    assert task_slug(noted) == "atm"


def test_a_build_lands_in_its_own_folder(tmp_path) -> None:
    model = ScriptedModel(
        [
            _write("atm.py", "print('atm')\n"),
            Completion(content="built", tool_calls=[], prompt_tokens=1, completion_tokens=1),
            _edit("atm.py", "print('atm')\n", "print('atm-fixed')\n"),
            Completion(content="fixed", tool_calls=[], prompt_tokens=1, completion_tokens=1),
            _write("clock.py", "print('clock')\n"),
            Completion(content="clocked", tool_calls=[], prompt_tokens=1, completion_tokens=1),
            Completion(content="left it", tool_calls=[], prompt_tokens=1, completion_tokens=1),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    config = HarnessConfig(
        project_root=tmp_path,
        model="test",
        permissions={**HarnessConfig().permissions, "write_file": "allow", "edit_file": "allow"},
    )
    handle_turn(store, session, "build an atm", model, config, ask=lambda name, detail: False)
    handle_turn(store, session, "fix the menu", model, config, ask=lambda name, detail: False)
    handle_turn(store, session, "build a clock", model, config, ask=lambda name, detail: False)
    handle_turn(store, session, "make the button red", model, config, ask=lambda name, detail: False)
    store.close()
    assert (tmp_path / "projects" / "atm" / "atm.py").read_text(encoding="utf-8") == "print('atm-fixed')\n"
    assert (tmp_path / "projects" / "clock" / "clock.py").read_text(encoding="utf-8") == "print('clock')\n"
    assert not (tmp_path / "atm.py").exists()
    assert not (tmp_path / "clock.py").exists()
    assert not (tmp_path / "projects" / "button-red").exists()
    joined = "\n".join(item.get("content") or "" for batch in model.seen_messages for item in batch)
    assert "atm.py" in joined


def test_undo_puts_the_file_back(tmp_path) -> None:
    model = ScriptedModel(
        [
            _write("atm.py", "print('atm')\n"),
            Completion(content="built", tool_calls=[], prompt_tokens=1, completion_tokens=1),
            _edit("atm.py", "print('atm')\n", "print('atm-fixed')\n"),
            Completion(content="fixed", tool_calls=[], prompt_tokens=1, completion_tokens=1),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    config = HarnessConfig(
        project_root=tmp_path,
        model="test",
        permissions={**HarnessConfig().permissions, "write_file": "allow", "edit_file": "allow"},
    )
    handle_turn(store, session, "build an atm", model, config, ask=lambda name, detail: False)
    handle_turn(store, session, "fix the menu", model, config, ask=lambda name, detail: False)
    program = tmp_path / "projects" / "atm" / "atm.py"
    assert program.read_text(encoding="utf-8") == "print('atm-fixed')\n"
    handle_turn(store, session, "undo", model, config, ask=lambda name, detail: False)
    store.close()
    assert program.read_text(encoding="utf-8") == "print('atm')\n"


def test_launch_from_the_harness_uses_projects(tmp_path) -> None:
    (tmp_path / "src" / "codeharness").mkdir(parents=True)
    (tmp_path / "stray.py").write_text("print('stray')\n", encoding="utf-8")
    program = tmp_path / "projects" / "atm"
    program.mkdir(parents=True)
    (program / "atm.py").write_text("print('from-project')\n", encoding="utf-8")
    code, report = compile_and_launch(HarnessConfig(project_root=tmp_path, model="test", shell_timeout=15))
    assert code == 0
    assert "from-project" in report
    assert "stray" not in report
