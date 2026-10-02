"""Run the same agent loop against tiny coding tasks and score token use."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from codeharness.config import HarnessConfig, config_for_eval
from codeharness.loop import TurnResult, run_turn
from codeharness.model import ChatModel
from codeharness.session import SessionStore, database_path

TASKS_DIR = Path(__file__).resolve().parent / "tasks"


@dataclass(frozen=True)
class EvalResult:
    name: str
    passed: bool
    detail: str
    prompt_tokens: int
    completion_tokens: int
    tool_calls: int
    estimated: bool


def discover_tasks() -> list[Path]:
    return sorted(path for path in TASKS_DIR.iterdir() if (path / "task.json").is_file())


def run_eval(config: HarnessConfig, model: ChatModel, task_name: str | None = None) -> list[EvalResult]:
    tasks = discover_tasks()
    if task_name is not None:
        tasks = [path for path in tasks if path.name == task_name]
        if not tasks:
            known = ", ".join(path.name for path in discover_tasks())
            raise KeyError(f"unknown task {task_name}. Known tasks: {known}")
    return [run_task(path, config, model) for path in tasks]


def run_task(task_dir: Path, config: HarnessConfig, model: ChatModel) -> EvalResult:
    spec = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    name = str(spec.get("name") or task_dir.name)
    prompt = str(spec["prompt"])
    with tempfile.TemporaryDirectory() as raw_dir:
        workdir = Path(raw_dir) / "project"
        shutil.copytree(task_dir, workdir)
        eval_config = _with_task_permissions(config_for_eval(config, workdir), spec)
        store = SessionStore(database_path(workdir))
        try:
            session = store.create(workdir)
            turn = run_turn(
                store=store,
                session=session,
                user_text=prompt,
                model=model,
                config=eval_config,
                ask=_deny,
            )
        finally:
            store.close()
        passed, detail = _run_check(workdir)
    return _result(name, passed, detail, turn)


def format_result(result: EvalResult) -> str:
    status = "pass" if result.passed else "fail"
    source = "estimated" if result.estimated else "api"
    line = (
        f"{result.name}: {status} "
        f"prompt_tokens={result.prompt_tokens} "
        f"completion_tokens={result.completion_tokens} "
        f"tool_calls={result.tool_calls} "
        f"tokens={source}"
    )
    if result.detail and not result.passed:
        return f"{line}\n{result.detail}"
    return line


def _run_check(workdir: Path) -> tuple[bool, str]:
    completed = subprocess.run(
        [sys.executable, str(workdir / "check.py")],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=30,
    )
    detail = (completed.stdout + completed.stderr).strip()[:500]
    return completed.returncode == 0, detail


def _result(name: str, passed: bool, detail: str, turn: TurnResult) -> EvalResult:
    return EvalResult(
        name=name,
        passed=passed,
        detail=detail,
        prompt_tokens=turn.usage.prompt_tokens,
        completion_tokens=turn.usage.completion_tokens,
        tool_calls=turn.usage.tool_calls,
        estimated=turn.usage.estimated,
    )


def _with_task_permissions(config: HarnessConfig, spec: dict) -> HarnessConfig:
    raw = spec.get("permissions", {})
    if not isinstance(raw, dict) or not raw:
        return config
    permissions = dict(config.permissions)
    for name, rule in raw.items():
        if rule in {"allow", "ask", "deny"}:
            permissions[str(name)] = str(rule)
    return replace(config, permissions=permissions)


def _deny(tool_name: str, detail: str) -> bool:
    del tool_name, detail
    return False
