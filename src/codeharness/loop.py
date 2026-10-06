"""The agent loop: call the model, run tools, repeat until it answers."""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from codeharness.agents import config_for_agent
from codeharness.cards import tool_card
from codeharness.config import HarnessConfig
from codeharness.context import build_context, estimate_messages, estimate_tokens
from codeharness.languages import language_of
from codeharness.model import ChatModel, Completion, ToolCall
from codeharness.permissions import AskFunc, PermissionGate
from codeharness.playbook import coaching_for, lesson_from_failure, wants_harness_tests
from codeharness.review import brief_report, review_paths
from codeharness.runtime import TurnRuntime, bind_runtime, reset_runtime
from codeharness.run import open_window, probe_compiled, probe_java, probe_program, python_script, uses_tkinter
from codeharness.stack import serves_http, stack_problem
from codeharness.session import Session, SessionStore, StoredMessage
from codeharness.tools import run_tool, shell, tools_for_model


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: int = 0
    estimated: bool = False

    def add_step(self, prompt: int, completion: int, calls: int, estimated: bool) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.tool_calls += calls
        self.estimated = self.estimated or estimated


@dataclass(frozen=True)
class LoopEvent:
    kind: str
    text: str
    title: str = ""
    body: str = ""


@dataclass
class TurnResult:
    text: str
    usage: Usage = field(default_factory=Usage)
    session_id: str = ""


EventHandler = Callable[[LoopEvent], None]

_PARALLEL = frozenset({"read_file", "search", "list_files", "git_status", "git_diff", "diagnostics", "skill"})


def format_usage(usage: Usage, prefix: str = "tokens") -> str:
    source = "estimated" if usage.estimated else "api"
    return (
        f"{prefix}: prompt={usage.prompt_tokens} "
        f"completion={usage.completion_tokens} "
        f"tool_calls={usage.tool_calls} source={source}"
    )


def run_turn(
    *,
    store: SessionStore,
    session: Session,
    user_text: str,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None = None,
    note: str = "",
) -> TurnResult:
    token = bind_runtime(
        TurnRuntime(store=store, session=session, model=model, config=config, ask=ask, on_event=on_event)
    )
    try:
        return _run_turn(
            store=store,
            session=session,
            user_text=user_text,
            model=model,
            config=config,
            ask=ask,
            on_event=on_event,
            note=note,
        )
    finally:
        reset_runtime(token)


