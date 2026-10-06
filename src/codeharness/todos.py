"""Checklist parsed from PLAN.md and stored beside the project."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_FILE = re.compile(r"[A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+")


@dataclass
class Todo:
    text: str
    done: bool = False

    def files(self) -> list[str]:
        return _FILE.findall(self.text)


def todo_path(root: Path) -> Path:
    return root / ".codeharness" / "todos.json"


def parse_plan(text: str) -> list[Todo]:
    """Checklist lines become todos. A plan with no boxes is one todo."""
    items: list[Todo] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("- [x]") or line.startswith("- [X]"):
            body = line[5:].strip()
            if body:
                items.append(Todo(text=body, done=True))
            continue
        if line.startswith("- [ ]"):
            body = line[5:].strip()
            if body:
                items.append(Todo(text=body, done=False))
            continue
        if line.startswith("- "):
            body = line[2:].strip()
            if body:
                items.append(Todo(text=body, done=False))
    if items:
        return items
    cleaned = text.strip()
    if not cleaned:
        return []
    return [Todo(text=cleaned)]


def save_todos(root: Path, items: list[Todo]) -> None:
    path = todo_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [{"text": item.text, "done": item.done} for item in items]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_todos(root: Path) -> list[Todo]:
    path = todo_path(root)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    items: list[Todo] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if text:
            items.append(Todo(text=text, done=bool(row.get("done"))))
    return items


def open_todos(root: Path) -> list[Todo]:
    return [item for item in load_todos(root) if not item.done]


def mark_done(root: Path, text: str) -> None:
    items = load_todos(root)
    for item in items:
        if item.text == text:
            item.done = True
    save_todos(root, items)
