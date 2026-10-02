import subprocess
import sys
from pathlib import Path

from codeharness.config import HarnessConfig
from codeharness.eval.runner import discover_tasks, run_task
from codeharness.model import Completion, ToolCall
from tests.fakes import ScriptedModel


def test_fixtures_fail_before_the_agent() -> None:
    for task_dir in discover_tasks():
        completed = subprocess.run(
            [sys.executable, str(task_dir / "check.py")],
            cwd=task_dir,
            capture_output=True,
            text=True,
        )
        assert completed.returncode != 0


def test_add_function_passes_with_a_scripted_model() -> None:
    task_dir = next(path for path in discover_tasks() if path.name == "add_function")
    source = (task_dir / "math_utils.py").read_text(encoding="utf-8")
    updated = source.rstrip() + "\n\n\ndef add(a, b):\n    return a + b\n"
    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[ToolCall(id="r", name="read_file", arguments={"path": "math_utils.py"})],
                prompt_tokens=10,
                completion_tokens=5,
            ),
            Completion(
                content="",
                tool_calls=[
                    ToolCall(
                        id="e",
                        name="edit_file",
                        arguments={"path": "math_utils.py", "old_string": source, "new_string": updated},
                    )
                ],
                prompt_tokens=10,
                completion_tokens=5,
            ),
            Completion(content="Added add.", tool_calls=[], prompt_tokens=10, completion_tokens=5),
        ]
    )
    config = HarnessConfig(project_root=Path("."), model="test")
    result = run_task(task_dir, config, model)
    assert result.passed
    assert result.tool_calls == 2
    assert result.prompt_tokens == 30
    assert result.estimated is False


def test_gui_clock_passes_with_a_scripted_model() -> None:
    task_dir = next(path for path in discover_tasks() if path.name == "gui_clock")
    source = "import tkinter\n\ndef main():\n    root = tkinter.Tk()\n    root.mainloop()\n"
    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[ToolCall(id="w", name="write_file", arguments={"path": "clock.py", "content": source})],
                prompt_tokens=8,
                completion_tokens=4,
            ),
            Completion(content="Wrote clock.py.", tool_calls=[], prompt_tokens=6, completion_tokens=3),
        ]
    )
    result = run_task(task_dir, HarnessConfig(project_root=Path("."), model="test"), model)
    assert result.passed
    assert result.tool_calls == 1
