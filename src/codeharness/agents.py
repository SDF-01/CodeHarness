"""OpenCode-style agents. Build may edit. Plan may not."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from codeharness.config import HarnessConfig
from codeharness.frontmatter import split_frontmatter

_PACKAGE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Agent:
    name: str
    description: str
    mode: str
    body: str


def load_agents() -> dict[str, Agent]:
    root = _PACKAGE / "agents"
    loaded: dict[str, Agent] = {}
    if not root.is_dir():
        return loaded
    for path in sorted(root.glob("*.md")):
        fields, body = split_frontmatter(path.read_text(encoding="utf-8"))
        name = path.stem
        loaded[name] = Agent(
            name=name,
            description=fields.get("description", ""),
            mode=fields.get("mode", "primary"),
            body=body.strip(),
        )
    return loaded


AGENTS = load_agents()


_READ_ONLY = ("edit_file", "write_file", "apply_patch", "git_add", "git_commit", "shell", "task", "todo")


def config_for_agent(config: HarnessConfig, name: str) -> HarnessConfig:
    """Each agent is a permission set. Denied tools are omitted from its prompt."""
    permissions = dict(config.permissions)
    only_path = ""
    if name == "plan":
        permissions["write_file"] = "allow"
        permissions["edit_file"] = "allow"
        permissions["apply_patch"] = "deny"
        permissions["git_add"] = "deny"
        permissions["git_commit"] = "deny"
        permissions["shell"] = "deny"
        permissions["task"] = "deny"
        only_path = "PLAN.md"
    elif name == "route":
        for tool in ("edit_file", "write_file", "apply_patch", "git_add", "git_commit", "shell", "task"):
            permissions[tool] = "deny"
    elif name == "explore":
        for tool in _READ_ONLY:
            permissions[tool] = "deny"
    elif name == "review":
        for tool in ("write_file", "apply_patch", "git_add", "git_commit", "task", "todo"):
            permissions[tool] = "deny"
    elif name == "general":
        permissions["task"] = "deny"
    elif name == "page":
        permissions["git_add"] = "deny"
        permissions["git_commit"] = "deny"
        permissions["task"] = "deny"
        only_path = "index.html"
    elif name == "api":
        permissions["git_add"] = "deny"
        permissions["git_commit"] = "deny"
        permissions["task"] = "deny"
        only_path = "server.py"
    else:
        return config
    return replace(config, permissions=permissions, only_path=only_path)
