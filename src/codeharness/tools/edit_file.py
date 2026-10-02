"""Replace one exact snippet in a file."""

from __future__ import annotations

from pathlib import Path

from codeharness.config import HarnessConfig
from codeharness.review import source_problem
from codeharness.tools.common import require_str, resolve_inside


def run(arguments: dict, root: Path, config: HarnessConfig) -> str:
    del config
    path = resolve_inside(root, require_str(arguments, "path"))
    old = arguments.get("old_string")
    new = arguments.get("new_string")
    if not isinstance(old, str) or old == "":
        return "error: old_string must not be empty"
    if not isinstance(new, str):
        return "error: new_string is required"
    if not path.is_file():
        return "error: file not found"
    text = path.read_text(encoding="utf-8")
    updated = _replace_once(text, old, new)
    if updated is None:
        count = text.count(old)
        if count > 1:
            return "error: old_string matched more than once. Include more surrounding lines."
        return "error: old_string was not found"
    path.write_text(updated, encoding="utf-8", newline="")
    problem = source_problem(path)
    if problem:
        return problem
    return f"edited {path.relative_to(root.resolve())}"


def _replace_once(text: str, old: str, new: str) -> str | None:
    if text.count(old) == 1:
        return text.replace(old, new, 1)
    if "\r\n" not in old and "\n" in old:
        old_crlf = old.replace("\n", "\r\n")
        if text.count(old_crlf) == 1:
            return text.replace(old_crlf, new.replace("\n", "\r\n"), 1)
    if text.count(old) > 1:
        return None
    return None
