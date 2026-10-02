"""Read-only git inspection, plus add and commit. No push."""

from __future__ import annotations

import subprocess
from pathlib import Path

from codeharness.config import HarnessConfig
from codeharness.tools.common import cap_text, require_str, resolve_inside


def status(arguments: dict, root: Path, config: HarnessConfig) -> str:
    del arguments
    return _git(root, ["status", "--short"], config)


def diff(arguments: dict, root: Path, config: HarnessConfig) -> str:
    args = ["diff"]
    raw = arguments.get("path")
    if isinstance(raw, str) and raw.strip():
        path = resolve_inside(root, raw)
        args.extend(["--", path.relative_to(root.resolve()).as_posix()])
    return _git(root, args, config)


def add(arguments: dict, root: Path, config: HarnessConfig) -> str:
    path = resolve_inside(root, require_str(arguments, "path"))
    if not path.exists():
        return "error: path does not exist"
    relative = path.relative_to(root.resolve()).as_posix()
    return _git(root, ["add", "--", relative], config)


def commit(arguments: dict, root: Path, config: HarnessConfig) -> str:
    message = require_str(arguments, "message")
    return _git(root, ["commit", "-m", message], config)


def _git(root: Path, args: list[str], config: HarnessConfig) -> str:
    if not (root.resolve() / ".git").exists():
        return "error: this folder is not a git repository"
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root.resolve(),
            capture_output=True,
            text=True,
            timeout=config.shell_timeout,
        )
    except FileNotFoundError:
        return "error: git is not installed"
    except subprocess.TimeoutExpired:
        return f"error: git timed out after {config.shell_timeout:g} seconds"
    body = (completed.stdout or "") + (("\n" + completed.stderr) if completed.stderr else "")
    if not body.strip():
        body = "(no output)"
    return cap_text(body.rstrip("\n"), config.max_tool_output_chars) + f"\nexit_code={completed.returncode}"
