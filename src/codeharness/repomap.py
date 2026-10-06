"""A short picture of the project for a child prompt."""

from __future__ import annotations

from pathlib import Path

from codeharness.tools.common import SKIP_DIRS

_LIMIT = 40


def repo_map(root: Path) -> str:
    """Entry files, suffixes, and up to 40 paths. Skips the harness database."""
    files: list[Path] = []
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            files.append(path)
            if len(files) >= _LIMIT:
                break
    names = [path.relative_to(root).as_posix() for path in files]
    suffixes = ", ".join(sorted({path.suffix.lower() for path in files if path.suffix}) or ["none"])
    shown = ", ".join(names) if names else "(empty)"
    server = "yes" if (root / "server.py").is_file() else "no"
    page = "yes" if (root / "index.html").is_file() else "no"
    return f"Files: {shown}\nSuffixes: {suffixes}\nserver.py: {server}\nindex.html: {page}"
