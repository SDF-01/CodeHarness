"""Load harness settings from defaults and codeharness.json."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from codeharness.errors import ConfigError

DEFAULT_PERMISSIONS = {
    "read_file": "allow",
    "search": "allow",
    "list_files": "allow",
    "edit_file": "ask",
    "write_file": "ask",
    "shell": "ask",
    "git_status": "allow",
    "git_diff": "allow",
    "git_add": "ask",
    "git_commit": "ask",
    "doom_loop": "ask",
    "apply_patch": "ask",
    "skill": "allow",
    "diagnostics": "allow",
    "todo": "allow",
    "task": "ask",
}

SETUP_HELP = """No model is configured.
Copy codeharness.example.json to codeharness.json and set "model" to the name your local server exposes.
The server should provide an OpenAI-compatible POST /v1/chat/completions endpoint.
Example:
{
  "base_url": "http://127.0.0.1:11434/v1",
  "model": "qwen2.5-coder:7b"
}
"""

_PERMISSION_RULES = {"allow", "ask", "deny"}


@dataclass(frozen=True)
class HarnessConfig:
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = ""
    api_key: str = ""
    context_limit: int = 8192
    response_reserve: int = 1024
    max_tool_output_chars: int = 4000
    project_root: Path = field(default_factory=lambda: Path("."))
    max_steps: int = 12
    doom_repeat_limit: int = 3
    request_timeout: float = 120.0
    shell_timeout: float = 20.0
    check_command: str = ""
    compile_command: str = ""
    launch_command: str = ""
    open_windows: bool = True
    permissions: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_PERMISSIONS))
    only_path: str = ""

    @property
    def prompt_budget(self) -> int:
        return self.context_limit - self.response_reserve


def load_config(config_path: Path | None = None, project_root: Path | None = None) -> HarnessConfig:
    """Load config, then apply a CLI project root when one was passed."""
    path = config_path if config_path is not None else Path.cwd() / "codeharness.json"
    data: dict = {}
    if path.is_file():
        data = _read_json(path)
    elif config_path is not None:
        raise ConfigError(f"config file not found: {path}")

    permissions = dict(DEFAULT_PERMISSIONS)
    raw_permissions = data.get("permissions", {})
    if not isinstance(raw_permissions, dict):
        raise ConfigError("permissions must be an object")
    for name, rule in raw_permissions.items():
        if rule not in _PERMISSION_RULES:
            raise ConfigError(f"invalid permission for {name}: {rule}")
        permissions[str(name)] = str(rule)

    root_value = project_root if project_root is not None else data.get("project_root", ".")
    config = HarnessConfig(
        base_url=_require_str(data, "base_url", "http://127.0.0.1:11434/v1").rstrip("/"),
        model=_require_str(data, "model", ""),
        api_key=_require_str(data, "api_key", ""),
        context_limit=_require_int(data, "context_limit", 8192),
        response_reserve=_require_int(data, "response_reserve", 1024),
        max_tool_output_chars=_require_int(data, "max_tool_output_chars", 4000),
        project_root=Path(str(root_value)),
        max_steps=_require_int(data, "max_steps", 12),
        doom_repeat_limit=_require_int(data, "doom_repeat_limit", 3),
        request_timeout=_require_number(data, "request_timeout", 120),
        shell_timeout=_require_number(data, "shell_timeout", 20),
        check_command=_require_str(data, "check_command", ""),
        compile_command=_require_str(data, "compile_command", ""),
        launch_command=_require_str(data, "launch_command", ""),
        permissions=permissions,
    )
    return validate_config(config)


def validate_config(config: HarnessConfig) -> HarnessConfig:
    if not config.base_url.startswith(("http://", "https://")):
        raise ConfigError("base_url must start with http:// or https://")
    if config.context_limit <= config.response_reserve:
        raise ConfigError("context_limit must be greater than response_reserve")
    if config.response_reserve < 1:
        raise ConfigError("response_reserve must be at least 1")
    if config.max_tool_output_chars < 1:
        raise ConfigError("max_tool_output_chars must be at least 1")
    if config.max_steps < 1:
        raise ConfigError("max_steps must be at least 1")
    if config.doom_repeat_limit < 2:
        raise ConfigError("doom_repeat_limit must be at least 2")
    if config.request_timeout <= 0 or config.shell_timeout <= 0:
        raise ConfigError("timeouts must be greater than 0")
    for name, rule in config.permissions.items():
        if rule not in _PERMISSION_RULES:
            raise ConfigError(f"invalid permission for {name}: {rule}")
    root = config.project_root.expanduser().resolve()
    if not root.is_dir():
        raise ConfigError(f"project root is not a directory: {root}")
    return replace(config, project_root=root)


def config_for_eval(config: HarnessConfig, project_root: Path) -> HarnessConfig:
    """Eval edits a temp copy and does not ask a person or run shell commands."""
    permissions = dict(config.permissions)
    permissions["edit_file"] = "allow"
    permissions["write_file"] = "deny"
    permissions["shell"] = "deny"
    permissions["git_add"] = "deny"
    permissions["git_commit"] = "deny"
    permissions["doom_loop"] = "deny"
    return validate_config(
        replace(config, project_root=project_root, permissions=permissions, open_windows=False)
    )


def _read_json(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file is not valid JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config file must contain a JSON object")
    return raw


def _require_str(data: dict, key: str, default: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string")
    return value


def _require_int(data: dict, key: str, default: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} must be an integer")
    return value


def _require_number(data: dict, key: str, default: float) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{key} must be a number")
    return float(value)