def _run_turn(
    *,
    store: SessionStore,
    session: Session,
    user_text: str,
    model: ChatModel,
    config: HarnessConfig,
    ask: AskFunc,
    on_event: EventHandler | None = None,
    note: str = "",
) -> TurnResult:
    if session.title == "new session" and user_text.strip():
        store.set_title(session, user_text.strip().splitlines()[0][:60])
    store.append(session, StoredMessage(role="user", content=user_text))
    agent_name = store.get_agent(session)
    config = config_for_agent(config, agent_name)
    gate = PermissionGate(config.permissions, ask)
    schemas = tools_for_model(config.permissions)
    usage = Usage()
    history: list[tuple[str, str]] = []
    written: list[Path] = []
    opened: set[str] = set()
    shell_problem = ""
    advice_sent = False
    paste_sent = False

    for _step in range(config.max_steps):
        built = build_context(
            session.messages,
            config,
            lessons=store.lessons(session),
            agent=agent_name,
            note=note,
        )
        _emit(
            on_event,
            LoopEvent(
                "harness",
                f"harness: {built.estimated_tokens} tokens, {len(schemas)} tools",
                title="Harness",
                body=_harness_body(built, schemas, config, user_text, agent_name),
            ),
        )
        store.append(session, StoredMessage(role="assistant", content="interrupted"))
        keep_interrupted = False
        try:
            completion = model.complete(
                built.messages,
                schemas,
                on_delta=lambda piece: _emit(on_event, LoopEvent("delta", piece, title="Ollama", body=piece)),
            )
        except Exception:
            keep_interrupted = True
            raise
        finally:
            if not keep_interrupted:
                store.drop_interrupted(session)
        step_usage = _step_usage(completion, built.messages)
        usage.add_step(
            step_usage.prompt_tokens,
            step_usage.completion_tokens,
            len(completion.tool_calls),
            step_usage.estimated,
        )
        _emit(on_event, LoopEvent("tokens", format_usage(step_usage)))
        _emit(
            on_event,
            LoopEvent(
                "ollama",
                f"ollama: {config.model}",
                title="Ollama",
                body=_ollama_body(config, completion, step_usage),
            ),
        )
        if not schemas and completion.tool_calls:
            text = (completion.content or "").strip() or "Say what you want built."
            store.append(session, StoredMessage(role="assistant", content=text))
            _emit(on_event, LoopEvent("answer", f"agent: {text}", title="Result", body=text))
            _emit(on_event, LoopEvent("tokens", format_usage(usage, prefix="turn tokens")))
            return TurnResult(text=text, usage=usage, session_id=session.id)
        if not completion.tool_calls:
            problems = "" if agent_name in {"general", "review", "explore", "plan"} else review_paths(written)
            if problems:
                _remember(store, session, problems)
                _send_back(store, session, "Review failed.", problems, on_event)
                continue
            if shell_problem and not (_is_advice(shell_problem) and advice_sent):
                if _is_advice(shell_problem):
                    advice_sent = True
                _remember(store, session, shell_problem)
                _send_back(store, session, "Shell failed.", shell_problem, on_event)
                continue
            window_problem = _launch_pending(written, opened, config)
            if window_problem:
                _remember(store, session, window_problem)
                _send_back(store, session, "Window failed.", window_problem, on_event)
                continue
            stack = ""
            if agent_name not in {"plan", "route", "general", "review", "explore"}:
                stack = stack_problem(config.project_root, user_text)
            if stack:
                _remember(store, session, stack)
                _send_back(store, session, "Stack failed.", stack, on_event)
                continue
            runtime_problem = ""
            if agent_name not in {"general", "review", "explore", "plan"}:
                runtime_problem = _runtime_pending(written, config.project_root)
            if runtime_problem:
                _remember(store, session, runtime_problem)
                _send_back(store, session, "Run failed.", runtime_problem, on_event)
                continue
            text = completion.content.strip() or "The model returned an empty reply."
            if not written and not paste_sent and "```" in text:
                paste_sent = True
                _send_back(store, session, "Write the file.", "Do not paste the source.", on_event)
                continue
            store.append(session, StoredMessage(role="assistant", content=text))
            _emit(on_event, LoopEvent("answer", f"agent: {text}", title="Result", body=text))
            _emit(on_event, LoopEvent("tokens", format_usage(usage, prefix="turn tokens")))
            return TurnResult(text=text, usage=usage, session_id=session.id)

        store.append(
            session,
            StoredMessage(
                role="assistant",
                content=completion.content,
                tool_calls=[_stored_call(call) for call in completion.tool_calls],
            ),
        )
        wrote_code = False
        pending = list(completion.tool_calls)
        cursor = 0
        while cursor < len(pending):
            stopped = _user_stopped(gate, store, session, on_event, usage)
            if stopped is not None:
                return stopped
            if _parallel_batch(pending, cursor):
                batch: list[ToolCall] = []
                while cursor < len(pending) and _can_parallel(pending[cursor]):
                    batch.append(pending[cursor])
                    cursor += 1
                ready: list[ToolCall] = []
                for call in batch:
                    if _preflight(call, history, config, gate, store, session, on_event):
                        ready.append(call)
                        history.append(_signature(call))
                if len(ready) > 1:
                    with ThreadPoolExecutor(max_workers=len(ready)) as pool:
                        results = list(pool.map(lambda item: _execute(item, config), ready))
                else:
                    results = [_execute(item, config) for item in ready]
                for call, result in zip(ready, results):
                    if _note_write(call, result, config, written):
                        wrote_code = True
                    label = f"tool: {call.name} {_tool_detail(call)}".rstrip()
                    _record(
                        store,
                        session,
                        call,
                        result,
                        on_event,
                        label,
                        title=f"Tool {call.name}",
                        body=tool_card(call.name, call.arguments, result),
                    )
                continue
            call = pending[cursor]
            cursor += 1
            detail = _tool_detail(call)
            signature = _signature(call)
            if _is_repeated_call(history, signature, config.doom_repeat_limit):
                if not gate.allow("doom_loop", detail):
                    stopped = _user_stopped(gate, store, session, on_event, usage)
                    if stopped is not None:
                        return stopped
                    _record(
                        store,
                        session,
                        call,
                        "error: this repeated tool call was stopped",
                        on_event,
                        f"denied: repeated {call.name} was stopped",
                        title=f"Tool {call.name}",
                        body="Denied. This repeated call was stopped.",
                    )
                    continue
            if call.parse_error:
                history.append(signature)
                _record(
                    store,
                    session,
                    call,
                    f"error: {call.parse_error}",
                    on_event,
                    f"tool: {call.name} invalid arguments",
                    title=f"Tool {call.name}",
                    body=f"Input\n{_format_arguments(call)}\nOutput\nerror: {call.parse_error}",
                )
                continue
            if not gate.allow(call.name, detail):
                stopped = _user_stopped(gate, store, session, on_event, usage)
                if stopped is not None:
                    return stopped
                _record(
                    store,
                    session,
                    call,
                    f"error: {call.name} was denied",
                    on_event,
                    f"denied: {call.name} was denied",
                    title=f"Tool {call.name}",
                    body="Denied. The harness did not run this tool.",
                )
                continue
            result = _execute(call, config)
            if _note_write(call, result, config, written):
                wrote_code = True
            if call.name == "shell":
                if result.startswith("Opened "):
                    opened.add(_opened_name(result))
                    shell_problem = ""
                elif _command_failed(result):
                    shell_problem = result
                    _remember(store, session, result)
                else:
                    shell_problem = ""
            history.append(signature)
            label = f"tool: {call.name} {detail}".rstrip()
            _record(
                store,
                session,
                call,
                result,
                on_event,
                label,
                title=f"Tool {call.name}",
                body=tool_card(call.name, call.arguments, result),
            )
        stopped = _user_stopped(gate, store, session, on_event, usage)
        if stopped is not None:
            return stopped
        if _should_check(user_text, written, wrote_code, config):
            _run_check(store, session, config, on_event)

    text = f"Stopped after {config.max_steps} steps. The task is not finished."
    store.append(session, StoredMessage(role="assistant", content=text))
    _emit(on_event, LoopEvent("answer", f"agent: {text}", title="Result", body=text))
    _emit(on_event, LoopEvent("tokens", format_usage(usage, prefix="turn tokens")))
    return TurnResult(text=text, usage=usage, session_id=session.id)


