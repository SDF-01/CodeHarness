"""Checks the lead runs before a todo can finish."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from codeharness.languages import language_of
from codeharness.review import source_problem


def diagnose(paths: list[Path]) -> str:
    """Parse problems, plus node --check or javac when those tools exist."""
    problems: list[str] = []
    for path in paths:
        if not path.is_file():
            problems.append(f"error: {path.name} is missing.")
            continue
        problem = source_problem(path)
        if problem:
            problems.append(problem)
            continue
        extra = _external(path)
        if extra:
            problems.append(extra)
    return "\n".join(problems)


def diagnose_root(root: Path) -> str:
    paths = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and language_of(path) is not None and ".codeharness" not in path.parts
    ]
    return diagnose(paths)


def _external(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".js", ".mjs", ".cjs"} and shutil.which("node"):
        return _run(["node", "--check", str(path)], path)
    if suffix == ".java" and shutil.which("javac"):
        return _run(["javac", "-d", str(path.parent), str(path)], path)
    return ""


def _run(command: list[str], path: Path) -> str:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"error: {path.name} check failed. {exc}"
    if completed.returncode == 0:
        return ""
    detail = (completed.stderr or completed.stdout or "check failed").strip().splitlines()
    line = detail[-1] if detail else "check failed"
    return f"error: {path.name} failed to check. {line}"
