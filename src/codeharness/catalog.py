"""Live tool and skill lists for the CLI banner."""

from __future__ import annotations

from codeharness.playbook import SKILLS
from codeharness.tools import TOOLS

_TOOL_GROUPS = (
    ("file", ("read_file", "write_file", "edit_file", "apply_patch")),
    ("search", ("search", "list_files")),
    ("run", ("shell", "diagnostics")),
    ("git", ("git_status", "git_diff", "git_add", "git_commit")),
    ("agent", ("task", "todo", "skill")),
)

_SKILL_GROUPS = (
    ("build", ("vibe-build", "structure", "engineering", "interface", "opencode")),
    ("desktop", ("gui-app",)),
    ("web", ("web-app", "fullstack")),
    ("language", ("java", "c", "cpp", "csharp", "go", "rust", "ruby", "php", "kotlin", "swift", "sql")),
    ("check", ("verify", "stdlib")),
)


def tool_count() -> int:
    return len(TOOLS)


def skill_count() -> int:
    return len(SKILLS)


def tool_catalog() -> str:
    lines: list[str] = []
    listed: set[str] = set()
    for label, names in _TOOL_GROUPS:
        present = [name for name in names if name in TOOLS]
        listed.update(present)
        if present:
            lines.append(f"{label}: {', '.join(present)}")
    rest = [name for name in TOOLS if name not in listed]
    if rest:
        lines.append(f"other: {', '.join(rest)}")
    return "\n".join(lines)


def skill_catalog() -> str:
    by_name = {skill.name: skill.name for skill in SKILLS}
    lines: list[str] = []
    listed: set[str] = set()
    for label, names in _SKILL_GROUPS:
        present = [name for name in names if name in by_name]
        listed.update(present)
        if present:
            lines.append(f"{label}: {', '.join(present)}")
    rest = [skill.name for skill in SKILLS if skill.name not in listed]
    if rest:
        lines.append(f"general: {', '.join(rest)}")
    return "\n".join(lines)
