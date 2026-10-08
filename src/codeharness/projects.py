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
_FILLER = {
    "lets",
    "let",
    "please",
    "can",
    "could",
    "would",
    "just",
    "now",
    "hey",
    "ok",
    "okay",
    "i",
    "we",
    "you",
    "wanna",
    "want",
    "need",
    "to",
}
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
_FRESH = re.compile(r"^(?:new project|new folder|switch project)\b[:\s-]*", re.IGNORECASE)
_TWEAK = re.compile(r"^(fix|change|update|edit)\b|^make\s+the\b")
_HELP = {"help", "help me", "commands", "what can you do", "what can i do", "show help"}
_LIST = {
    "list projects",
    "show projects",
    "my projects",
    "what projects",
    "which projects",
    "projects",
    "list folders",
}
_SWITCH = {
    "change project folder",
    "change folder",
    "change the folder",
    "switch project",
    "switch folder",
    "switch projects",
    "change project",
    "change the project",
    "open another project",
    "pick a project",
    "pick a folder",
    "go to another project",
    "another folder",
    "change projects",
    "switch the project",
    "navigate",
}
_DELETE_OPEN = {
    "delete this project",
    "delete the project",
    "remove this project",
    "delete the open project",
    "remove the open project",
    "delete current project",
}
_NEW_PROJECT = re.compile(
    r"^(?:new project|new folder|start a new project|create a new project|"
    r"make a new project|another project|start over)\b\s*(.*)$"
)
_NAMED_SWITCH = re.compile(
    r"^(?:switch to|open project|work on|go to|change to)\s+(?:the\s+|project\s+|folder\s+)?(.+)$"
)


def _opening(text: str) -> str:
    """Drop chatter in front of the verb. 'lets build a clock' starts at build."""
    words = re.findall(r"[a-z0-9]+", text.replace("'", "").split("\n", 1)[0].lower())
    while words and words[0] in _FILLER:
        words.pop(0)
    return " ".join(words)


def _spoken(text: str) -> str:
    """Lowercase words with the leading okay/please dropped. Punctuation is not a word."""
    words = re.findall(r"[a-z0-9]+", text.split("\n", 1)[0].lower())
    while words and words[0] in _FILLER:
        words.pop(0)
    return " ".join(words)


def project_act(text: str) -> tuple[str, str]:
    """What this sentence does to the project list. Empty means it is work on the open one.

    The action is help, list, switch, delete, or new. The rest is a project name or the task.
    """
    spoken = _spoken(text.split("\n\n", 1)[0])
    if not spoken:
        return "", ""
    if spoken in _HELP:
        return "help", ""
    if spoken in _LIST:
        return "list", ""
    if spoken in _SWITCH:
        return "switch", ""
    if spoken in _DELETE_OPEN:
        return "delete", ""
    named_delete = re.match(r"^(?:delete|remove)\s+project\s+(.+)$", spoken)
    if named_delete:
        return "delete", named_delete.group(1).strip()
    trailing_delete = re.match(r"^(?:delete|remove)\s+(?:the\s+)?(.+?)\s+project$", spoken)
    if trailing_delete:
        return "delete", trailing_delete.group(1).strip()
    fresh = _NEW_PROJECT.match(spoken)
    if fresh:
        return "new", fresh.group(1).strip()
    move = _NAMED_SWITCH.match(spoken)
    if move:
        return "switch", move.group(1).strip()
    named_switch = re.match(r"^switch project(?:\s+(.+))?$", spoken)
    if named_switch:
        return "switch", (named_switch.group(1) or "").strip()
    return "", ""


def starts_fresh(text: str) -> bool:
    """True when the user asked to leave the open project and build another one."""
    action, rest = project_act(text)
    if action == "new" and rest.strip():
        return True
    return _FRESH.match(text.strip()) is not None


