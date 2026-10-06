"""Sequential branches for a full stack task.

One local model is called one branch at a time. Each branch has its own session
so the page prompt does not carry the API tool log.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from codeharness.config import HarnessConfig
from codeharness.diagnostics import diagnose
from codeharness.loop import EventHandler, LoopEvent, run_turn
from codeharness.model import ChatModel
from codeharness.permissions import AskFunc
from codeharness.repomap import repo_map
from codeharness.run import compile_and_launch
from codeharness.scaffold import write_stack_skeleton
from codeharness.session import Session, SessionStore, StoredMessage
from codeharness.stack import probe_health, stack_problem
from codeharness.taskrecord import add_evidence, behavior_note
from codeharness.todos import mark_done, parse_plan, save_todos

_ORDER = ("page", "api", "review")
_STEPS = {"route": 2, "page": 4, "api": 4, "review": 4}
_LABELS = {"route": "Route", "page": "Page", "api": "API", "review": "Review"}
_BRIEFS = {
    "page": (
        "Write index.html in this folder. "
        "The page must implement the user's request, not only call /api/health. "
        "/api/health is a readiness check. "
        "Use HTML, CSS, React, Tailwind, and shadcn-style components. "
        "Do not create a Python file."
    ),
    "api": (
        "Write server.py in this folder. Use http.server, serve this folder, "
        "and answer GET /api/health with JSON on port 8766. "
        "GET /api/health is a readiness check. Implement the behavior in the user request."
    ),
    "review": (
        "Check the page and the API against the user request. "
        "A health response is not enough. "
        "Edit a file only if it is wrong. Do not create a new file."
    ),
}
_FILES = {"page": "index.html", "api": "server.py", "review": "index.html"}
_EMPTY = {
    "route": "The route was empty. Branches did not start.",
    "page": "The page branch was empty. The API did not start.",
    "api": "The API branch was empty. Review did not start.",
    "review": "The review branch was empty.",
}


def run_stack(
    store: SessionStore,
    session: Session,
    task: str,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None,
) -> int:
    """Route, then page, API, and review, in the same project folder."""
    store.append(session, StoredMessage(role="user", content=task))
    route_text = _branch(store, "route", f"Name the branches for this task. Use page, api, and review.\n\n{task}", model, config, ask, on_event)
    _note(store, session, "route", route_text, on_event)
    if _empty(route_text):
        _emit(on_event, LoopEvent("answer", _EMPTY["route"], title="Result", body=_EMPTY["route"]))
        return 0
    names = _chosen(route_text)
    _save_plan(config.project_root, names)
    skeleton = write_stack_skeleton(config.project_root)
    if skeleton:
        _emit(on_event, LoopEvent("status", skeleton, title="Harness", body=skeleton))
    failure = ""
    for name in names:
        brief = branch_brief(name, task)
        if failure:
            brief = f"{brief}\n{failure}"
        text = _branch(
            store,
            name,
            brief,
            model,
            config,
            ask,
            on_event,
            note=_worker_note(task, config.project_root),
        )
        _note(store, session, name, text, on_event)
        if _empty(text):
            _emit(on_event, LoopEvent("answer", _EMPTY[name], title="Result", body=_EMPTY[name]))
            return 0
        failure = _check_branch(config.project_root, name)
        if not failure:
            mark_done(config.project_root, f"{name}: {_FILES[name]}")
    problem = _prove(store, session, task, model, config, ask, on_event)
    if problem:
        message = f"Stack failed. {problem}"
        store.append(session, StoredMessage(role="assistant", content=message))
        _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
    return 0


def _branch(
    store: SessionStore,
    name: str,
    brief: str,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None,
    note: str = "",
) -> str:
    child = store.create(config.project_root)
    store.set_agent(child, name)
    label = _LABELS[name]
    _emit(on_event, LoopEvent("status", f"{label} branch.", title="Harness", body=f"{label} branch."))
    result = run_turn(
        store=store,
        session=child,
        user_text=brief,
        model=model,
        config=replace(config, max_steps=_STEPS[name]),
        ask=ask,
        on_event=on_event,
        note=note,
    )
    return result.text


def _save_plan(root, names: list[str]) -> None:
    lines = [f"- [ ] {name}: {_FILES[name]}" for name in names]
    text = "\n".join(lines) + "\n"
    (root / "PLAN.md").write_text(text, encoding="utf-8")
    save_todos(root, parse_plan(text))


def _check_branch(root, name: str) -> str:
    owned = {
        "page": ["index.html"],
        "api": ["server.py"],
        "review": ["index.html", "server.py"],
    }
    paths = [root / item for item in owned[name] if (root / item).is_file()]
    if not paths:
        return f"error: missing {_FILES[name]}"
    return diagnose(paths)


def _prove(store, session, task, model, config, ask, on_event) -> str:
    problem = stack_problem(config.project_root, task) or probe_health(config.project_root)
    if problem:
        _branch(
            store,
            "review",
            "Health check failed. Edit the file that is wrong. Do not create a new file.\n" + problem,
            model,
            config,
            ask,
            on_event,
            note=_worker_note(task, config.project_root),
        )
        problem = stack_problem(config.project_root, task) or probe_health(config.project_root)
    if problem:
        add_evidence(config.project_root, f"failed: {problem}")
        return problem
    add_evidence(config.project_root, "passed: GET /api/health")
    if config.open_windows:
        _code, report = compile_and_launch(config)
        message = report.strip() or "Health passed."
    else:
        message = "Health passed."
    store.append(session, StoredMessage(role="assistant", content=message))
    _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
    return ""


def branch_brief(name: str, task: str) -> str:
    """Page, API, and review each see the user's request. Health stays a readiness check."""
    user = task.split("\n\n", 1)[0].strip()
    parts = [_BRIEFS[name], f"User request: {user}"]
    extra = behavior_note(task)
    if extra:
        parts.append(extra)
    return "\n".join(parts)


def _worker_note(task: str, root: Path) -> str:
    """Every branch sees the original request, not only its file name."""
    extra = behavior_note(task)
    tail = f"\n{extra}" if extra and extra not in task else ""
    return (
        f"Requirements:\n{task.strip()}\n\n"
        "Acceptance: the finished program must meet the requirements above."
        f"{tail}\n\n"
        f"{repo_map(root)}"
    )


def _chosen(text: str) -> list[str]:
    lowered = text.lower()
    picked = [name for name in _ORDER if name in lowered]
    return picked or list(_ORDER)


def _note(store: SessionStore, session: Session, name: str, text: str, on_event: EventHandler | None) -> None:
    line = f"{_LABELS[name]}: {text.strip()}"
    store.append(session, StoredMessage(role="assistant", content=line))
    _emit(on_event, LoopEvent("answer", line, title="Result", body=line))


def _empty(text: str) -> bool:
    cleaned = text.strip()
    if not cleaned or cleaned == "The model returned an empty reply.":
        return True
    return cleaned.startswith("Stopped after ")


def _emit(on_event: EventHandler | None, event: LoopEvent) -> None:
    if on_event is not None:
        on_event(event)
