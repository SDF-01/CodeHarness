"""Build the small prompt the model sees from the full session."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from codeharness.config import HarnessConfig
from codeharness.languages import LANGUAGE_NAMES
from codeharness.playbook import prompt_addons, wants_harness_tests
from codeharness.session import StoredMessage

SYSTEM_PROMPT = (
    "You are a coding agent in one project directory. "
    "Work like OpenCode: write code into project files. "
    f"You can write {LANGUAGE_NAMES}. Follow the language the user chose. "
    "Use write_file to create a file and edit_file to change one that exists. "
    "Write a new file in one write_file call. Do not explain the code before that call. "
    "Do not paste source code in the final answer. Name the file path and whether the check passed. "
    "Do not repeat a tool call that failed. "
    "Call a tool when you need one. Do not print a JSON tool call as the answer. "
    "If the user is greeting you or chatting, answer in text and do not call a tool. "
    "Do not invent a program from a greeting. "
    "Do not launch the program yourself. The harness compiles and launches it after the files are saved."
)


@dataclass(frozen=True)
class BuiltContext:
    messages: list[dict]
    estimated_tokens: int
    pruned: bool
    tools: list[dict] = field(default_factory=list)


def estimate_tokens(text: str) -> int:
    """Rough count used only when the server does not report usage."""
    if not text:
        return 0
    return (len(text) + 3) // 4


def estimate_messages(messages: list[dict]) -> int:
    parts: list[str] = []
    for message in messages:
        parts.append(str(message.get("content") or ""))
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            parts.append(str(function.get("name") or ""))
            parts.append(str(function.get("arguments") or ""))
    return estimate_tokens("\n".join(parts))


def build_context(
    stored: list[StoredMessage],
    config: HarnessConfig,
    lessons: list[str] | None = None,
    agent: str = "build",
    note: str = "",
    tools: list[dict] | None = None,
) -> BuiltContext:
    """Keep a valid request inside the budget, including tool schemas."""
    schemas = tools or []
    kept = list(stored)
    tool_indexes = [index for index, message in enumerate(kept) if message.role == "tool"]
    order = tool_indexes[:-1] + tool_indexes[-1:]
    stubbed: set[int] = set()
    pruned = False
    messages = _assemble(kept, stubbed, config, lessons or [], agent, note, pruned)
    for index in order:
        if _request_tokens(messages, schemas) <= config.prompt_budget:
            break
        if index >= len(kept):
            break
        stubbed.add(index)
        pruned = True
        messages = _assemble(kept, stubbed, config, lessons or [], agent, note, pruned)
    while kept and _request_tokens(messages, schemas) > config.prompt_budget:
        kept = _drop_oldest(kept)
        stubbed = {index for index in stubbed if index < len(kept)}
        pruned = True
        messages = _assemble(kept, stubbed, config, lessons or [], agent, note, pruned)
    kept_tools = list(schemas)
    while kept_tools and _request_tokens(messages, kept_tools) > config.prompt_budget:
        kept_tools.pop()
        pruned = True
    if messages and _request_tokens(messages, kept_tools) > config.prompt_budget:
        room = config.prompt_budget - _schema_tokens(kept_tools)
        messages[0]["content"] = _fit(str(messages[0]["content"]), max(room, 1))
        pruned = True
    return BuiltContext(
        messages=messages,
        estimated_tokens=_request_tokens(messages, kept_tools),
        pruned=pruned,
        tools=kept_tools,
    )


_HARNESS_NOTES = ("Check result:", "Review failed.", "Shell failed.", "Window failed.")


def _system_prompt(
    config: HarnessConfig,
    stored: list[StoredMessage],
    lessons: list[str],
    agent: str,
    note: str = "",
    pruned: bool = False,
) -> str:
    task = _latest_task(stored)
    lines = [SYSTEM_PROMPT]
    extra = prompt_addons(task, config.project_root, _touched_paths(stored), lessons, agent)
    if extra:
        lines.append(extra)
    if note.strip():
        lines.append("Context:\n" + note.strip())
    if config.check_command and wants_harness_tests(task):
        lines.append(f"Check command: {config.check_command}")
    if config.launch_command:
        lines.append(f"Launch command: {config.launch_command}")
    return "\n".join(lines)


def _latest_task(stored: list[StoredMessage]) -> str:
    for message in reversed(stored):
        if message.role == "user" and not message.content.startswith(_HARNESS_NOTES):
            return message.content
    return ""


def _touched_paths(stored: list[StoredMessage]) -> list[str]:
    paths: list[str] = []
    for message in stored:
        for call in message.tool_calls or []:
            if call.get("name") not in {"write_file", "edit_file"}:
                continue
            path = str((call.get("arguments") or {}).get("path") or "")
            if path:
                paths.append(path)
    return paths


def _assemble(
    stored: list[StoredMessage],
    stubbed: set[int],
    config: HarnessConfig,
    lessons: list[str],
    agent: str,
    note: str = "",
    pruned: bool = False,
) -> list[dict]:
    messages = [{"role": "system", "content": _system_prompt(config, stored, lessons, agent, note, pruned)}]
    for index, message in enumerate(stored):
        content = _stub(message) if index in stubbed else message.content
        messages.append(_to_api(message, content))
    return messages


def _request_tokens(messages: list[dict], tools: list[dict]) -> int:
    return estimate_messages(messages) + _schema_tokens(tools)


def _schema_tokens(tools: list[dict]) -> int:
    if not tools:
        return 0
    return estimate_tokens(json.dumps(tools, sort_keys=True))


def _fit(text: str, token_budget: int) -> str:
    limit = max(token_budget, 1) * 4
    if len(text) <= limit:
        return text
    return text[:limit]


def _drop_oldest(kept: list[StoredMessage]) -> list[StoredMessage]:
    """Drop one complete turn so a tool call is never left without its result."""
    if not kept:
        return kept
    first = kept[0]
    if first.role == "assistant" and first.tool_calls:
        ids = {str(call.get("id") or "") for call in first.tool_calls}
        rest: list[StoredMessage] = []
        skipping = True
        for message in kept[1:]:
            if skipping and message.role == "tool" and (message.tool_call_id or "") in ids:
                continue
            skipping = False
            rest.append(message)
        return rest
    return kept[1:]


def _stub(message: StoredMessage) -> str:
    name = message.tool_name or "tool"
    return f"[pruned tool result: {name}]"


def _to_api(message: StoredMessage, content: str) -> dict:
    if message.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id or "",
            "content": content,
        }
    if message.role == "assistant" and message.tool_calls:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [_api_tool_call(call) for call in message.tool_calls],
        }
    return {"role": message.role, "content": content}


def _api_tool_call(call: dict) -> dict:
    return {
        "id": call["id"],
        "type": "function",
        "function": {
            "name": call["name"],
            "arguments": json.dumps(call.get("arguments") or {}, sort_keys=True),
        },
    }
