"""The harness leads. The model works one todo at a time."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from codeharness.branches import run_stack
from codeharness.config import HarnessConfig
from codeharness.diagnostics import diagnose, diagnose_root
from codeharness.loop import EventHandler, LoopEvent, run_turn
from codeharness.model import ChatModel
from codeharness.permissions import AskFunc
from codeharness.repomap import repo_map
from codeharness.scaffold import write_stack_skeleton
from codeharness.session import Session, SessionStore, StoredMessage
from codeharness.snapshot import capture
from codeharness.stack import expects_stack, probe_health
from codeharness.todos import Todo, load_todos, mark_done, parse_plan, save_todos
from codeharness.web_prompt import apply_kind, kind_question, task_kind

_EMPTY = "The model returned an empty reply."
_FILE = re.compile(r"[A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+")
_BUILD = {"build", "create", "make", "design"}


def is_lead_task(text: str, root: Path) -> bool:
    """A new program goes through the lead. A named existing file stays one turn."""
    stripped = " ".join(text.strip().lower().split())
    if stripped in {"plan", "build", "undo", "exit", "quit"} or stripped.startswith("/"):
        return False
    if _names_existing_file(text, root):
        return False
    words = set(re.findall(r"[a-z0-9]+", stripped))
    if words & _BUILD:
        return True
    return expects_stack(text)


def lead_stack(
    store: SessionStore,
    session: Session,
    task: str,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None = None,
) -> int:
    """Snapshot the folder, then run the page, API, and review branches."""
    capture(config.project_root)
    return run_stack(store, session, task, model, config, ask, on_event)


def run_lead(
    store: SessionStore,
    session: Session,
    text: str,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None = None,
    reply=None,
) -> int:
    task = text
    question = kind_question(task)
    if question and reply is not None:
        _phase(store, session, "question", on_event)
        task = apply_kind(task, reply(question))
    elif task_kind(task):
        task = apply_kind(task, task)
    if session.title == "new session" and task.strip():
        store.set_title(session, task.strip().splitlines()[0][:60])
    store.append(session, StoredMessage(role="user", content=task))
    root = config.project_root
    _phase(store, session, "map", on_event)
    if expects_stack(task) and not (root / "server.py").is_file():
        capture(root)
        notice = write_stack_skeleton(root)
        if notice:
            _emit(on_event, LoopEvent("status", notice, title="Harness", body=notice))
    _phase(store, session, "plan", on_event)
    _emit(on_event, LoopEvent("status", "Planning", title="Working", body="Planning"))
    plan_text = _write_plan(store, model, config, ask, on_event, task, root)
    if _plan_is_empty(plan_text):
        message = "The plan was empty. Build did not start."
        _finish(store, session, message, on_event)
        return 0
    _phase(store, session, "approval", on_event)
    if not ask("build_go", plan_text[:500]):
        _finish(store, session, "Build did not start.", on_event)
        return 0
    items = parse_plan(plan_text)
    save_todos(root, items)
    failure = ""
    for item in items:
        if item.done:
            continue
        capture(root)
        done, failure = _work_todo(store, session, model, config, ask, on_event, root, item, failure)
        if not done:
            _finish(store, session, f"Stopped on: {item.text}", on_event)
            return 0
        mark_done(root, item.text)
        _drain(store, session, model, config, ask, on_event)
    if (root / "server.py").is_file():
        _phase(store, session, "review", on_event)
        problem = probe_health(root)
        if problem:
            _finish(store, session, problem, on_event)
            return 0
    _remember(root, failure)
    names = ", ".join(item.text for item in load_todos(root)) or "the plan"
    _finish(store, session, f"Done. Checked: {names}", on_event)
    return 0


def _write_plan(
    store: SessionStore,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None,
    task: str,
    root: Path,
) -> str:
    child = store.create(root)
    store.set_agent(child, "plan")
    prompt = (
        "Write PLAN.md only. Use checklist lines that start with - [ ]. "
        "Name the file each item will change.\n\n"
        f"Repo map:\n{repo_map(root)}\n\nTask:\n{task}"
    )
    result = run_turn(
        store=store,
        session=child,
        user_text=prompt,
        model=model,
        config=config,
        ask=ask,
        on_event=on_event,
        note=repo_map(root),
    )
    path = root / "PLAN.md"
    if not path.is_file() or not path.read_text(encoding="utf-8").strip():
        if _plan_is_empty(result.text):
            return ""
        path.write_text(result.text.strip() + "\n", encoding="utf-8")
    return path.read_text(encoding="utf-8")


def _work_todo(
    store: SessionStore,
    session: Session,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None,
    root: Path,
    item: Todo,
    failure: str,
) -> tuple[bool, str]:
    prompt = _todo_prompt(root, item, failure)
    for _attempt in range(3):
        _phase(store, session, "running", on_event)
        worker = store.create(root)
        store.set_agent(worker, "general")
        run_turn(
            store=store,
            session=worker,
            user_text=prompt,
            model=model,
            config=_allow_writes(config),
            ask=ask,
            on_event=on_event,
            note=prompt,
        )
        report = _check_todo(root, item)
        if report:
            failure = report
            prompt = _todo_prompt(root, item, failure)
            continue
        _phase(store, session, "review", on_event)
        reviewer = store.create(root)
        store.set_agent(reviewer, "review")
        reviewed = run_turn(
            store=store,
            session=reviewer,
            user_text=f"Review this todo. If it is fine, answer exactly: No problems.\n{item.text}",
            model=model,
            config=config,
            ask=ask,
            on_event=on_event,
            note=prompt,
        )
        if _review_failed(reviewed.text):
            failure = reviewed.text
            prompt = _todo_prompt(root, item, failure)
            continue
        return True, failure
    return False, failure


def _todo_prompt(root: Path, item: Todo, failure: str) -> str:
    lines = [
        f"Repo map:\n{repo_map(root)}",
        f"Current todo:\n{item.text}",
        "Do this todo only. Do not repeat the rest of the plan.",
    ]
    if failure:
        lines.append(f"Last failure:\n{failure}")
    return "\n\n".join(lines)


def _check_todo(root: Path, item: Todo) -> str:
    missing = [name for name in item.files() if not (root / name).is_file()]
    if missing:
        return "error: missing " + ", ".join(missing)
    paths = [root / name for name in item.files()]
    if paths:
        return diagnose(paths)
    return diagnose_root(root)


def _review_failed(text: str) -> bool:
    lowered = text.lower()
    if "no problems" in lowered:
        return False
    return "error:" in lowered or lowered.startswith("problem")


def _allow_writes(config: HarnessConfig) -> HarnessConfig:
    permissions = dict(config.permissions)
    for name in ("write_file", "edit_file", "apply_patch"):
        if permissions.get(name) != "deny":
            permissions[name] = "allow"
    return replace(config, permissions=permissions)


def _remember(root: Path, failure: str) -> None:
    files = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and ".codeharness" not in path.parts and path.name != "PLAN.md"
    )
    path = root / "AGENTS.md"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = ["", "Verified by CodeHarness.", "Files: " + (", ".join(files) or "none") + "."]
    lines.append("Checks: diagnostics, review.")
    if (root / "server.py").is_file():
        lines.append("Checks: GET /api/health returned JSON.")
    if failure:
        lines.append("Last fix: " + " ".join(failure.split())[:180])
    path.write_text(existing.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


def _drain(
    store: SessionStore,
    session: Session,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None,
) -> None:
    from codeharness.commands import handle_turn

    while True:
        nxt = store.dequeue(session)
        if not nxt:
            return
        _emit(on_event, LoopEvent("status", f"Queued: {nxt}", title="Harness", body=f"Queued: {nxt}"))
        handle_turn(store, session, nxt, model, config, ask, on_event)


def _plan_is_empty(text: str) -> bool:
    cleaned = text.strip()
    if not cleaned or cleaned == _EMPTY:
        return True
    return cleaned.startswith("Stopped after ")


def _names_existing_file(text: str, root: Path) -> bool:
    base = root.resolve()
    for token in _FILE.findall(text):
        candidate = (base / token).resolve()
        if candidate != base and base not in candidate.parents:
            continue
        if candidate.is_file():
            return True
    return False


def _finish(store: SessionStore, session: Session, message: str, on_event: EventHandler | None) -> None:
    _phase(store, session, "ready", on_event)
    store.append(session, StoredMessage(role="assistant", content=message))
    _emit(on_event, LoopEvent("answer", message, title="Result", body=message))


def _phase(store: SessionStore, session: Session, name: str, on_event: EventHandler | None) -> None:
    store.set_phase(session, name)
    _emit(on_event, LoopEvent("phase", name, title="Harness", body=name))


def _emit(on_event: EventHandler | None, event: LoopEvent) -> None:
    if on_event is not None:
        on_event(event)
