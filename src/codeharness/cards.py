"""Composer-style cards for tool results. Diffs stay short. The file keeps the full text."""

from __future__ import annotations

_PREVIEW = 16


def tool_card(name: str, arguments: dict, result: str) -> str:
    if result.startswith("error:") or result.startswith("denied:"):
        return f"{name}\n{result}"
    if name == "write_file":
        return f"Created {arguments.get('path') or 'file'}"
    if name == "edit_file":
        return _edit_card(arguments, result)
    if name == "read_file":
        preview = "\n".join(result.splitlines()[:_PREVIEW])
        return f"Read {arguments.get('path') or 'file'}\n{preview}"
    if name == "shell":
        tail = "\n".join(result.splitlines()[-_PREVIEW:])
        return f"Shell\n{arguments.get('command') or ''}\n{tail}"
    preview = "\n".join(result.splitlines()[:_PREVIEW])
    return f"{name}\n{preview}"


def _edit_card(arguments: dict, result: str) -> str:
    path = str(arguments.get("path") or "file")
    old = str(arguments.get("old_string") or "").splitlines()
    new = str(arguments.get("new_string") or "").splitlines()
    removed = "\n".join(f"- {line}" for line in old[:8])
    added = "\n".join(f"+ {line}" for line in new[:8])
    parts = [f"Edited {path}", result]
    if removed:
        parts.append(removed)
    if added:
        parts.append(added)
    return "\n".join(parts)
