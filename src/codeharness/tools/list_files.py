"""List a capped set of project files."""

from __future__ import annotations

from pathlib import Path

from codeharness.config import HarnessConfig
from codeharness.tools.common import SKIP_DIRS, cap_text, resolve_inside

MAX_FILES = 80


def run(arguments: dict, root: Path, config: HarnessConfig) -> str:
    raw_path = arguments.get("path") or "."
    if not isinstance(raw_path, str):
        return "error: path must be a string"
    start = resolve_inside(root, raw_path)
    if not start.is_dir():
        return "error: not a directory"
    root_resolved = root.resolve()
    found: list[str] = []
    truncated = False
    for path in sorted(start.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root_resolved)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        found.append(str(relative))
        if len(found) >= MAX_FILES:
            truncated = True
            break
    if not found:
        return "(no files)"
    body = "\n".join(found)
    if truncated:
        body += "\ntruncated file list"
    return cap_text(body, config.max_tool_output_chars)
