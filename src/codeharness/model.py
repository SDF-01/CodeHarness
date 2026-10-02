"""OpenAI-compatible chat client for a local model server."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from codeharness.config import HarnessConfig
from codeharness.errors import ConfigError, ModelError


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict
    parse_error: str | None = None


@dataclass(frozen=True)
class Completion:
    content: str
    tool_calls: list[ToolCall]
    prompt_tokens: int | None
    completion_tokens: int | None


class ChatModel(Protocol):
    def complete(self, messages: list[dict], tools: list[dict], on_delta=None) -> Completion:
        """Return one model step. on_delta receives prose as it arrives."""


class OpenAICompatibleClient:
    """POST /chat/completions against a server such as Ollama or LM Studio."""

    def __init__(self, config: HarnessConfig) -> None:
        self._config = config

    def complete(self, messages: list[dict], tools: list[dict], on_delta=None) -> Completion:
        if not self._config.model:
            raise ConfigError("no model is configured")
        payload: dict = {
            "model": self._config.model,
            "messages": messages,
            "temperature": 0,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if on_delta is not None:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self._config.base_url}/chat/completions",
            data=data,
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._config.request_timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise ModelError(f"model server returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ModelError(
                f"could not reach the model server at {self._config.base_url}: {exc.reason}"
            ) from exc
        if on_delta is not None:
            completion, visible = completion_from_stream(raw)
            for piece in visible:
                on_delta(piece)
            return completion
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ModelError("model server returned invalid JSON") from exc
        return parse_completion(body)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers


def completion_from_stream(raw: str) -> tuple[Completion, list[str]]:
    """Turn an SSE body into a completion and the prose pieces safe to print live."""
    content_parts: list[str] = []
    tool_parts: dict[int, dict] = {}
    prompt_tokens = None
    completion_tokens = None
    for line in raw.splitlines():
        piece = line.strip()
        if piece.startswith("data:"):
            piece = piece[5:].strip()
        if not piece or piece == "[DONE]":
            continue
        try:
            event = json.loads(piece)
        except json.JSONDecodeError:
            continue
        usage = event.get("usage") or {}
        if isinstance(usage.get("prompt_tokens"), int):
            prompt_tokens = usage["prompt_tokens"]
        if isinstance(usage.get("completion_tokens"), int):
            completion_tokens = usage["completion_tokens"]
        choices = event.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or choices[0].get("message") or {}
        text = delta.get("content") or ""
        if text:
            content_parts.append(text)
        for call in delta.get("tool_calls") or []:
            index = int(call.get("index") or 0)
            slot = tool_parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if call.get("id"):
                slot["id"] = call["id"]
            function = call.get("function") or {}
            if function.get("name"):
                slot["name"] += function["name"]
            if function.get("arguments"):
                slot["arguments"] += function["arguments"]
    content = "".join(content_parts)
    raw_calls = [
        {
            "id": slot["id"] or f"call_{index}",
            "function": {"name": slot["name"], "arguments": slot["arguments"] or "{}"},
        }
        for index, slot in sorted(tool_parts.items())
        if slot["name"]
    ]
    completion = parse_completion(
        {
            "choices": [{"message": {"content": content, "tool_calls": raw_calls}}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        }
    )
    visible: list[str] = []
    if not completion.tool_calls and content and not content.lstrip().startswith("{"):
        visible.append(content)
    return completion, visible


def parse_completion(body: dict) -> Completion:
    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelError("model response did not include a message") from exc
    content = message.get("content") or ""
    if not isinstance(content, str):
        content = str(content)
    tool_calls = [
        parse_tool_call(raw_call, index)
        for index, raw_call in enumerate(message.get("tool_calls") or [])
    ]
    if not tool_calls:
        tool_calls = tool_calls_from_text(content)
        if tool_calls:
            content = ""
    usage = body.get("usage") or {}
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    return Completion(
        content=content,
        tool_calls=tool_calls,
        prompt_tokens=prompt if isinstance(prompt, int) else None,
        completion_tokens=completion if isinstance(completion, int) else None,
    )


def parse_tool_call(raw: dict, index: int) -> ToolCall:
    function = raw.get("function") or {}
    name = str(function.get("name") or "")
    call_id = str(raw.get("id") or f"call_{index}")
    arguments_raw = function.get("arguments")
    if isinstance(arguments_raw, dict):
        return ToolCall(id=call_id, name=name, arguments=arguments_raw)
    arguments_text = "{}" if arguments_raw is None else str(arguments_raw)
    try:
        parsed = json.loads(arguments_text) if arguments_text else {}
    except json.JSONDecodeError:
        return ToolCall(
            id=call_id,
            name=name,
            arguments={},
            parse_error="arguments were not valid JSON",
        )
    if not isinstance(parsed, dict):
        return ToolCall(
            id=call_id,
            name=name,
            arguments={},
            parse_error="arguments must be a JSON object",
        )
    return ToolCall(id=call_id, name=name, arguments=parsed)


def tool_calls_from_text(content: str) -> list[ToolCall]:
    """Read a tool call that a small model wrote into the message text."""
    text = _escape_loose_backslashes(_strip_fence(content.strip()))
    found: list[ToolCall] = []
    decoder = json.JSONDecoder()
    index = 0
    cursor = 0
    while cursor < len(text):
        start = text.find("{", cursor)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            cursor = start + 1
            continue
        call = _call_from_object(value, index)
        if call is not None:
            found.append(call)
            index += 1
        cursor = end
    return found


def _call_from_object(value: object, index: int) -> ToolCall | None:
    if not isinstance(value, dict):
        return None
    name = value.get("name")
    arguments = value.get("arguments")
    function = value.get("function")
    if isinstance(function, dict):
        name = function.get("name", name)
        if arguments is None:
            arguments = function.get("arguments")
    if not isinstance(name, str) or not name:
        return None
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return ToolCall(
                id=f"call_{index}",
                name=name,
                arguments={},
                parse_error="arguments were not valid JSON",
            )
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return None
    return ToolCall(id=f"call_{index}", name=name, arguments=arguments)


def _escape_loose_backslashes(text: str) -> str:
    """Keep Windows paths parseable when a model writes invalid JSON escapes."""
    valid = set('"\\/bfnrtu')
    pieces: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] not in valid:
            pieces.append("\\\\")
            index += 1
            continue
        pieces.append(char)
        index += 1
    return "".join(pieces)


def _strip_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
