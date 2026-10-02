"""Find short matching lines under the project root."""

from __future__ import annotations

from pathlib import Path

from codeharness.config import HarnessConfig
from codeharness.tools.common import SKIP_DIRS, cap_text, require_str, resolve_inside

MAX_HITS = 20
MAX_FILE_BYTES = 200_000


def run(arguments: dict, root: Path, config: HarnessConfig) -> str:
    query = require_str(arguments, "query")
    start = root.resolve()
    raw_path = arguments.get("path")
    if isinstance(raw_path, str) and raw_path:
        start = resolve_inside(root, raw_path)
    if not start.exists():
        return "error: path not found"
    hits: list[str] = []
    for path in _files(start, root.resolve()):
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\x00" in text:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if query not in line:
                continue
            snippet = line.strip()
            if len(snippet) > 160:
                snippet = snippet[:160] + "..."
            hits.append(f"{path.relative_to(root.resolve())}:{number}: {snippet}")
            if len(hits) >= MAX_HITS:
                return cap_text("\n".join(hits), config.max_tool_output_chars)
    if not hits:
        return "no matches"
    return cap_text("\n".join(hits), config.max_tool_output_chars)


def _files(start: Path, root: Path):
    if start.is_file():
        yield start
        return
    for path in start.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        yield path
