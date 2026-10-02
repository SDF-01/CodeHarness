"""Plan, build, launch, and handoff. Chat and the local page share this."""

from __future__ import annotations

from codeharness.config import HarnessConfig
from codeharness.loop import EventHandler, LoopEvent, run_turn
from codeharness.model import ChatModel
from codeharness.permissions import AskFunc
from codeharness.projects import assign_project
from codeharness.review import brief_report
from codeharness.run import compile_and_launch, is_run_command
from codeharness.session import Session, SessionStore
from codeharness.web_prompt import WEB_GUI_QUESTION, apply_web_choice, offer_web_gui

_EMPTY_PLAN = "The model returned an empty reply."


def handle_turn(
    store: SessionStore,
    session: Session,
    text: str,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None = None,
) -> int:
    lowered = " ".join(text.strip().lower().split())
    if lowered in {"plan", "build"}:
        store.set_agent(session, lowered)
        message = (
            "Plan mode. I will not edit files."
            if lowered == "plan"
            else "Build mode. I can create and edit files."
        )
        _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
        return 0
    config, notice = assign_project(store, session, text, config)
    if notice:
        _emit(on_event, LoopEvent("status", notice, title="Harness", body=notice))
    if is_run_command(text):
        return launch_with_repair(store, session, model, config, ask, on_event)
    task = handoff_task(text)
    if task is not None:
        return handoff(store, session, task, model, config, ask, on_event)
    run_turn(
        store=store,
        session=session,
        user_text=_with_web_choice(text, ask, on_event),
        model=model,
        config=config,
        ask=ask,
        on_event=on_event,
    )
    return 0


def handoff_task(text: str) -> str | None:
    """Return the task after `handoff`, or None when this is not a handoff."""
    stripped = text.strip()
    lowered = " ".join(stripped.lower().split())
    if lowered == "handoff":
        return ""
    if lowered.startswith("handoff "):
        return stripped.split(None, 1)[1].strip()
    return None


def handoff(
    store: SessionStore,
    session: Session,
    task: str,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None,
) -> int:
    if not task:
        message = "Type handoff and the task. Example: handoff build a clock."
        _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
        return 0
    task = _with_web_choice(task, ask, on_event)
    store.set_agent(session, "plan")
    _emit(on_event, LoopEvent("status", "Planning. Files stay unchanged.", title="Harness", body="Planning. Files stay unchanged."))
    plan = run_turn(
        store=store,
        session=session,
        user_text=task,
        model=model,
        config=config,
        ask=ask,
        on_event=on_event,
    )
    if _plan_is_empty(plan.text):
        message = "The plan was empty. Build did not start."
        _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
        return 0
    store.set_agent(session, "build")
    _emit(on_event, LoopEvent("status", "Building from the plan.", title="Harness", body="Building from the plan."))
    run_turn(
        store=store,
        session=session,
        user_text=f"Implement this plan:\n{plan.text}",
        model=model,
        config=config,
        ask=ask,
        on_event=on_event,
    )
    return 0


def launch_with_repair(
    store: SessionStore,
    session: Session,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None,
) -> int:
    code, report = compile_and_launch(config)
    _emit(on_event, LoopEvent("run", brief_report(report), title="Run", body=brief_report(report)))
    if code == 0:
        return 0
    note = "That build is not viable. Reviewing the files and updating them."
    _emit(on_event, LoopEvent("answer", note, title="Result", body=note))
    run_turn(
        store=store,
        session=session,
        user_text=(
            "The Python does not compile. Fix the files in the project. "
            "Do not paste source in the answer.\n"
            + brief_report(report)
        ),
        model=model,
        config=config,
        ask=ask,
        on_event=on_event,
    )
    code, report = compile_and_launch(config)
    _emit(on_event, LoopEvent("run", brief_report(report), title="Run", body=brief_report(report)))
    return code


def _with_web_choice(text: str, ask, on_event: EventHandler | None) -> str:
    if not offer_web_gui(text):
        return text
    allowed = ask("web_gui", WEB_GUI_QUESTION)
    message = (
        "Realistic web app. HTML, CSS, React, Tailwind, and shadcn."
        if allowed
        else "Local program. No website."
    )
    _emit(on_event, LoopEvent("status", message, title="Harness", body=message))
    return apply_web_choice(text, allowed)


def _plan_is_empty(text: str) -> bool:
    cleaned = text.strip()
    if not cleaned or cleaned == _EMPTY_PLAN:
        return True
    return cleaned.startswith("Stopped after ")


def _emit(on_event: EventHandler | None, event: LoopEvent) -> None:
    if on_event is not None:
        on_event(event)
