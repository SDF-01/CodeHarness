"""Sequential branches for a full stack task.

One local model is called one branch at a time. Each branch has its own session
so the page prompt does not carry the API tool log.
"""

from __future__ import annotations

from dataclasses import replace

from codeharness.config import HarnessConfig
from codeharness.loop import EventHandler, LoopEvent, run_turn
from codeharness.model import ChatModel
from codeharness.permissions import AskFunc
from codeharness.session import Session, SessionStore, StoredMessage
from codeharness.stack import stack_problem

_ORDER = ("page", "api", "review")
_STEPS = {"route": 2, "page": 4, "api": 4, "review": 4}
_LABELS = {"route": "Route", "page": "Page", "api": "API", "review": "Review"}
_BRIEFS = {
    "page": (
        "Write index.html in this folder. It must call /api/health. "
        "Use HTML, CSS, React, Tailwind, and shadcn-style components. "
        "Do not create a Python file."
    ),
    "api": (
        "Write server.py in this folder. Use http.server, serve this folder, "
        "and answer GET /api/health with JSON on port 8766."
    ),
    "review": (
        "Check the page and the API. Edit a file only if it is wrong. Do not create a new file."
    ),
}
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
    for name in _chosen(route_text):
        text = _branch(store, name, _BRIEFS[name], model, config, ask, on_event)
        _note(store, session, name, text, on_event)
        if _empty(text):
            _emit(on_event, LoopEvent("answer", _EMPTY[name], title="Result", body=_EMPTY[name]))
            return 0
    problem = stack_problem(config.project_root, task)
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
    )
    return result.text


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