def _user_stopped(
    gate: PermissionGate,
    store: SessionStore,
    session: Session,
    on_event: EventHandler | None,
    usage: Usage,
) -> TurnResult | None:
    """A sentence at the approval prompt ends the turn. A plain no does not."""
    if not gate.halt:
        return None
    text = "Stopped. Say what you want built."
    store.append(session, StoredMessage(role="assistant", content=text))
    _emit(on_event, LoopEvent("answer", f"agent: {text}", title="Result", body=text))
    _emit(on_event, LoopEvent("tokens", format_usage(usage, prefix="turn tokens")))
    return TurnResult(text=text, usage=usage, session_id=session.id)


def _can_parallel(call: ToolCall) -> bool:
    return call.name in _PARALLEL and not call.parse_error


def _parallel_batch(calls: list[ToolCall], cursor: int) -> bool:
    return cursor + 1 < len(calls) and _can_parallel(calls[cursor]) and _can_parallel(calls[cursor + 1])


def _preflight(
    call: ToolCall,
    history: list[tuple[str, str]],
    config: HarnessConfig,
    gate: PermissionGate,
    store: SessionStore,
    session: Session,
    on_event: EventHandler | None,
) -> bool:
    detail = _tool_detail(call)
    signature = _signature(call)
    if _is_repeated_call(history, signature, config.doom_repeat_limit):
        if not gate.allow("doom_loop", detail):
            if gate.halt:
                return False
            _record(
                store,
                session,
                call,
                "error: this repeated tool call was stopped",
                on_event,
                f"denied: repeated {call.name} was stopped",
                title=f"Tool {call.name}",
                body="Denied. This repeated call was stopped.",
            )
            return False
    if not gate.allow(call.name, detail):
        if gate.halt:
            return False
        _record(
            store,
            session,
            call,
            f"error: {call.name} was denied",
            on_event,
            f"denied: {call.name} was denied",
            title=f"Tool {call.name}",
            body="Denied. The harness did not run this tool.",
        )
        return False
    return True


