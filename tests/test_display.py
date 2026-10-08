from io import StringIO
from pathlib import Path

from codeharness.config import HarnessConfig
from codeharness.display import (
    Console,
    approval_choice,
    approval_sentence,
    folder_choice,
    format_elapsed,
    format_status,
    meter_line,
    step_meter,
    progress_line,
)
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
    assert "█" in text
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


def test_repo_map_stays_off_the_screen() -> None:
    buffer = StringIO()
    view = Console(out=buffer, color=False)
    view.event(LoopEvent("status", "Files: a.py\nSuffixes: .py\nserver.py: no\nindex.html: no", title="Harness"))
    view.event(LoopEvent("status", "Local program. No website.", title="Harness"))
    view.event(LoopEvent("status", "Planning", title="Working"))
    text = buffer.getvalue()
    assert "Files:" not in text
    assert "No website" not in text
    assert "Harness" not in text
    assert "Planning" in text
    assert progress_line("Project folder: projects/atm") == "Project folder: projects/atm"


def test_approval_names_the_file_not_the_tool() -> None:
    assert approval_sentence("write_file", "atm_gui.py") == "Create atm_gui.py?"
    assert approval_sentence("shell", 'python atm_gui.py') == "Open atm_gui.py in a window?"
    assert approval_sentence("build_go", "projects/clock") == "Build this in projects/clock?"
    assert progress_line("Compiling.") == "Compiling."


def test_folder_choice_picks_a_number_or_the_new_folder(tmp_path) -> None:
    existing = tmp_path / "projects" / "atm"
    assert folder_choice("", [existing], "your-mom") == Path("your-mom")
    assert folder_choice("1", [existing], "your-mom") == existing
    assert folder_choice("2", [existing], "your-mom") == Path("your-mom")
    assert folder_choice("9", [existing], "your-mom") is None
    assert folder_choice("1", [existing], "") == existing
    assert folder_choice("", [existing], "") is None
    assert progress_line("Using that as the request.") == "Using that as the request."


def test_a_sentence_is_not_a_yes_or_a_no() -> None:
    assert approval_choice("y") == "yes"
    assert approval_choice("yup") == "yes"
    assert approval_choice("yeah") == "yes"
    assert approval_choice("no") == "no"
    assert approval_choice("what needs a yes") == "stop"
    assert approval_choice("you dont even know what youre building") == "stop"


def test_the_meter_counts_up_from_zero() -> None:
    assert format_elapsed(0) == "0s"
    assert format_elapsed(4.6) == "4s"
    assert format_elapsed(31) == "31s"
    assert 0 < step_meter(0, 60, 0) < 60
    creeping = step_meter(60, 60, 40)
    assert 60 < creeping < 80
    buffer = StringIO()
    view = Console(out=buffer, color=False)
    view.event(LoopEvent("meter", "60"))
    text = buffer.getvalue()
    assert "0s" in text
    assert "60%" in text


def test_a_build_meter_hides_the_source() -> None:
    buffer = StringIO()
    view = Console(out=buffer, color=False)
    source = "```python\nimport math\ndef add(x, y):\n    return x + y\n```"
    view.event(LoopEvent("meter", "8"))
    view.event(LoopEvent("delta", source))
    view.event(LoopEvent("answer", source, title="Result", body=source))
    view.event(LoopEvent("meter", "100"))
    text = buffer.getvalue()
    assert "import math" not in text
    assert "Thinking" not in text
    assert "Result" not in text
    assert "100%" in text
    assert meter_line(100, fancy=False).strip().endswith("100%")


def test_a_tool_name_is_not_a_progress_line() -> None:
    buffer = StringIO()
    view = Console(out=buffer, color=False)
    view.event(LoopEvent("tool", "todo", title="Tool todo", body="todo\nNo todos."))
    text = buffer.getvalue()
    assert "todo" not in text
    assert "Working" not in text
