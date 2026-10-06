"""Create or replace a whole file inside the project."""

from __future__ import annotations

from pathlib import Path

from codeharness.config import HarnessConfig
from codeharness.languages import language_of
from codeharness.review import normalize_source, source_problem
from codeharness.tools.common import only_path_error, require_str, resolve_inside


def run(arguments: dict, root: Path, config: HarnessConfig) -> str:
    path = resolve_inside(root, require_str(arguments, "path"))
    limited = only_path_error(config, path, root)
    if limited:
        return limited
    content = arguments.get("content")
    if not isinstance(content, str):
        return "error: content is required"
    if path.exists() and not path.is_file():
        return "error: path is a directory"
    if language_of(path) is not None:
        content = normalize_source(content)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")
    problem = source_problem(path)
    if problem:
        return problem
    lines = 0 if content == "" else content.count("\n") + (0 if content.endswith("\n") else 1)
    return f"wrote {path.relative_to(root.resolve())} ({lines} lines)"
