"""Read a line range from a file inside the project root."""

from __future__ import annotations

from pathlib import Path

from codeharness.config import HarnessConfig
from codeharness.errors import ToolInputError
from codeharness.tools.common import cap_text, optional_int, require_str, resolve_inside

DEFAULT_WINDOW = 200


def run(arguments: dict, root: Path, config: HarnessConfig) -> str:
    path = resolve_inside(root, require_str(arguments, "path"))
    if not path.is_file():
        return "error: file not found"
    text = path.read_text(encoding="utf-8", errors="replace")
    if "\x00" in text:
        return "error: file looks binary"
    lines = text.splitlines()
    start = optional_int(arguments, "start_line") or 1
    end = optional_int(arguments, "end_line")
    if start < 1:
        raise ToolInputError("start_line must be at least 1")
    if end is None:
        end = start + DEFAULT_WINDOW - 1
    if end < start:
        raise ToolInputError("end_line must be greater than or equal to start_line")
    selected = lines[start - 1 : end]
    shown_end = start + len(selected) - 1 if selected else start - 1
    body = "\n".join(f"{number}|{line}" for number, line in enumerate(selected, start))
    rel = path.relative_to(root.resolve())
    header = f"{rel} lines {start}-{shown_end} of {len(lines)}"
    return cap_text(f"{header}\n{body}", config.max_tool_output_chars)
