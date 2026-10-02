"""Run an approved build command with its working directory set to the project root."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from codeharness.config import HarnessConfig
from codeharness.tools.common import cap_text, require_str

_CHAIN = set("|;&><`")
_INTERPRETER_NAMES = {
    "python",
    "python.exe",
    "py",
    "py.exe",
    "java",
    "java.exe",
    "javac",
    "javac.exe",
    "node",
    "node.exe",
    "npm",
    "npm.cmd",
    "npx",
    "npx.cmd",
}

MAX_COMMAND_CHARS = 4000

# These ship with Python. pip cannot install them, and trying wastes a model step.
_STDLIB = {
    "collections",
    "dataclasses",
    "datetime",
    "functools",
    "itertools",
    "json",
    "math",
    "os",
    "pathlib",
    "re",
    "sqlite3",
    "subprocess",
    "sys",
    "tkinter",
    "turtle",
    "typing",
    "unittest",
    "venv",
}


def run(arguments: dict, root: Path, config: HarnessConfig) -> str:
    command = require_str(arguments, "command")
    if len(command) > MAX_COMMAND_CHARS:
        return "error: command is too long"
    blocked = _blocked_stdlib_install(command)
    if blocked:
        return blocked
    blocked = _sandbox_problem(command)
    if blocked:
        return blocked
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=root.resolve(),
            capture_output=True,
            text=True,
            timeout=config.shell_timeout,
        )
    except subprocess.TimeoutExpired:
        return f"error: command timed out after {config.shell_timeout:g} seconds"
    body = (completed.stdout or "") + (("\n" + completed.stderr) if completed.stderr else "")
    if not body.strip():
        body = "(no output)"
    return cap_text(body.rstrip("\n"), config.max_tool_output_chars) + f"\nexit_code={completed.returncode}"


def _sandbox_problem(command: str) -> str | None:
    """Allow Python, Java, and Node. Block other programs and chained commands."""
    if _has_unquoted_chain(command):
        return "error: command cannot chain programs."
    token = _first_token(command)
    if not token:
        return "error: command is required"
    if _is_interpreter(token):
        return None
    return (
        "error: that command is blocked. "
        "Python, Java, Node, npm, and npx can run. Other programs are blocked."
    )


def _has_unquoted_chain(command: str) -> bool:
    if "\n" in command or "\r" in command:
        return True
    quote = ""
    for char in command:
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char in _CHAIN:
            return True
    return False


def _first_token(command: str) -> str:
    text = command.lstrip()
    if not text:
        return ""
    if text[0] in {'"', "'"}:
        quote = text[0]
        end = text.find(quote, 1)
        if end < 0:
            return ""
        return text[1:end]
    return text.split(maxsplit=1)[0]


def _is_interpreter(token: str) -> bool:
    name = Path(token).name.lower()
    if name in _INTERPRETER_NAMES or name == Path(sys.executable).name.lower():
        return True
    try:
        return Path(token).expanduser().resolve() == Path(sys.executable).resolve()
    except OSError:
        return False


def _blocked_stdlib_install(command: str) -> str | None:
    lowered = command.lower()
    if "pip" not in lowered or "install" not in lowered:
        return None
    tokens = [part.strip("'\"") for part in command.replace("=", " ").replace(",", " ").split()]
    lower_tokens = [part.lower() for part in tokens]
    if "install" not in lower_tokens:
        return None
    for token in lower_tokens[lower_tokens.index("install") + 1 :]:
        if not token or token.startswith("-"):
            continue
        name = token.split("[", 1)[0]
        if name in _STDLIB:
            return (
                f"error: {name} is already part of Python. "
                "Run the program with the current Python. Do not pip install it."
            )
    return None
