"""Load one skill body when the model asks for it."""

from __future__ import annotations

from pathlib import Path

from codeharness.config import HarnessConfig
from codeharness.playbook import SKILLS
from codeharness.tools.common import require_str


def run(arguments: dict, root: Path, config: HarnessConfig) -> str:
    del root, config
    name = require_str(arguments, "name")
    for skill in SKILLS:
        if skill.name == name:
            return skill.body or skill.description
    known = ", ".join(skill.name for skill in SKILLS)
    return f"error: unknown skill {name}. Known skills: {known}"
