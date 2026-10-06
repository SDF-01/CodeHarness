"""Load skills and rules for one coding turn.

This is the harness playbook. It does not change the model weights.
A turn keeps name and description for discovery, then adds the body for the best matches.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path

from codeharness.frontmatter import as_bool, split_frontmatter

_PACKAGE = Path(__file__).resolve().parent
_SKILL_ORDER = (
    "vibe-build",
    "gui-app",
    "web-app",
    "fullstack",
    "java",
    "c",
    "cpp",
    "csharp",
    "go",
    "rust",
    "ruby",
    "php",
    "kotlin",
    "swift",
    "sql",
    "structure",
    "engineering",
    "interface",
    "verify",
    "stdlib",
    "opencode",
)
_WHEN_STOP = {
    "the",
    "user",
    "asks",
    "to",
    "for",
    "or",
    "a",
    "an",
    "and",
    "when",
    "with",
    "task",
    "mentions",
}
_AGENTS_CAP = 600
_LESSON_LIMIT = 3
_SKILL_CAP = 4
_BRANCH_SKILL = {"route": "structure", "page": "web-app", "api": "fullstack", "review": "verify"}


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    triggers: tuple[str, ...]


@dataclass(frozen=True)
class Rule:
    description: str
    always_apply: bool
    globs: tuple[str, ...]
    body: str


def load_skills() -> tuple[Skill, ...]:
    loaded: list[Skill] = []
    root = _PACKAGE / "skills"
    for name in _SKILL_ORDER:
        path = root / name / "SKILL.md"
        if not path.is_file():
            continue
        fields, body = split_frontmatter(path.read_text(encoding="utf-8"))
        skill_name = fields.get("name") or name
        if skill_name != name:
            continue
        description = fields.get("description", "")
        loaded.append(
            Skill(
                name=skill_name,
                description=description,
                body=body.strip(),
                triggers=_triggers(description),
            )
        )
    return tuple(loaded)


def load_rules() -> tuple[Rule, ...]:
    root = _PACKAGE / "rules"
    if not root.is_dir():
        return ()
    loaded: list[Rule] = []
    for path in sorted(root.glob("*.md")):
        fields, body = split_frontmatter(path.read_text(encoding="utf-8"))
        globs = tuple(part for part in fields.get("globs", "").split() if part)
        loaded.append(
            Rule(
                description=fields.get("description", ""),
                always_apply=as_bool(fields.get("alwaysApply", "")),
                globs=globs,
                body=body.strip(),
            )
        )
    return tuple(loaded)


def coaching_for(task: str, agent: str = "build") -> str:
    """Return the shared rules and the skills for this turn. A branch loads one skill."""
    if agent in _BRANCH_SKILL:
        selected = [skill for skill in SKILLS if skill.name == _BRANCH_SKILL[agent]]
    else:
        selected = select_skills(task)
    if not selected:
        return ""
    names = ", ".join(skill.name for skill in selected)
    lines = [
        f"Active skills: {names}",
        "Ask the skill tool for a skill body before you rely on it.",
        f"Rules: {always_rule_text()}",
    ]
    for skill in selected:
        lines.append(f"{skill.name}: {skill.description}")
    return "\n".join(lines)


def select_skills(task: str) -> list[Skill]:
    """Rank skills by trigger hits. Keep file order on a tie. Take the top four."""
    words = set(re.findall(r"[a-z0-9]+", task.lower()))
    text = task.lower()
    ranked: list[tuple[int, int, Skill]] = []
    for index, skill in enumerate(SKILLS):
        hits = sum(1 for trigger in skill.triggers if trigger in words or trigger in text)
        hits += _language_hits(skill.name, words, text)
        if hits:
            ranked.append((hits, index, skill))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [skill for _, _, skill in ranked[:_SKILL_CAP]]


def _language_hits(name: str, words: set[str], text: str) -> int:
    """Match short language names as whole words so a letter does not hit every task."""
    hits = 0
    if name == "c" and "c" in words and "c#" not in text and "c++" not in text:
        hits += 1
    if name == "cpp" and ("cpp" in words or "c++" in text):
        hits += 1
    if name == "csharp" and ("csharp" in words or "dotnet" in words or "c#" in text):
        hits += 1
    if name == "go" and ("go" in words or "golang" in words):
        hits += 1
    return hits


def prompt_addons(
    task: str,
    project_root: Path,
    touched: list[str] | None = None,
    lessons: list[str] | None = None,
    agent: str = "build",
) -> str:
    """Text added under the base system prompt. At most four skills are included."""
    parts: list[str] = []
    coaching = coaching_for(task, agent)
    if coaching:
        parts.append(coaching)
    else:
        rules = always_rule_text()
        if rules:
            parts.append(f"Rules: {rules}")
    for rule in glob_rules(touched or []):
        if rule.body:
            parts.append(rule.body)
    project = project_rules(project_root)
    if project:
        parts.append(project)
    if agent == "plan":
        parts.append("Agent: plan. You may write only PLAN.md.")
    elif agent == "route":
        parts.append("Agent: route. Name the branches. Do not create or edit files.")
    elif agent == "page":
        parts.append("Agent: page. Write the page only.")
    elif agent == "api":
        parts.append("Agent: api. Write the API only.")
    elif agent == "review":
        parts.append("Agent: review. Edit a file only if it is wrong. Do not create a new file.")
    elif agent == "explore":
        parts.append("Agent: explore. Read only. Return a short summary.")
    elif agent == "general":
        parts.append("Agent: general. Do the current todo. Do not start another task.")
    recent = [item for item in (lessons or []) if item][-_LESSON_LIMIT:]
    if recent:
        parts.append("Lessons:\n" + "\n".join(f"- {item}" for item in recent))
    return "\n".join(parts)


def always_rule_text() -> str:
    bodies = [rule.body for rule in RULES if rule.always_apply and rule.body]
    if bodies:
        return " ".join(bodies)
    return (
        "Stay inside the project. Write files instead of pasting code. "
        "Fix a failed check before you answer."
    )


def glob_rules(touched: list[str]) -> list[Rule]:
    if not touched:
        return []
    matched: list[Rule] = []
    for rule in RULES:
        if rule.always_apply or not rule.globs:
            continue
        if any(_glob_hit(path, rule.globs) for path in touched):
            matched.append(rule)
    return matched


def project_rules(project_root: Path) -> str:
    path = project_root / "AGENTS.md"
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if len(text) <= _AGENTS_CAP:
        return text
    return text[:_AGENTS_CAP].rstrip() + "..."


def wants_harness_tests(task: str) -> bool:
    text = task.lower()
    return "harness" in text and any(word in text for word in ("test", "pytest", "check"))


def lesson_from_failure(text: str) -> str:
    lower = text.lower()
    if "pip" in lower and "tkinter" in lower:
        return "Do not pip install tkinter."
    if "closed immediately" in lower or "did not stay open" in lower:
        return "A window must stay open. Include mainloop."
    if "undefined names" in lower:
        return "Define or import names before answering."
    if "failed at runtime" in lower or "did not finish" in lower:
        return "Run the file and fix the error before answering."
    if "valid python" in lower or "syntax" in lower:
        return "Fix the Python syntax before answering."
    first = text.strip().splitlines()[0] if text.strip() else "A step failed."
    for prefix in (
        "error:",
        "Shell failed.",
        "Review failed.",
        "Window failed.",
        "Run failed.",
        "Stack failed.",
    ):
        if first.startswith(prefix):
            first = first[len(prefix) :].strip()
    return first[:140]


def _triggers(description: str) -> tuple[str, ...]:
    lower = description.lower()
    clause = lower.split("use when", 1)[-1] if "use when" in lower else lower
    words = re.findall(r"[a-z0-9]+", clause)
    return tuple(word for word in words if word not in _WHEN_STOP and len(word) >= 3)


def _glob_hit(path: str, patterns: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/")
    name = Path(normalized).name
    return any(fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(normalized, pattern) for pattern in patterns)


SKILLS = load_skills()
RULES = load_rules()
