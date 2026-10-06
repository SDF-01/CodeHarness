import pytest

from codeharness.agents import config_for_agent
from codeharness.commands import handle_turn
from codeharness.config import HarnessConfig
from codeharness.lead import _allow_writes
from codeharness.loop import run_turn
from codeharness.model import Completion, ToolCall
from codeharness.session import SessionStore, database_path
from codeharness.snapshot import capture, restore
from codeharness.stack import probe_health
from codeharness.tools import apply_patch, tools_for_model
from codeharness.ui import PAGE, Board
from tests.fakes import ScriptedModel


def _config(root, **extra):
    permissions = {**HarnessConfig().permissions, "write_file": "allow", "edit_file": "allow"}
    return HarnessConfig(project_root=root, model="test", permissions=permissions, **extra)


def _text(content: str) -> Completion:
    return Completion(content=content, tool_calls=[], prompt_tokens=1, completion_tokens=1)


def _write(path: str, content: str) -> Completion:
    return Completion(
        content="",
        tool_calls=[ToolCall(id="w", name="write_file", arguments={"path": path, "content": content})],
        prompt_tokens=1,
        completion_tokens=1,
    )


def test_explore_has_no_write_tool(tmp_path) -> None:
    config = config_for_agent(_config(tmp_path), "explore")
    names = [tool["function"]["name"] for tool in tools_for_model(config.permissions)]
    assert "write_file" not in names
    assert "edit_file" not in names
    assert "apply_patch" not in names
    assert "read_file" in names


def test_a_general_child_sees_one_todo(tmp_path) -> None:
    model = ScriptedModel(
        [
            _text("- [ ] Create atm.py\n- [ ] Create extra.py"),
            _write("atm.py", "print('atm')\n"),
            _text("built"),
            _text("No problems."),
            _write("extra.py", "print('extra')\n"),
            _text("built"),
            _text("No problems."),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    handle_turn(store, session, "build an atm in python", model, _config(tmp_path), ask=lambda name, detail: True)
    store.close()
    prompts = [
        item.get("content") or ""
        for batch in model.seen_messages
        for item in batch
        if (item.get("content") or "").startswith("Repo map:")
    ]
    assert any("Create atm.py" in prompt and "Create extra.py" not in prompt for prompt in prompts)
    assert any("Create extra.py" in prompt and "Create atm.py" not in prompt for prompt in prompts)
    assert all("build an atm in python" not in prompt for prompt in prompts)


def test_a_matching_patch_applies_and_a_missing_hunk_errors(tmp_path) -> None:
    (tmp_path / "a.py").write_text("print('alpha')\n", encoding="utf-8")
    config = _config(tmp_path)
    applied = apply_patch.run(
        {"hunks": [{"path": "a.py", "old_string": "print('alpha')\n", "new_string": "print('beta')\n"}]},
        tmp_path,
        config,
    )
    missing = apply_patch.run(
        {"hunks": [{"path": "a.py", "old_string": "gone\n", "new_string": "print('nope')\n"}]},
        tmp_path,
        config,
    )
    assert applied.startswith("patched")
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "print('beta')\n"
    assert "old_string was not found" in missing


def test_diagnostics_send_a_broken_file_back(tmp_path) -> None:
    model = ScriptedModel(
        [
            _text("- [ ] Create app.py"),
            _write("app.py", "def broken("),
            _text("tried"),
            _write("app.py", "print('ok')\n"),
            _text("fixed"),
            _text("No problems."),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    handle_turn(
        store,
        session,
        "build a ledger",
        model,
        _config(tmp_path),
        ask=lambda name, detail: name != "web_gui",
    )
    text = "\n".join(message.content for message in session.messages)
    store.close()
    prompts = [
        item.get("content") or ""
        for batch in model.seen_messages
        for item in batch
        if "Current todo:" in (item.get("content") or "")
    ]
    assert (tmp_path / "projects" / "ledger" / "app.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert any("Last failure:" in prompt for prompt in prompts)
    assert "Done." in text


def test_undo_restores_the_snapshot(tmp_path) -> None:
    target = tmp_path / "a.py"
    target.write_text("one\n", encoding="utf-8")
    capture(tmp_path)
    target.write_text("two\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("new\n", encoding="utf-8")
    message = restore(tmp_path)
    assert "Restored" in message
    assert target.read_text(encoding="utf-8") == "one\n"
    assert not (tmp_path / "b.py").exists()


def test_a_silent_server_fails_the_health_gate(tmp_path) -> None:
    (tmp_path / "server.py").write_text("print('down')\n", encoding="utf-8")
    (tmp_path / "index.html").write_text("<html></html>\n", encoding="utf-8")
    problem = probe_health(tmp_path)
    assert problem.startswith("error:")
    assert "health" in problem or "stopped" in problem or "JSON" in problem


def test_a_second_message_is_queued(tmp_path) -> None:
    board = Board(_config(tmp_path), ScriptedModel([]))
    board.busy = True
    assert board.start_turn("next") is None
    assert board.store.queued(board.session) == ["next"]
    board.close()


def test_an_interrupted_reply_stays_in_the_session(tmp_path) -> None:
    class Down(ScriptedModel):
        def complete(self, messages, tools, on_delta=None):
            raise RuntimeError("down")

    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    with pytest.raises(RuntimeError):
        run_turn(
            store=store,
            session=session,
            user_text="hello",
            model=Down([]),
            config=_config(tmp_path),
            ask=lambda name, detail: False,
        )
    contents = [message.content for message in session.messages]
    store.close()
    assert "interrupted" in contents


def test_shell_still_asks_after_build_approval(tmp_path) -> None:
    allowed = _allow_writes(_config(tmp_path))
    assert allowed.permissions["shell"] == "ask"
    assert allowed.permissions["write_file"] == "allow"


def test_reads_from_one_step_both_return(tmp_path) -> None:
    (tmp_path / "a.py").write_text("alpha\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("beta\n", encoding="utf-8")
    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[
                    ToolCall(id="r1", name="read_file", arguments={"path": "a.py"}),
                    ToolCall(id="r2", name="read_file", arguments={"path": "b.py"}),
                ],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            _text("both"),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    result = run_turn(
        store=store,
        session=session,
        user_text="read both",
        model=model,
        config=_config(tmp_path),
        ask=lambda name, detail: False,
    )
    text = "\n".join(message.content for message in session.messages)
    store.close()
    assert result.text == "both"
    assert "alpha" in text
    assert "beta" in text


def test_the_page_shows_phase_todos_queue_and_undo() -> None:
    assert "Phase:" in PAGE
    assert "Todos:" in PAGE
    assert "Queue:" in PAGE
    assert ">Undo<" in PAGE or "Undo" in PAGE
