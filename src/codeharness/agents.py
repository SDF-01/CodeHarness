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


def config_for_agent(config: HarnessConfig, name: str) -> HarnessConfig:
    """Plan cannot create or edit files. Other agents keep the configured permissions."""
    if name != "plan":
        return config
    permissions = dict(config.permissions)
    permissions["edit_file"] = "deny"
    permissions["write_file"] = "deny"
    permissions["git_add"] = "deny"
    permissions["git_commit"] = "deny"
    return replace(config, permissions=permissions)
