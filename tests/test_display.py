from io import StringIO

from codeharness.config import HarnessConfig
from codeharness.display import Console, approval_sentence, format_status
from codeharness.loop import LoopEvent, run_turn
from codeharness.model import Completion, ToolCall
from codeharness.session import SessionStore, database_path
from tests.fakes import ScriptedModel


def test_transcript_prints_prompt_then_harness_then_ollama(tmp_path) -> None:
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
    buffer = StringIO()
    view = Console(out=buffer, color=False)
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    view.prompt_block("what is in a.py")
    run_turn(
        store=store,
        session=session,
        user_text="what is in a.py",
        model=model,
        config=HarnessConfig(project_root=tmp_path, model="qwen2.5-coder:7b"),
        ask=lambda name, detail: False,
        on_event=view.event,
    )
    store.close()
    text = buffer.getvalue()
    prompt_at = text.index("> Prompt")
    working_at = text.index("Working")
    reading_at = text.index("Reading a.py")
    answer_at = text.index("Result")
    assert prompt_at < working_at < reading_at < answer_at
    assert "Tool read_file" not in text
    assert "The file says alpha." in text
    assert "prompt=" not in text


def test_banner_names_the_path() -> None:
    buffer = StringIO()
    view = Console(out=buffer, color=False)
    view.banner(HarnessConfig(model="qwen2.5-coder:7b"), "abc123")
    text = buffer.getvalue()
    assert "CODEHARNESS" in text
    assert "Available Tools" in text
    assert "Available Skills" in text
    assert "qwen2.5-coder:7b" in text
    assert "session abc123" in text
    assert "/help" in text
    assert "\033[" not in text


def test_token_lines_stay_out_of_the_blocks() -> None:
    buffer = StringIO()
    view = Console(out=buffer, color=False)
    view.event(LoopEvent("tokens", "tokens: prompt=1 completion=1 tool_calls=0 source=api"))
    view.event(LoopEvent("tokens", "turn tokens: prompt=1 completion=1 tool_calls=0 source=api"))
    text = buffer.getvalue()
    assert "prompt=1" not in text


def test_status_bar_shortens_below_52_columns() -> None:
    wide = format_status(
        model="qwen2.5-coder:7b",
        used=100,
        limit=1000,
        phase="running",
        title="clock",
        estimated=True,
        width=80,
    )
    compact = format_status(
        model="qwen2.5-coder:7b",
        used=100,
        limit=1000,
        phase="running",
        title="clock",
        estimated=False,
        width=60,
    )
    narrow = format_status(
        model="qwen2.5-coder:7b",
        used=100,
        limit=1000,
        phase="review",
        title="clock",
        estimated=False,
        width=40,
    )
    assert "clock" in wide
    assert "~100/1000" in wide
    assert "clock" not in compact
    assert "100/1000" in compact
    assert narrow == "qwen2.5-coder:7b  review"


def test_approval_names_the_file_not_the_tool() -> None:
    assert approval_sentence("write_file", "atm_gui.py") == "Create atm_gui.py?"
    assert approval_sentence("shell", 'python atm_gui.py') == "Open atm_gui.py in a window?"
