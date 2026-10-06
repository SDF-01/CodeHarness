"""Apply several exact replacements in one call."""

from __future__ import annotations

from pathlib import Path

from codeharness.config import HarnessConfig
from codeharness.review import source_problem
from codeharness.tools.common import only_path_error, resolve_inside
from codeharness.tools.edit_file import _replace_once


def run(arguments: dict, root: Path, config: HarnessConfig) -> str:
    hunks = arguments.get("hunks")
    if not isinstance(hunks, list) or not hunks:
        return "error: hunks are required"
    planned: list[tuple[Path, str]] = []
    for hunk in hunks:
        if not isinstance(hunk, dict):
            return "error: each hunk must be an object"
        raw_path = hunk.get("path")
        old = hunk.get("old_string")
        new = hunk.get("new_string")
        if not isinstance(raw_path, str) or not raw_path:
            return "error: hunk path is required"
        if not isinstance(old, str) or old == "":
            return "error: hunk old_string must not be empty"
        if not isinstance(new, str):
            return "error: hunk new_string is required"
        path = resolve_inside(root, raw_path)
        limited = only_path_error(config, path, root)
        if limited:
            return limited
        if not path.is_file():
            return f"error: {raw_path} was not found"
        text = path.read_text(encoding="utf-8")
        updated = _replace_once(text, old, new)
        if updated is None:
            return f"error: old_string was not found in {raw_path}"
        planned.append((path, updated))
    names: list[str] = []
    for path, updated in planned:
        path.write_text(updated, encoding="utf-8", newline="")
        problem = source_problem(path)
        if problem:
            return problem
        names.append(path.relative_to(root.resolve()).as_posix())
    return "patched " + ", ".join(names)
