"""Return the same report the done gate uses."""

from __future__ import annotations

from pathlib import Path

from codeharness.config import HarnessConfig
from codeharness.diagnostics import diagnose, diagnose_root
from codeharness.tools.common import resolve_inside


def run(arguments: dict, root: Path, config: HarnessConfig) -> str:
    del config
    raw = arguments.get("path")
    if isinstance(raw, str) and raw:
        path = resolve_inside(root, raw)
        report = diagnose([path])
    else:
        report = diagnose_root(root)
    return report or "No problems."
