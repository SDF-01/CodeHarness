"""Download the configured model from a local Ollama server."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from urllib.parse import urlparse

from codeharness.config import HarnessConfig
from codeharness.errors import ConfigError, ModelError

UrlOpener = Callable[..., object]


def ollama_origin(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("base_url must include http:// or https:// and a host")
    return f"{parsed.scheme}://{parsed.netloc}"


def pull_model(config: HarnessConfig, opener: UrlOpener | None = None) -> str:
    """Download `config.model` when the server does not already have it."""
    if not config.model.strip():
        raise ConfigError("Set model before pulling.")
    open_url = opener or urllib.request.urlopen
    origin = ollama_origin(config.base_url)
    names = _model_names(origin, config.request_timeout, open_url)
    if config.model in names:
        return f"Model ready: {config.model}"
    _pull(origin, config.model, config.request_timeout, open_url)
    return f"Downloaded {config.model}"


def _model_names(origin: str, timeout: float, open_url: UrlOpener) -> set[str]:
    payload = _read_json(f"{origin}/api/tags", timeout, open_url)
    models = payload.get("models", [])
    if not isinstance(models, list):
        return set()
    names: set[str] = set()
    for item in models:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            names.add(item["name"])
    return names


def _pull(origin: str, model: str, timeout: float, open_url: UrlOpener) -> None:
    body = json.dumps({"name": model, "stream": True}).encode("utf-8")
    request = urllib.request.Request(
        f"{origin}/api/pull",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    raw = _read_bytes(request, timeout, open_url).decode("utf-8", errors="replace")
    saw_line = False
    for line in raw.splitlines():
        if not line.strip():
            continue
        saw_line = True
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ModelError("the model server returned an unreadable pull status") from exc
        if isinstance(item, dict) and item.get("error"):
            raise ModelError(str(item["error"]))
    if not saw_line:
        raise ModelError("the model server returned an empty pull response")


def _read_json(url: str, timeout: float, open_url: UrlOpener) -> dict:
    request = urllib.request.Request(url, method="GET")
    raw = _read_bytes(request, timeout, open_url)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ModelError("the model server returned unreadable JSON") from exc
    if not isinstance(payload, dict):
        raise ModelError("the model server returned an unexpected payload")
    return payload


def _read_bytes(request: urllib.request.Request, timeout: float, open_url: UrlOpener) -> bytes:
    try:
        response = open_url(request, timeout=timeout)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise ModelError(f"could not reach the model server: {reason}") from exc
    try:
        if hasattr(response, "__enter__"):
            with response as handle:
                return _body(handle)
        return _body(response)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise ModelError(f"could not reach the model server: {reason}") from exc


def _body(handle: object) -> bytes:
    read = getattr(handle, "read", None)
    if read is None:
        raise ModelError("the model server returned an empty response")
    data = read()
    if isinstance(data, str):
        return data.encode("utf-8")
    if isinstance(data, bytes):
        return data
    raise ModelError("the model server returned an empty response")
