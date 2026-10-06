"""Allow, deny, or ask before a tool runs."""

from __future__ import annotations

from collections.abc import Callable

from codeharness.errors import ConfigError, TurnStopped

AskFunc = Callable[[str, str], bool]


class PermissionGate:
    def __init__(self, rules: dict[str, str], ask: AskFunc) -> None:
        self._rules = rules
        self._ask = ask
        self.halt = ""

    def allow(self, tool_name: str, detail: str) -> bool:
        rule = self._rules.get(tool_name, "ask")
        if rule == "allow":
            return True
        if rule == "deny":
            return False
        if rule == "ask":
            try:
                return self._ask(tool_name, detail)
            except TurnStopped as stopped:
                self.halt = str(stopped).strip() or "stop"
                return False
        raise ConfigError(f"unknown permission rule for {tool_name}: {rule}")
