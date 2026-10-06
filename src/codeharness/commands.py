"""Plan, build, launch, and handoff. Chat and the local page share this."""

from __future__ import annotations

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
from codeharness.playbook import is_chat
from codeharness.projects import PROJECTS_DIR, assign_project, is_tweak, task_slug, wants_new_folder
from codeharness.review import brief_report
from codeharness.run import compile_and_launch, is_run_command
from codeharness.session import Session, SessionStore
from codeharness.snapshot import capture, restore
from codeharness.stack import expects_stack
from codeharness.taskrecord import begin_task, behavior_note, check_clause, settle_task
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
    task = handoff_task(text)
    if task is not None and not task:
        return handoff(store, session, task, model, config, ask, on_event, reply)
    source = task if task is not None else text
    chosen = _with_web_choice(source, on_event, reply)
    placed = _assign(store, session, chosen, config, choose, on_event)
    if placed is None:
        return 0
    config, notice = placed
    if task is not None:
        return handoff(store, session, chosen, model, config, ask, on_event, reply)
    if expects_stack(chosen):
        _meter(on_event, "8")
        begin_task(config.project_root, chosen)
        code = lead_stack(store, session, chosen, model, config, ask, on_event)
        _settle(config.project_root, session)
        _meter(on_event, "100" if code == 0 else "failed")
        return code
    note, turn_config = _follow_up(text, notice, config)
    note = _with_behavior(note, chosen)
    mode = _construction(chosen, turn_config)
    armed = _arm_build(mode, turn_config, ask, chosen)
    if mode:
        note = _build_note(note)
        _meter(on_event, "8")
    begin_task(config.project_root, chosen)
    result = run_turn(
        store=store,
        session=session,
        user_text=chosen,
        model=model,
        config=replace(armed, open_windows=False) if mode else armed,
        ask=ask,
        on_event=on_event,
        note=note,
        focus=bool(mode),
    )
    settle_task(config.project_root, result.text, result.checks)
    code = _finish_build(
        store,
        session,
        model,
        replace(armed, open_windows=turn_config.open_windows),
        ask,
        on_event,
        mode,
    )
    if mode:
        _meter(on_event, "100" if code == 0 else "failed")
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


_BUILD_NOTE = (
    "Write this program only. Ignore every older program in the chat. "
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


def _build_note(note: str) -> str:
    return (note.rstrip() + "\n" + _BUILD_NOTE).strip()


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
    _meter(on_event, "8")
    begin_task(config.project_root, task)
    result = run_turn(
        store=store,
        session=session,
        user_text=task,
        model=model,
        config=replace(armed, open_windows=False),
        ask=approved,
        on_event=on_event,
        note=_build_note(behavior_note(task)),
        focus=True,
    )
    settle_task(config.project_root, result.text, result.checks)
    code = _finish_build(
        store,
        session,
        model,
        replace(armed, open_windows=config.open_windows),
        approved,
        on_event,
        mode,
    )
    _meter(on_event, "100" if code == 0 else "failed")
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
    """Launch the program from the latest build, not whichever folder was open before."""
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
    _meter(on_event, "8")
    begin_task(config.project_root, task)
    if expects_stack(task):
        code = lead_stack(store, session, task, model, config, ask, on_event)
        _settle(config.project_root, session)
        _meter(on_event, "100" if code == 0 else "failed")
        return code
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
        note=behavior_note(task),
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
    built = run_turn(
        store=store,
        session=session,
        user_text=f"Implement this plan:\n{plan.text}",
        model=model,
        config=armed,
        ask=ask,
        on_event=on_event,
        note=behavior_note(task),
    )
    settle_task(config.project_root, built.text, built.checks)
    code = _finish_build(store, session, model, armed, ask, on_event, mode)
    _meter(on_event, "100" if code == 0 else "failed")
    return code


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


def _construction(text: str, config: HarnessConfig) -> str:
    """A new program is a build. A change inside projects/ is an update."""
    if wants_new_folder(text):
        return "build"
    if is_tweak(text) and config.project_root.parent.name == PROJECTS_DIR:
        return "update"
    return ""


def _arm_build(mode: str, config: HarnessConfig, ask: AskFunc, request: str = "") -> HarnessConfig:
    """One yes covers the file writes. No keeps writes off. The harness launches."""
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


def _with_behavior(note: str, request: str) -> str:
    extra = behavior_note(request)
    if not extra:
        return note
    if not note:
        return extra
    return note.rstrip() + "\n" + extra


def _finish_build(
    store: SessionStore,
    session: Session,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None,
    mode: str,
) -> int:
    """Compile the folder and start the program. One repair if that fails.

    A start is not a pass. Only a failed launch is recorded here.
    """
    if mode not in {"build", "update"} or not config.open_windows or not _has_program(config.project_root):
        return 0
    code, report = _compile_and_show(config, on_event)
    if code == 0:
        return 0
    _emit(on_event, LoopEvent("status", "Fixing the launch.", title="Working", body="Fixing the launch."))
    run_turn(
        store=store,
        session=session,
        user_text=(
            "The program did not compile or launch. Fix the files in the project. "
            "Do not paste source in the answer.\n"
            + brief_report(report)
        ),
        model=model,
        config=config,
        ask=ask,
        on_event=on_event,
        focus=True,
    )
    code, report = _compile_and_show(config, on_event)
    if code != 0:
        settle_task(config.project_root, report, ["failed: launch"])
    return code


def _compile_and_show(config: HarnessConfig, on_event: EventHandler | None) -> tuple[int, str]:
    _emit(on_event, LoopEvent("status", "Compiling.", title="Working", body="Compiling."))
    code, report = compile_and_launch(config)
    shown = brief_report(report)
    _emit(on_event, LoopEvent("answer", shown, title="Result", body=shown))
    return code, report


def _has_program(root: Path) -> bool:
    if not root.is_dir():
        return False
    suffixes = {".py", ".java", ".html", ".htm"}
    return any(
        path.is_file() and path.suffix.lower() in suffixes and not path.name.startswith("test_")
        for path in root.iterdir()
    )


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