def _note_write(call: ToolCall, result: str, config: HarnessConfig, written: list[Path]) -> bool:
    if call.name not in {"write_file", "edit_file", "apply_patch"}:
        return False
    if not (result.startswith(("wrote ", "edited ", "patched ")) or result.startswith("error:")):
        return False
    paths: list[str] = []
    if call.name == "apply_patch":
        for hunk in call.arguments.get("hunks") or []:
            if isinstance(hunk, dict) and hunk.get("path"):
                paths.append(str(hunk["path"]))
    else:
        paths.append(str(call.arguments.get("path") or ""))
    for raw in paths:
        target = config.project_root / raw
        if language_of(target) is not None and target not in written:
            written.append(target)
    return True


def _execute(call: ToolCall, config: HarnessConfig) -> str:
    if call.name == "shell":
        command = str(call.arguments.get("command") or "")
        script = python_script(command, config.project_root)
        if script is not None and uses_tkinter(script):
            return open_window(script, config.project_root)
    return run_tool(call.name, call.arguments, config.project_root, config)


def _launch_pending(paths: list[Path], opened: set[str], config: HarnessConfig) -> str:
    if not config.open_windows:
        return ""
    for path in paths:
        if path.name in opened or not uses_tkinter(path):
            continue
        result = open_window(path, config.project_root)
        if result.startswith("error:"):
            return result
        opened.add(path.name)
    return ""


def _runtime_pending(paths: list[Path], root: Path) -> str:
    for path in paths:
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix == ".py" and not uses_tkinter(path) and not serves_http(path):
            result = probe_program(path, root)
        elif suffix == ".java":
            result = probe_java(path, root)
        elif suffix in {".c", ".cpp", ".go", ".rs", ".rb", ".php", ".kt", ".swift"}:
            result = probe_compiled(path, root)
        else:
            continue
        if result:
            return result
    return ""


def _should_check(user_text: str, written: list[Path], wrote_code: bool, config: HarnessConfig) -> bool:
    if written or not config.check_command or config.permissions.get("shell") == "deny":
        return False
    return wrote_code or wants_harness_tests(user_text)


def _opened_name(result: str) -> str:
    rest = result.removeprefix("Opened ").strip()
    marker = ". The window is running."
    if marker in rest:
        return rest.split(marker, 1)[0]
    return rest.split(" ", 1)[0]


def _command_failed(result: str) -> bool:
    if result.startswith("error:"):
        return True
    if "exit_code=" not in result:
        return False
    return result.rstrip().rsplit("exit_code=", 1)[-1].strip() != "0"


def _is_advice(result: str) -> bool:
    return "already part of Python" in result


def _remember(store: SessionStore, session: Session, text: str) -> None:
    store.add_lesson(session, lesson_from_failure(text))


def _send_back(
    store: SessionStore,
    session: Session,
    title: str,
    detail: str,
    on_event: EventHandler | None,
) -> None:
    store.append(session, StoredMessage(role="user", content=f"{title}\n{detail}"))
    _emit(on_event, LoopEvent("harness", f"harness: {title}", title="Result", body=f"{title}\n{detail}"))


def _step_usage(completion: Completion, messages: list[dict]) -> Usage:
    if completion.prompt_tokens is not None and completion.completion_tokens is not None:
        return Usage(
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            tool_calls=len(completion.tool_calls),
            estimated=False,
        )
    completion_text = completion.content + json.dumps([call.arguments for call in completion.tool_calls])
    return Usage(
        prompt_tokens=estimate_messages(messages),
        completion_tokens=estimate_tokens(completion_text),
        tool_calls=len(completion.tool_calls),
        estimated=True,
    )


