"""Copy project files before a write, and put them back on undo."""

from __future__ import annotations

import shutil
from pathlib import Path

from codeharness.tools.common import SKIP_DIRS

_DIR_NAME = "snapshot"


def snapshot_dir(root: Path) -> Path:
    return root / ".codeharness" / _DIR_NAME


def capture(root: Path) -> str:
    """Replace the saved snapshot with the files in the project right now."""
    dest = snapshot_dir(root)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in _files(root):
        rel = path.relative_to(root)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        count += 1
    return f"Snapshot saved ({count} files)."


def restore(root: Path) -> str:
    """Put the latest snapshot back. Files created after it are removed."""
    dest = snapshot_dir(root)
    if not dest.is_dir():
        return "error: no snapshot to undo"
    saved = {path.relative_to(dest) for path in _files(dest)}
    for path in list(_files(root)):
        rel = path.relative_to(root)
        if rel not in saved:
            path.unlink()
    for rel in saved:
        source = dest / rel
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return f"Restored {len(saved)} files."


def _files(root: Path) -> list[Path]:
    found: list[Path] = []
    if not root.is_dir():
        return found
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        found.append(path)
    return found
