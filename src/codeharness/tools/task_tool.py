"""Start a child agent. The import of the loop stays inside the function.

run_turn imports the tool registry, and this tool starts another run_turn.
A module-level import would cycle.
"""

from __future__ import annotations

from pathlib import Path

from codeharness.agents import config_for_agent
from codeharness.config import HarnessConfig
from codeharness.runtime import current_runtime
from codeharness.tools.common import require_str

_AGENTS = {"explore", "review", "general"}


def run(arguments: dict, root: Path, config: HarnessConfig) -> str:
    del root
    name = require_str(arguments, "agent")
    prompt = require_str(arguments, "prompt")
    if name not in _AGENTS:
        return f"error: unknown agent {name}"
    runtime = current_runtime()
    if runtime is None:
        return "error: task is unavailable"
    from codeharness.loop import run_turn

    child = runtime.store.create(config.project_root)
    child_config = config_for_agent(config, name)
    result = run_turn(
        store=runtime.store,
        session=child,
        user_text=prompt,
        model=runtime.model,
        config=child_config,
        ask=runtime.ask,
        on_event=runtime.on_event,
    )
    text = result.text.strip() or "The child returned an empty reply."
    return text[:2000]