def _stored_call(call: ToolCall) -> dict:
    return {"id": call.id, "name": call.name, "arguments": call.arguments}


def _signature(call: ToolCall) -> tuple[str, str]:
    if call.parse_error:
        return (call.name, call.parse_error)
    return (call.name, json.dumps(call.arguments, sort_keys=True, separators=(",", ":")))


def _is_repeated_call(history: list[tuple[str, str]], signature: tuple[str, str], limit: int) -> bool:
    needed = limit - 1
    if len(history) < needed:
        return False
    return all(item == signature for item in history[-needed:])


def _tool_detail(call: ToolCall) -> str:
    if call.name == "shell":
        return str(call.arguments.get("command", ""))[:120]
    if call.name == "git_commit":
        return str(call.arguments.get("message", ""))[:80]
    if call.name in {"git_add", "git_diff"}:
        return str(call.arguments.get("path", ""))
    if "path" in call.arguments:
        return str(call.arguments.get("path"))
    if "query" in call.arguments:
        return str(call.arguments.get("query"))[:80]
    return ""


def _run_check(store: SessionStore, session: Session, config: HarnessConfig, on_event: EventHandler | None) -> None:
    result = shell.run({"command": config.check_command}, config.project_root, config)
    store.append(session, StoredMessage(role="user", content=f"Check result:\n{result}"))
    passed = result.rstrip().endswith("exit_code=0")
    if passed:
        return
    status = "Check failed."
    _emit(
        on_event,
        LoopEvent("harness", f"harness: {status}", title="Result", body=f"{status}\n{brief_report(result)}"),
    )


def _harness_body(built, schemas: list[dict], config: HarnessConfig, task: str, agent: str = "build") -> str:
    names = ", ".join(item["function"]["name"] for item in schemas) or "(none)"
    coaching = coaching_for(task, agent)
    skill_line = coaching.splitlines()[0] if coaching else "Active skills: none"
    lines = [
        skill_line,
        f"Tools: {names}",
        f"Prompt size: {built.estimated_tokens} estimated tokens. Budget: {config.prompt_budget}.",
        f"Next: {config.model} at {config.base_url}",
    ]
    if built.pruned:
        lines.append("Older tool results were shortened to stay inside the budget.")
    return "\n".join(lines)


def _ollama_body(config: HarnessConfig, completion: Completion, step_usage: Usage) -> str:
    source = "estimated" if step_usage.estimated else "api"
    if completion.tool_calls:
        names = ", ".join(call.name for call in completion.tool_calls)
        outcome = f"Tool call: {names}"
    else:
        outcome = "Answer"
    return "\n".join(
        [
            config.model or "(no model name)",
            outcome,
            (
                f"Prompt {step_usage.prompt_tokens}, completion {step_usage.completion_tokens}, "
                f"source {source}."
            ),
        ]
    )


def _format_arguments(call: ToolCall) -> str:
    if not call.arguments:
        return "  (none)"
    lines: list[str] = []
    for key, value in call.arguments.items():
        shown = str(value).replace("\r\n", "\n")
        if len(shown) > 240:
            shown = shown[:240] + "..."
        if "\n" in shown:
            lines.append(f"  {key}:")
            lines.extend(f"    {part}" for part in shown.splitlines())
        else:
            lines.append(f"  {key}: {shown}")
    return "\n".join(lines)


def _record(
    store: SessionStore,
    session: Session,
    call: ToolCall,
    result: str,
    on_event: EventHandler | None,
    event_text: str,
    title: str = "",
    body: str = "",
) -> None:
    _emit(on_event, LoopEvent("tool", event_text, title=title or f"Tool {call.name}", body=body or result))
    store.append(
        session,
        StoredMessage(role="tool", content=result, tool_call_id=call.id, tool_name=call.name),
    )


def _emit(on_event: EventHandler | None, event: LoopEvent) -> None:
    if on_event is not None:
        on_event(event)
