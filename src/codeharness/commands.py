"""Plan, build, launch, and handoff. Chat and the local page share this."""

from __future__ import annotations

import shutil
import time
from dataclasses import replace
from pathlib import Path

from codeharness.catalog import skill_catalog, skill_count, tool_catalog, tool_count
from codeharness.config import HarnessConfig
from codeharness.context import estimate_messages
from codeharness.errors import TurnStopped
from codeharness.lead import lead_stack
from codeharness.loop import EventHandler, LoopEvent, run_turn
from codeharness.repomap import repo_map
from codeharness.model import ChatModel
from codeharness.permissions import AskFunc
from codeharness.playbook import design_body, is_chat
from codeharness.projects import (
    PROJECTS_DIR,
    assign_project,
    fresh_task,
    is_tweak,
    match_project,
    project_act,
    project_dirs,
    task_slug,
    wants_new_folder,
)
from codeharness.review import brief_report
from codeharness.run import compile_and_launch, is_run_command
from codeharness.session import Session, SessionStore
from codeharness.snapshot import capture, restore
from codeharness.stack import expects_stack
from codeharness.taskrecord import (
    begin_task,
    behavior_note,
    check_clause,
    close_faults,
    close_known_faults,
    fault_brief,
    file_versions,
    load_task,
    probe_logic,
    save_faults,
    settle_task,
    troubleshoot,
)
from codeharness.todos import open_todos
from codeharness.tools import TOOLS
from codeharness.web_prompt import apply_kind, design_memory, kind_question, product_note, remember_design, task_kind

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
    choose=None,
    _depth: int = 0,
) -> int:
    try:
        return _handle_turn(store, session, text, model, config, ask, on_event, reply, choose)
    except TurnStopped as stopped:
        request = str(stopped).strip()
        if _depth >= 1 or not request or request == "stop":
            message = "Stopped. Say what you want built."
            _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
            return 0
        if _qualifies(request):
            request = text.rstrip() + "\n\n" + request
        action, _rest = project_act(request)
        if action not in {"help", "list", "switch", "delete", "new"}:
            _emit(
                on_event,
                LoopEvent(
                    "status",
                    "Using that as the request.",
                    title="Working",
                    body="Using that as the request.",
                ),
            )
        return handle_turn(
            store,
            session,
            request,
            model,
            config,
            ask,
            on_event,
            reply,
            choose,
            _depth + 1,
        )


