"""Each generated program lives in its own folder under projects/."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from codeharness.config import HarnessConfig
from codeharness.session import Session, SessionStore

PROJECTS_DIR = "projects"
_PROGRAM_SUFFIXES = {".py", ".java", ".html", ".htm"}
_LEADING = {"handoff", "build", "create", "make", "design", "write", "implement"}
_SKIP = {
    "a",
    "an",
    "the",
    "me",
    "my",
    "please",
    "app",
    "application",
    "program",
    "script",
    "project",
    "simple",
    "small",
    "new",
    "local",
    "using",
    "with",
    "and",
    "for",
    "to",
    "of",
    "in",
    "on",
}
_VAGUE = {"it", "this", "that", "them", "one", "something"}
_RESERVED = {"con", "prn", "aux", "nul", "com1", "com2", "com3", "com4", "lpt1", "lpt2", "lpt3"}
_RUN = {"run", "run it", "launch", "launch it"}
_NEW = re.compile(r"^(handoff\s+)?(build|create|make|design|write|implement)\b")


def task_slug(text: str) -> str:
    """Short folder name taken from a build request."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    while words and words[0] in _LEADING:
        words.pop(0)
    kept = [word for word in words if word not in _SKIP]
    slug = "-".join((kept or ["app"])[:3])[:40].strip("-") or "app"
    if slug in _RESERVED:
        return f"{slug}-app"
    return slug


def wants_new_folder(text: str) -> bool:
    """True when this message starts a program, not a tweak of the current one."""
    stripped = " ".join(text.strip().lower().split())
    if stripped in {"plan", "build"} or stripped in _RUN:
        return False
    if _NEW.match(stripped) is None:
        return False
    return task_slug(stripped).split("-")[0] not in _VAGUE


def assign_project(
    store: SessionStore,
    session: Session,
    text: str,
    config: HarnessConfig,
) -> tuple[HarnessConfig, str]:
    """Point this turn at projects/<name>. Return a status line when the folder changes."""
    stripped = " ".join(text.strip().lower().split())
    if stripped in _RUN:
        folder = _resume_or_newest(store, session, config.project_root)
        if folder is None:
            return config, ""
        return _use(store, session, config, folder)

    if wants_new_folder(text):
        folder = (config.project_root / PROJECTS_DIR / task_slug(text)).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        return _use(store, session, config, folder)

    current = store.work_dir(session)
    if not current:
        return config, ""
    folder = Path(current)
    if not folder.is_dir():
        return config, ""
    return replace(config, project_root=folder.resolve()), ""


def launch_root(workspace: Path) -> Path | None:
    """Folder to launch when the workspace is the harness and programs live under projects/."""
    if not (workspace / "src" / "codeharness").is_dir():
        return None
    return newest_program_dir(workspace / PROJECTS_DIR)


def newest_program_dir(projects: Path) -> Path | None:
    if not projects.is_dir():
        return None
    programs: list[Path] = []
    for child in projects.iterdir():
        if not child.is_dir():
            continue
        programs.extend(
            path
            for path in child.iterdir()
            if path.is_file() and path.suffix.lower() in _PROGRAM_SUFFIXES and not path.name.startswith("test_")
        )
    if not programs:
        return None
    return max(programs, key=lambda path: path.stat().st_mtime).parent


def _resume_or_newest(store: SessionStore, session: Session, workspace: Path) -> Path | None:
    current = store.work_dir(session)
    if current and Path(current).is_dir():
        return Path(current)
    return newest_program_dir(workspace / PROJECTS_DIR)


def _use(
    store: SessionStore,
    session: Session,
    config: HarnessConfig,
    folder: Path,
) -> tuple[HarnessConfig, str]:
    resolved = folder.resolve()
    notice = ""
    if store.work_dir(session) != str(resolved):
        store.set_work_dir(session, resolved)
        notice = f"Project folder: {PROJECTS_DIR}/{resolved.name}"
    return replace(config, project_root=resolved), notice
