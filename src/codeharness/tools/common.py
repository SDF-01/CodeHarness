"""Shared path checks and output caps for tools."""

from __future__ import annotations

from pathlib import Path

from codeharness.errors import PathEscape, ToolInputError

SKIP_DIRS = {".git", "__pycache__", ".codeharness", "node_modules", ".venv", "venv", ".pytest_cache"}


def resolve_inside(root: Path, raw: str) -> Path:
    root_resolved = root.resolve()
    raw_path = Path(raw)
    candidate = raw_path.resolve() if raw_path.is_absolute() else (root_resolved / raw_path).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise PathEscape("path is outside the project root")
    return candidate


def cap_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


def require_str(arguments: dict, field: str) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or not value:
        raise ToolInputError(f"{field} is required")
    return value


def optional_int(arguments: dict, field: str) -> int | None:
    if field not in arguments or arguments[field] is None:
        return None
    value = arguments[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolInputError(f"{field} must be an integer")
    return value
