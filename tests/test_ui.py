import time

from codeharness.config import HarnessConfig
from codeharness.model import Completion, ToolCall
from codeharness.ui import Board
from tests.fakes import ScriptedModel


def test_page_shows_a_tool_step_and_waits_for_approval(tmp_path) -> None:
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
                prompt_tokens=12,
                completion_tokens=4,
            ),
            Completion(content="updated", tool_calls=[], prompt_tokens=20, completion_tokens=3),
        ]
    )
    board = Board(HarnessConfig(project_root=tmp_path, model="test"), model)
    problem = board.start_turn("change the note")
    assert problem is None
    deadline = time.time() + 5
    while time.time() < deadline:
        if board.snapshot()["pending"]:
            break
        time.sleep(0.02)
    pending = board.snapshot()["pending"]
    assert pending is not None
    assert pending["tool"] == "edit_file"
    assert board.decide(True) is None
    deadline = time.time() + 5
    while time.time() < deadline and board.snapshot()["busy"]:
        time.sleep(0.02)
    state = board.snapshot()
    board.close()
    kinds = [event["kind"] for event in state["events"]]
    assert "user" in kinds
    assert "tool" in kinds
    assert "answer" in kinds
    assert state["latest_prompt"] == 20
    assert target.read_text(encoding="utf-8") == "new\n"
    assert not state["busy"]


def _wait(board) -> None:
    deadline = time.time() + 20
    while time.time() < deadline and board.snapshot()["busy"]:
        time.sleep(0.02)


def test_page_plan_build_and_launch(tmp_path) -> None:
    (tmp_path / "app.py").write_text("print('from-page')\n", encoding="utf-8")
    board = Board(HarnessConfig(project_root=tmp_path, model="test", shell_timeout=15), ScriptedModel([]))
    assert board.start_turn("plan") is None
    _wait(board)
    assert board.snapshot()["agent"] == "plan"
    assert board.start_turn("build") is None
    _wait(board)
    assert board.snapshot()["agent"] == "build"
    assert board.start_turn("launch") is None
    _wait(board)
    state = board.snapshot()
    board.close()
    text = "\n".join(event["text"] for event in state["events"])
    assert "from-page" in text
    assert not state["busy"]