def _handle_turn(
    store: SessionStore,
    session: Session,
    text: str,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None = None,
    reply=None,
    choose=None,
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
    action, rest = project_act(text)
    if action == "help":
        return _slash(store, session, "/help", config, on_event)
    if action == "list":
        return _list_projects(store, session, config, on_event)
    if action == "switch":
        return _switch_project(store, session, config, on_event, choose, rest)
    if action == "delete":
        return _delete_project(store, session, config, ask, on_event, rest)
    if action == "new" and not rest.strip():
        message = "Say what the new project should be. Example: new project build an ATM."
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
    if _continues(lowered):
        return _continue_work(store, session, text, model, config, ask, on_event, choose)
    if is_run_command(text):
        return _launch_requested(store, session, model, config, ask, on_event, choose)
    if _wants_verify(lowered):
        return _verify_open(store, session, config, on_event, choose)
    task = handoff_task(text)
    if task is not None and not task:
        return handoff(store, session, task, model, config, ask, on_event, reply)
    source = task if task is not None else text
    already = bool(store.work_dir(session))
    fresh = wants_new_folder(source, has_project=already)
    chosen = _with_web_choice(source, on_event, reply, has_project=already)
    if fresh:
        chosen = fresh_task(chosen)
    placed = _assign(store, session, source if fresh else chosen, config, choose, on_event)
    if placed is None:
        return 0
    config, notice = placed
    if task is not None:
        return handoff(store, session, chosen, model, config, ask, on_event, reply)
    if expects_stack(chosen):
        _meter(on_event, "10")
        _meter(on_event, "30")
        begin_task(config.project_root, chosen)
        lead_stack(store, session, chosen, model, config, ask, on_event)
        _settle(config.project_root, session)
        remember_design(config.project_root, chosen)
        _show_verdict(config.project_root, on_event, opened=False)
        return 0
    open_project = already and not fresh
    product = _saved_request(config.project_root) if open_project else chosen
    if not product:
        product = chosen
    note, turn_config = _follow_up(text, notice, config)
    note = _with_project(note, config.project_root)
    note = _with_behavior(note, product)
    note = _with_design(note, config.project_root)
    mode = _construction(chosen, turn_config, open_project=open_project)
    armed = _arm_build(mode, turn_config, ask, product)
    declined = _declined(turn_config, armed, mode)
    if mode == "build":
        note = _build_note(note, product)
    elif mode == "update":
        note = _update_note(note, product)
    if mode:
        _meter(on_event, "10")
        _meter(on_event, "30")
        _meter(on_event, "60")
    begin_task(config.project_root, chosen)
    stages = _Stages() if mode else None
    result = run_turn(
        store=store,
        session=session,
        user_text=chosen,
        model=model,
        config=_build_turn(armed) if mode else armed,
        ask=ask,
        on_event=on_event,
        note=note,
        focus=bool(mode),
    )
    if stages is not None:
        stages.lap("write")
    settle_task(config.project_root, result.text, result.checks)
    if declined:
        message = "Stopped. Nothing was changed."
        _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
        return 0
    code = _finish_build(
        store,
        session,
        model,
        replace(armed, open_windows=turn_config.open_windows),
        ask,
        on_event,
        mode,
        product,
        stages,
    )
    if mode:
        remember_design(config.project_root, product)
        _show_verdict(
            config.project_root,
            on_event,
            opened=code == 0 and turn_config.open_windows,
            timing="" if stages is None else stages.text(),
        )
    return code


def _assign(store, session, text, config, choose, on_event):
    """Point the turn at a folder. None means the user cancelled."""
    config, notice = assign_project(store, session, text, config, choose)
    if notice == "No folder chosen.":
        _emit(on_event, LoopEvent("answer", notice, title="Result", body=notice))
        return None
    if notice:
        _emit(on_event, LoopEvent("status", notice, title="Harness", body=notice))
    return config, notice


_BUILD_STEPS = 24
_BUILD_NOTE = (
    "Write this program only. Ignore every older program in the chat. "
    "Write logic.py first, with the behavior from the request. "
    "Then write app.py that imports logic and builds the ttk shell. "
    "Do not answer until both files exist. "
    "The window is the product. Do not ask for a command. Do not show Unknown command. "
    "Use a dark background, a display, and at least four buttons. "
    "Every button calls logic.py and updates the display. "
    "Use write_file. Do not paste source and do not print a JSON tool call."
)
_CONTINUE = {"do it", "do that", "go ahead", "finish it", "finish", "make it", "write it", "build it"}


def _meter(on_event: EventHandler | None, value: str) -> None:
    _emit(on_event, LoopEvent("meter", value, title="meter", body=value))


def _qualifies(answer: str) -> bool:
    """A yes or no with more words keeps the program and adds the correction."""
    lowered = answer.strip().lower()
    return lowered.startswith("yes") or lowered.startswith("no")


def _corrects(text: str) -> bool:
    lowered = text.lower()
    return "not a " in lowered or "not in the browser" in lowered or "instead of" in lowered


def _wants_desktop(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in ("desktop", "plugin", "tkinter", "browser"))


def _continues(text: str) -> bool:
    if text in _CONTINUE:
        return True
    if "not running" in text or "did not launch" in text or "didn't launch" in text:
        return True
    return _corrects(text)


def _build_note(note: str, request: str = "") -> str:
    text = (note.rstrip() + "\n" + _BUILD_NOTE).strip()
    extra = product_note(request)
    if extra:
        text = text + "\n" + extra
    return text


class _Stages:
    """How long each part of a build took."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self.mark = self.started
        self.rows: list[tuple[str, float]] = []

    def lap(self, name: str) -> None:
        now = time.monotonic()
        self.rows.append((name, now - self.mark))
        self.mark = now

    def text(self) -> str:
        parts = [f"{name} {_fmt_seconds(seconds)}" for name, seconds in self.rows]
        parts.append(f"total {_fmt_seconds(time.monotonic() - self.started)}")
        return " ".join(parts)


def _fmt_seconds(seconds: float) -> str:
    if seconds < 10:
        return f"{seconds:.1f}s"
    return f"{int(round(seconds))}s"


def _last_request(session: Session) -> str:
    for message in reversed(session.messages):
        if message.role != "user" or not wants_new_folder(message.content):
            continue
        return message.content
    return ""


def _workspace(config: HarnessConfig) -> Path:
    root = config.project_root
    if (root / "src" / "codeharness").is_dir() or (root / PROJECTS_DIR).is_dir():
        return root
    if root.parent.name == PROJECTS_DIR:
        return root.parent.parent
    return root


def _continue_work(
    store: SessionStore,
    session: Session,
    text: str,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None,
    choose,
) -> int:
    """Finish the program already requested, in its folder, then launch that program."""
    prior = _last_request(session)
    if not prior:
        message = "Say what you want built."
        _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
        return 0
    task = prior
    if _corrects(text) or _wants_desktop(text):
        task = prior.rstrip() + "\n\n" + text.strip()
        if _wants_desktop(text) and "mainloop" not in task:
            task = apply_kind(task, "desktop plugin")
    folder = _workspace(config) / PROJECTS_DIR / task_slug(prior)
    folder.mkdir(parents=True, exist_ok=True)
    resolved = folder.resolve()
    store.set_work_dir(session, resolved)
    config = replace(config, project_root=resolved)
    if _has_program(resolved) and not _corrects(text):
        _emit(
            on_event,
            LoopEvent(
                "status",
                f"Project folder: {PROJECTS_DIR}/{resolved.name}",
                title="Working",
                body=f"Project folder: {PROJECTS_DIR}/{resolved.name}",
            ),
        )
        return launch_with_repair(store, session, model, config, ask, on_event)
    mode = "update" if _has_program(resolved) else "build"

    def approved(name: str, detail: str) -> bool:
        if name == "build_go":
            return True
        return ask(name, detail)

    armed = _arm_build(mode, config, approved, task)
    _meter(on_event, "10")
    _meter(on_event, "60")
    begin_task(config.project_root, task)
    stages = _Stages()
    result = run_turn(
        store=store,
        session=session,
        user_text=task,
        model=model,
        config=_build_turn(armed),
        ask=approved,
        on_event=on_event,
        note=_with_design(_build_note(behavior_note(task), task), config.project_root),
        focus=True,
    )
    settle_task(config.project_root, result.text, result.checks)
    stages.lap("write")
    code = _finish_build(
        store,
        session,
        model,
        replace(armed, open_windows=config.open_windows),
        approved,
        on_event,
        mode,
        task,
        stages,
    )
    remember_design(config.project_root, task)
    _show_verdict(
        config.project_root,
        on_event,
        opened=code == 0 and config.open_windows,
        timing=stages.text(),
    )
    return code


def _launch_requested(
    store: SessionStore,
    session: Session,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None,
    choose,
) -> int:
    """Launch the open project. A chat slug is only a fallback when no folder is open."""
    current = store.work_dir(session)
    if current and Path(current).is_dir():
        resolved = Path(current).resolve()
        config = replace(config, project_root=resolved)
        _emit(
            on_event,
            LoopEvent(
                "status",
                f"Project folder: {PROJECTS_DIR}/{resolved.name}",
                title="Working",
                body=f"Project folder: {PROJECTS_DIR}/{resolved.name}",
            ),
        )
        return launch_with_repair(store, session, model, config, ask, on_event)
    prior = _last_request(session)
    if prior:
        folder = _workspace(config) / PROJECTS_DIR / task_slug(prior)
        if not folder.is_dir() or not _has_program(folder):
            return _continue_work(store, session, "do it", model, config, ask, on_event, choose)
        resolved = folder.resolve()
        store.set_work_dir(session, resolved)
        config = replace(config, project_root=resolved)
        _emit(
            on_event,
            LoopEvent(
                "status",
                f"Project folder: {PROJECTS_DIR}/{resolved.name}",
                title="Working",
                body=f"Project folder: {PROJECTS_DIR}/{resolved.name}",
            ),
        )
        return launch_with_repair(store, session, model, config, ask, on_event)
    config, _notice = assign_project(store, session, "launch it", config, choose)
    return launch_with_repair(store, session, model, config, ask, on_event)


def _settle(root: Path, session: Session) -> None:
    text = session.messages[-1].content if session.messages else ""
    settle_task(root, text)


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
    _meter(on_event, "10")
    _meter(on_event, "30")
    begin_task(config.project_root, task)
    if expects_stack(task):
        lead_stack(store, session, task, model, config, ask, on_event)
        _settle(config.project_root, session)
        remember_design(config.project_root, task)
        _show_verdict(config.project_root, on_event, opened=False)
        return 0
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
        note=_with_design(
            "\n".join(part for part in (behavior_note(task), product_note(task)) if part),
            config.project_root,
        ),
    )
    if _plan_is_empty(plan.text):
        message = "The plan was empty. Build did not start."
        _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
        settle_task(config.project_root, message, ["failed: the plan was empty"])
        _meter(on_event, "failed")
        return 0
    store.set_agent(session, "build")
    _emit(on_event, LoopEvent("status", "Building from the plan.", title="Harness", body="Building from the plan."))
    mode = _construction(task, config)
    armed = _arm_build(mode, config, ask, task)
    declined = _declined(config, armed, mode)
    stages = _Stages()
    built = run_turn(
        store=store,
        session=session,
        user_text=f"Implement this plan:\n{plan.text}",
        model=model,
        config=_build_turn(armed) if mode else armed,
        ask=ask,
        on_event=on_event,
        note=_with_design(_build_note(behavior_note(task), task), config.project_root),
    )
    settle_task(config.project_root, built.text, built.checks)
    stages.lap("write")
    if declined:
        message = "Stopped. Nothing was changed."
        _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
        return 0
    code = _finish_build(store, session, model, armed, ask, on_event, mode, task, stages)
    remember_design(config.project_root, task)
    _show_verdict(
        config.project_root,
        on_event,
        opened=code == 0 and config.open_windows,
        timing=stages.text(),
    )
    return code


def launch_with_repair(
    store: SessionStore,
    session: Session,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None,
) -> int:
    """Launch the open program. A failure is a fault to fix, not the end of the run."""
    stages = _Stages()
    record = load_task(config.project_root)
    request = str(record.get("request") or "") if record else ""
    code = _until_it_runs(store, session, model, config, ask, on_event, request, stages)
    _show_verdict(config.project_root, on_event, opened=code == 0, timing=stages.text())
    return code


def _construction(text: str, config: HarnessConfig, *, open_project: bool = False) -> str:
    """A new program is a build. A change inside an open project is an update."""
    if open_project:
        return "update"
    if wants_new_folder(text):
        return "build"
    if is_tweak(text) and config.project_root.parent.name == PROJECTS_DIR:
        return "update"
    return ""


def _saved_request(root: Path) -> str:
    """The product the open folder was built for. Later sentences do not replace it."""
    record = load_task(root)
    if not record:
        return ""
    return str(record.get("request") or "")


def _update_note(note: str, request: str) -> str:
    """Keep the original product card. The new sentence is only the change."""
    extra = product_note(request)
    if not extra:
        return note
    return (note.rstrip() + "\n" + extra).strip()


def _declined(before: HarnessConfig, after: HarnessConfig, mode: str) -> bool:
    """True when the user said no to the build. A no does not launch the open program."""
    if not mode:
        return False
    asked = before.permissions.get("write_file") == "ask" or before.permissions.get("edit_file") == "ask"
    return asked and after.permissions.get("write_file") == "deny"


def _arm_build(mode: str, config: HarnessConfig, ask: AskFunc, request: str = "") -> HarnessConfig:
    """One yes covers the file writes. No stops the turn before anything launches."""
    if not mode:
        return config
    permissions = dict(config.permissions)
    if mode == "build":
        needs_yes = permissions.get("write_file") == "ask"
    else:
        needs_yes = permissions.get("write_file") == "ask" or permissions.get("edit_file") == "ask"
    if needs_yes:
        folder = f"{PROJECTS_DIR}/{config.project_root.name}"
        if ask("build_go", _build_yes(mode, request, folder)):
            for name in ("write_file", "edit_file", "apply_patch"):
                if permissions.get(name) != "deny":
                    permissions[name] = "allow"
        else:
            for name in ("write_file", "edit_file", "apply_patch"):
                permissions[name] = "deny"
            permissions["shell"] = "deny"
            return replace(config, permissions=permissions)
    for name, rule in list(permissions.items()):
        if name in {"shell", "git_add", "git_commit"}:
            continue
        if rule != "deny":
            permissions[name] = "allow"
    permissions["shell"] = "deny"
    return replace(config, permissions=permissions)


def _build_yes(mode: str, request: str, folder: str) -> str:
    """One build yes, with the kind and the checks this request named."""
    labels = {
        "web": "a website",
        "desktop": "a desktop",
        "cli": "a command line",
        "api": "an API",
        "game": "a game",
    }
    label = labels.get(task_kind(request), "")
    subject = task_slug(request).replace("-", " ")
    verb = "Update" if mode == "update" else "Build"
    if label and subject and subject != "app":
        head = f"{verb} {label} {subject} in {folder}?"
    else:
        head = f"{verb} this in {folder}?"
    return f"{head} {check_clause(request)}"


def _with_design(note: str, root: Path) -> str:
    """Design rules and the last design memory. Callers must not print this."""
    parts = [note.strip(), design_body()]
    memory = design_memory(root)
    if memory:
        parts.append("Earlier design:\n" + memory)
    return "\n".join(part for part in parts if part)


def _show_verdict(root: Path, on_event: EventHandler | None, opened: bool, timing: str = "") -> None:
    """One line from task.json. The model sentence is not this line."""
    state = _task_state(root)
    if state == "failed":
        cause = _open_cause(root)
        line = f"failed: logic. {cause}" if cause else "failed: the build failed."
        _meter(on_event, "failed")
    elif opened:
        line = f"{state}: the window is open."
        _meter(on_event, "100")
    else:
        line = f"{state}: the build finished."
        _meter(on_event, "100" if state != "failed" else "failed")
    if timing:
        line = f"{line}\n{timing}"
    _emit(on_event, LoopEvent("answer", line, title="Result", body=line))


def _task_state(root: Path) -> str:
    record = load_task(root)
    if record is None:
        return "unverified"
    states = [str(item.get("state") or "") for item in record.get("requirements", [])]
    if any(item == "failed" for item in states):
        return "failed"
    if states and all(item == "verified" for item in states):
        return "verified"
    return states[-1] if states else "unverified"


def _with_behavior(note: str, request: str) -> str:
    extra = behavior_note(request)
    if not extra:
        return note
    if not note:
        return extra
    return note.rstrip() + "\n" + extra


def _build_turn(config: HarnessConfig) -> HarnessConfig:
    """One construction turn may write both modules. Chat and eval keep the default."""
    return replace(config, open_windows=False, max_steps=_BUILD_STEPS)


def _finish_build(
    store: SessionStore,
    session: Session,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None,
    mode: str,
    request: str = "",
    stages: _Stages | None = None,
) -> int:
    """Keep diagnosing and fixing until the program runs. A start is not a pass."""
    if mode not in {"build", "update"} or not config.open_windows or not _has_program(config.project_root):
        return 0
    return _until_it_runs(store, session, model, config, ask, on_event, request, stages)


_FIX_LIMIT = 8


def _until_it_runs(
    store: SessionStore,
    session: Session,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None,
    request: str,
    stages: _Stages | None = None,
) -> int:
    """Name the fault, fix what the harness can, then ask the model. Stop only when it runs or the same fault is stuck."""
    root = config.project_root
    seen: tuple[str, ...] | None = None
    seen_files: dict[str, str] | None = None
    launch_report = ""
    report = ""
    code = 1
    faults: list[dict] = []
    for _attempt in range(_FIX_LIMIT):
        faults = troubleshoot(root, request, launch=launch_report)
        if faults:
            save_faults(root, faults)
            before = file_versions(root)
            close_known_faults(root, request)
            if file_versions(root) != before:
                launch_report = ""
                continue
            signature = tuple(str(item.get("cause") or "") for item in faults)
            if signature == seen and file_versions(root) == seen_files:
                break
            seen = signature
            seen_files = file_versions(root)
            _repair(store, session, model, config, ask, on_event, fault_brief(faults))
            if stages is not None:
                stages.lap("repair")
            launch_report = ""
            continue
        _meter(on_event, "80")
        code, report = _compile_and_show(config, on_event)
        if stages is not None:
            stages.lap("check")
        if code == 0:
            lines = [line for line in probe_logic(root, request) if line.startswith("passed:")]
            if lines:
                settle_task(root, report or "checked", lines)
            close_faults(root)
            _meter(on_event, "95")
            return 0
        launch_report = report
        faults = troubleshoot(root, request, launch=launch_report)
        if faults:
            save_faults(root, faults)
    evidence = ["failed: logic"]
    evidence.extend(
        f"failed: {item.get('where') or 'logic'}. {item.get('cause') or 'the program is still broken.'}"
        for item in faults
    )
    settle_task(root, fault_brief(faults) or report or "failed: logic", evidence)
    return code or 1


def _open_cause(root: Path) -> str:
    record = load_task(root)
    if record is None:
        return ""
    open_faults = [item for item in record.get("faults") or [] if item.get("state") != "fixed"]
    if not open_faults:
        return ""
    fault = open_faults[0]
    return f"{fault.get('symptom', '')} {fault.get('cause', '')}".strip()


def _repair(
    store: SessionStore,
    session: Session,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None,
    problem: str,
) -> None:
    _meter(on_event, "60")
    _emit(on_event, LoopEvent("status", "Fixing the program.", title="Working", body="Fixing the program."))
    run_turn(
        store=store,
        session=session,
        user_text=(
            "The program failed a check. Repair the open fault. "
            "Do not stop, and do not paste source.\n"
            + brief_report(problem)
        ),
        model=model,
        config=replace(config, open_windows=False),
        ask=ask,
        on_event=on_event,
        focus=True,
    )


def _compile_and_show(config: HarnessConfig, on_event: EventHandler | None) -> tuple[int, str]:
    _emit(on_event, LoopEvent("status", "Compiling.", title="Working", body="Compiling."))
    code, report = compile_and_launch(config)
    shown = brief_report(report)
    _emit(on_event, LoopEvent("answer", shown, title="Result", body=shown))
    return code, report


def _has_program(root: Path) -> bool:
    if not root.is_dir():
        return False
    suffixes = {".py", ".java"}
    return any(
        path.is_file() and path.suffix.lower() in suffixes and not path.name.startswith("test_")
        for path in root.iterdir()
    )


def _wants_verify(text: str) -> bool:
    """A check of the open project. This is not a request to build a new one."""
    lowered = " ".join(text.lower().split())
    if lowered in {"verify", "verify build", "verify it", "check the build", "check this build"}:
        return True
    return lowered.startswith("verify ")


def _verify_open(
    store: SessionStore,
    session: Session,
    config: HarnessConfig,
    on_event: EventHandler | None,
    choose,
) -> int:
    """Check the folder already open. Do not ask what kind of software it is."""
    placed = _assign(store, session, "verify", config, choose, on_event)
    if placed is None:
        return 0
    config, _notice = placed
    root = config.project_root
    if root.parent.name != PROJECTS_DIR:
        message = "No project is open. Build one first."
        _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
        return 0
    record = load_task(root)
    request = str(record.get("request") or "") if record else ""
    faults = troubleshoot(root, request)
    if faults:
        save_faults(root, faults)
        brief = fault_brief(faults)
        evidence = [
            f"failed: {item.get('where') or 'logic'}. {item.get('cause') or 'the program is still broken.'}"
            for item in faults
        ]
        settle_task(root, brief, evidence)
        _meter(on_event, "failed")
        _emit(on_event, LoopEvent("answer", brief, title="Result", body=brief))
        return 1
    if not config.open_windows:
        message = f"Open project: {PROJECTS_DIR}/{root.name}. The files match the request."
        _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
        return 0
    code, report = compile_and_launch(config)
    _emit(on_event, LoopEvent("run", brief_report(report), title="Run", body=brief_report(report)))
    _show_verdict(root, on_event, opened=code == 0)
    return code


def _with_project(note: str, root: Path) -> str:
    """Tell the model which folder is open. A later message stays in that folder."""
    if root.parent.name != PROJECTS_DIR:
        return note
    head = (
        f"Open project: {PROJECTS_DIR}/{root.name}. Work only in this folder. "
        "Read those files before editing. Do not create another product."
    )
    listing = repo_map(root)
    if listing:
        head = head + "\n" + listing
    return (head + "\n" + note).strip() if note else head


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


def _with_web_choice(text: str, on_event: EventHandler | None, reply, has_project: bool = False) -> str:
    question = kind_question(text, has_project=has_project)
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


def _project_lines(folders: list[Path], current: str) -> str:
    if not folders:
        return "No projects yet. Say new project and what to build."
    rows = []
    for index, folder in enumerate(folders, start=1):
        mark = "  open" if str(folder.resolve()) == current else ""
        rows.append(f"{index}  projects/{folder.name}{mark}")
    return "Projects:\n" + "\n".join(rows)


def _list_projects(store: SessionStore, session: Session, config: HarnessConfig, on_event: EventHandler | None) -> int:
    folders = project_dirs(_workspace(config))
    message = _project_lines(folders, store.work_dir(session))
    message += "\nSay change project folder, or name one: open project calculator."
    _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
    return 0


def _switch_project(
    store: SessionStore,
    session: Session,
    config: HarnessConfig,
    on_event: EventHandler | None,
    choose,
    name: str,
) -> int:
    """Open an existing project. This does not build or update anything."""
    workspace = _workspace(config)
    folders = project_dirs(workspace)
    if name:
        folder = match_project(folders, name)
        if folder is None:
            message = f"No project named {name}.\n{_project_lines(folders, store.work_dir(session))}"
            _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
            return 0
        return _open_existing(store, session, on_event, folder)
    if not folders:
        message = "No projects yet. Say new project and what to build."
        _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
        return 0
    if choose is None:
        message = _project_lines(folders, store.work_dir(session))
        _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
        return 0
    picked = choose(folders, "")
    if picked is None:
        message = "No project chosen."
        _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
        return 0
    folder = picked if picked.is_absolute() else workspace / PROJECTS_DIR / picked.name
    if not folder.is_dir() or folder.resolve().parent != (workspace / PROJECTS_DIR).resolve():
        message = f"No project named {picked.name}."
        _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
        return 0
    return _open_existing(store, session, on_event, folder)


def _open_existing(
    store: SessionStore,
    session: Session,
    on_event: EventHandler | None,
    folder: Path,
) -> int:
    resolved = folder.resolve()
    store.set_work_dir(session, resolved)
    store.set_title(session, f"{PROJECTS_DIR}/{resolved.name}")
    message = f"Project folder: {PROJECTS_DIR}/{resolved.name}"
    opened = f"Open project: {PROJECTS_DIR}/{resolved.name}"
    _emit(on_event, LoopEvent("status", message, title="Working", body=message))
    _emit(on_event, LoopEvent("answer", opened, title="Result", body=opened))
    return 0


def _delete_project(
    store: SessionStore,
    session: Session,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None,
    name: str,
) -> int:
    """Remove one folder under projects/ after a yes. Nothing else is deleted."""
    workspace = _workspace(config)
    folders = project_dirs(workspace)
    if not name or name in {"this", "the", "it", "current", "open"}:
        current = store.work_dir(session)
        folder = Path(current) if current else None
    else:
        folder = match_project(folders, name)
    projects = (workspace / PROJECTS_DIR).resolve()
    if folder is None or not folder.is_dir() or folder.resolve().parent != projects:
        message = "Say which project to delete. Example: delete project calculator."
        _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
        return 0
    target = folder.resolve()
    if not ask("delete_project", f"Delete projects/{target.name}? This removes that folder."):
        message = f"Kept projects/{target.name}."
        _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
        return 0
    shutil.rmtree(target)
    if store.work_dir(session) == str(target):
        store.set_work_dir(session, workspace.resolve())
        store.set_title(session, workspace.name)
    message = f"Deleted projects/{target.name}."
    _emit(on_event, LoopEvent("answer", message, title="Result", body=message))
    return 0


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
            "/sessions  saved sessions\n"
            "new project build an ATM  start another program\n"
            "change project folder  open a different project\n"
            "list projects  show the folders\n"
            "delete project <name>  remove one project\n"
            "help  this list"
        )
    elif command == "/tools":
        body = tool_catalog()
    elif command == "/skills":
        body = skill_catalog()
    elif command == "/status":
        root = _work_root(store, session, config)
        folder = f"{PROJECTS_DIR}/{root.name}" if root.parent.name == PROJECTS_DIR else "none"
        pending = ", ".join(item.text for item in open_todos(root)) or "none"
        body = (
            f"project {folder}\n"
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