def fresh_task(text: str) -> str:
    """Drop a leading new-project phrase. The rest is the task."""
    action, rest = project_act(text)
    tail = text.split("\n\n", 1)[1] if "\n\n" in text else ""
    if action == "new":
        body = rest.strip()
        if tail:
            return (body + "\n\n" + tail).strip()
        return body
    if "\n\n" in text:
        head, kept = text.split("\n\n", 1)
        if _FRESH.match(head.strip()):
            return _FRESH.sub("", head, count=1).strip() + "\n\n" + kept
    if _FRESH.match(text.strip()):
        return _FRESH.sub("", text.strip(), count=1).strip()
    return text


def task_slug(text: str) -> str:
    """Short folder name taken from a build request."""
    words = _opening(fresh_task(text)).split()
    while words and words[0] in _LEADING:
        words.pop(0)
    kept = [word for word in words if word not in _SKIP]
    slug = "-".join((kept or ["app"])[:3])[:40].strip("-") or "app"
    if slug in _RESERVED:
        return f"{slug}-app"
    return slug


def wants_new_folder(text: str, has_project: bool = False) -> bool:
    """True when this message starts a program, not a tweak of the current one."""
    if has_project and not starts_fresh(text):
        return False
    body = fresh_task(text)
    stripped = " ".join(body.strip().lower().split())
    opening = _opening(body)
    if has_project and is_tweak(stripped):
        return False
    if stripped in {"plan", "build"} or stripped in _RUN or opening in {"plan", "build"}:
        return False
    if _NEW.match(opening) is None:
        return False
    return task_slug(text).split("-")[0] not in _VAGUE


def is_tweak(text: str) -> bool:
    """True for a change to the program already open, such as make the button red."""
    stripped = " ".join(text.strip().lower().split())
    return _TWEAK.match(stripped) is not None


def project_dirs(workspace: Path) -> list[Path]:
    """Folders already created under projects/."""
    root = workspace / PROJECTS_DIR
    if not root.is_dir():
        return []
    found = [path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")]
    return sorted(found, key=lambda path: path.name.lower())


def assign_project(
    store: SessionStore,
    session: Session,
    text: str,
    config: HarnessConfig,
    choose=None,
) -> tuple[HarnessConfig, str]:
    """Point this turn at projects/<name>. Return a status line when the folder changes."""
    stripped = " ".join(text.strip().lower().split())
    if stripped in _RUN:
        folder = _resume_or_newest(store, session, config.project_root)
        if folder is None:
            return config, ""
        return _use(store, session, config, folder)

    current = store.work_dir(session)
    if wants_new_folder(text, has_project=bool(current)):
        slug = task_slug(fresh_task(text))
        if choose is not None:
            picked = choose(project_dirs(config.project_root), slug)
            if picked is None:
                return config, "No folder chosen."
            if picked.is_absolute():
                folder = picked
            else:
                folder = config.project_root / PROJECTS_DIR / picked.name
        else:
            folder = config.project_root / PROJECTS_DIR / slug
        folder.mkdir(parents=True, exist_ok=True)
        return _use(store, session, config, folder.resolve())

    current = store.work_dir(session)
    if not current:
        return config, ""
    folder = Path(current)
    if not folder.is_dir():
        return config, ""
    return _use(store, session, config, folder.resolve())


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


def match_project(folders: list[Path], name: str) -> Path | None:
    """The project folder named by the sentence, or None."""
    tokens = [part for part in re.findall(r"[a-z0-9]+", name.lower()) if part not in {"the", "a", "an", "project", "folder"}]
    if not tokens:
        return None
    wanted = "-".join(tokens)
    for folder in folders:
        if folder.name.lower() == wanted:
            return folder
    if len(tokens) == 1:
        for folder in folders:
            if folder.name.lower() == tokens[0]:
                return folder
    return None


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
    store.set_title(session, f"{PROJECTS_DIR}/{resolved.name}")
    return replace(config, project_root=resolved), notice
