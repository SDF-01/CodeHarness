"""Plan, build, launch, and handoff. Chat and the local page share this."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from codeharness.catalog import skill_catalog, skill_count, tool_catalog, tool_count
from codeharness.config import HarnessConfig
from codeharness.context import estimate_messages
from codeharness.lead import is_lead_task, lead_stack, run_lead
from codeharness.loop import EventHandler, LoopEvent, run_turn
from codeharness.repomap import repo_map
from codeharness.model import ChatModel
from codeharness.permissions import AskFunc
from codeharness.playbook import is_chat
from codeharness.projects import assign_project, is_tweak
from codeharness.review import brief_report
from codeharness.run import compile_and_launch, is_run_command
from codeharness.session import Session, SessionStore
from codeharness.snapshot import capture, restore
from codeharness.stack import expects_stack
from codeharness.todos import open_todos
from codeharness.tools import TOOLS
from codeharness.web_prompt import apply_kind, kind_question, task_kind

_EMPTY_PLAN = "The model returned an empty reply."


def handle_turn(
    store: SessionStore,
    session: Session,
    text: str,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None = None,
    reply=None,
) -> int:
    lowered = " ".join(text.strip().lower().split())
    if lowered.startswith("/"):
        return _slash(store, session, lowered, config, on_event)
    if lowered in {"undo"}:
        return _undo(store, session, config, on_event)
    if lowered in {"plan", "build"}:
        store.set_agent(session, lowered)
        message = (
            "Plan mode. I may write only PLAN.md."
            if lowered == "plan"
            else "Build mode. I can create and edit files."
        )
        _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
        return 0
    if is_chat(lowered):
        run_turn(
            store=store,
            session=session,
            user_text=text,
            model=model,
            config=_chat_config(config),
            ask=ask,
            on_event=on_event,
            note=(
                "This message is conversation. Answer in one or two sentences. "
                "Do not call tools. Do not create, edit, or open files."
            ),
        )
        return 0
    config, notice = assign_project(store, session, text, config)
    if notice:
        _emit(on_event, LoopEvent("status", notice, title="Harness", body=notice))
    if is_run_command(text):
        return launch_with_repair(store, session, model, config, ask, on_event)
    task = handoff_task(text)
    if task is not None:
        return handoff(store, session, task, model, config, ask, on_event, reply)
    if is_lead_task(text, config.project_root):
        return run_lead(store, session, text, model, config, ask, on_event, reply)
    chosen = _with_web_choice(text, on_event, reply)
    if expects_stack(chosen):
        return lead_stack(store, session, chosen, model, config, ask, on_event)
    note, turn_config = _follow_up(text, notice, config)
    run_turn(
        store=store,
        session=session,
        user_text=chosen,
        model=model,
        config=turn_config,
        ask=ask,
        on_event=on_event,
        note=note,
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
    reply=None,
) -> int:
    if not task:
        message = "Type handoff and the task. Example: handoff build a clock."
        _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
        return 0
    task = _with_web_choice(task, on_event, reply)
    if expects_stack(task):
        return lead_stack(store, session, task, model, config, ask, on_event)
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


def _follow_up(text: str, notice: str, config: HarnessConfig) -> tuple[str, HarnessConfig]:
    """A tweak stays in the current folder, with the repo map and edit-only writes."""
    if notice or not is_tweak(text):
        return "", config
    capture(config.project_root)
    note = (
        repo_map(config.project_root)
        + "\nEdit the existing file that matches this request. Do not start a new program. "
        + "Use edit_file unless the file is missing."
    )
    return note, _edits_only(config)


def _chat_config(config: HarnessConfig) -> HarnessConfig:
    """A greeting gets no tools, so the model cannot create or open a file."""
    permissions = {name: "deny" for name in TOOLS}
    permissions["doom_loop"] = "deny"
    return replace(config, permissions=permissions)


def _edits_only(config: HarnessConfig) -> HarnessConfig:
    root = config.project_root
    if not root.is_dir() or not any(path.is_file() for path in root.iterdir()):
        return config
    permissions = dict(config.permissions)
    if permissions.get("write_file") != "deny":
        permissions["write_file"] = "deny"
    return replace(config, permissions=permissions)


def _with_web_choice(text: str, on_event: EventHandler | None, reply) -> str:
    question = kind_question(text)
    if question and reply is not None:
        return apply_kind(text, reply(question))
    if task_kind(text):
        return apply_kind(text, text)
    return text


def _plan_is_empty(text: str) -> bool:
    cleaned = text.strip()
    if not cleaned or cleaned == _EMPTY_PLAN:
        return True
    return cleaned.startswith("Stopped after ")


def _slash(
    store: SessionStore,
    session: Session,
    lowered: str,
    config: HarnessConfig,
    on_event: EventHandler | None,
) -> int:
    command = lowered.split()[0]
    if command in {"/plan", "/build"}:
        name = command[1:]
        store.set_agent(session, name)
        message = (
            "Plan mode. I may write only PLAN.md."
            if name == "plan"
            else "Build mode. I can create and edit files."
        )
        _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
        return 0
    if command == "/undo":
        return _undo(store, session, config, on_event)
    if command == "/help":
        body = (
            "/help  commands\n"
            "/tools  tool groups\n"
            "/skills  skill groups\n"
            "/status  model, phase, todos\n"
            "/context  token budget\n"
            "/plan  look, and write only PLAN.md\n"
            "/build  create files again\n"
            "/undo  restore the latest snapshot\n"
            "/sessions  saved sessions"
        )
    elif command == "/tools":
        body = tool_catalog()
    elif command == "/skills":
        body = skill_catalog()
    elif command == "/status":
        root = _work_root(store, session, config)
        pending = ", ".join(item.text for item in open_todos(root)) or "none"
        body = (
            f"model {config.model}\n"
            f"profile local\n"
            f"phase {store.get_phase(session)}\n"
            f"open todos {pending}"
        )
    elif command == "/context":
        messages = [{"role": "system", "content": "budget"}]
        used = estimate_messages(messages)
        body = (
            f"system ~{used}\n"
            f"tools {tool_count()}\n"
            f"skills {skill_count()}\n"
            f"history {len(session.messages)}\n"
            f"free {max(config.prompt_budget - used, 0)} of {config.prompt_budget}"
        )
    elif command == "/sessions":
        rows = store.list_sessions()
        body = "\n".join(f"{row.id}  {row.title}" for row in rows) or "no sessions"
    else:
        body = "Unknown command. Type /help."
    _emit(on_event, LoopEvent("answer", body, title="Result", body=body))
    return 0


def _undo(
    store: SessionStore,
    session: Session,
    config: HarnessConfig,
    on_event: EventHandler | None,
) -> int:
    message = restore(_work_root(store, session, config))
    _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
    return 0


def _work_root(store: SessionStore, session: Session, config: HarnessConfig) -> Path:
    current = store.work_dir(session)
    if current and Path(current).is_dir():
        return Path(current)
    return config.project_root


def _emit(on_event: EventHandler | None, event: LoopEvent) -> None:
    if on_event is not None:
        on_event(event)
