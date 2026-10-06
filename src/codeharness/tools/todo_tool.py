"""Read or close items on the project checklist."""

from __future__ import annotations

from pathlib import Path

from codeharness.config import HarnessConfig
from codeharness.todos import load_todos, mark_done, open_todos


def run(arguments: dict, root: Path, config: HarnessConfig) -> str:
    del config
    action = str(arguments.get("action") or "list")
    if action == "done":
        text = str(arguments.get("text") or "").strip()
        if not text:
            return "error: text is required"
        mark_done(root, text)
    items = load_todos(root)
    if not items:
        return "No todos."
    lines = []
    for item in items:
        mark = "x" if item.done else " "
        lines.append(f"- [{mark}] {item.text}")
    pending = open_todos(root)
    lines.append(f"Open: {len(pending)}")
    return "\n".join(lines)
