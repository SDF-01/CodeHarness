"""Durable task state. Chat history is not the record of the work."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCHEMA = 1
STATES = ("pending", "implementing", "verifying", "verified", "failed", "blocked", "unverified")
_NEXT = {
    "pending": {"implementing", "blocked"},
    "implementing": {"verifying", "failed", "blocked", "unverified"},
    "verifying": {"verified", "failed", "unverified", "implementing"},
    "failed": {"implementing", "blocked"},
    "unverified": {"implementing", "verifying"},
    "verified": {"implementing"},
    "blocked": {"pending", "implementing"},
}
_SKIP = {".codeharness", ".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache"}
_BEHAVIORS = (
    (re.compile(r"\b(?:save|saves|saving|saved|persist|persists|persistence)\b", re.I), "save its data", "saves its data"),
    (re.compile(r"\b(?:list|lists|listing)\b", re.I), "list its data", "lists its data"),
    (re.compile(r"\b(?:add|adds|adding)\b", re.I), "add an item", "adds an item"),
    (re.compile(r"\b(?:empty|blank)\b", re.I), "reject empty input", "rejects empty input"),
    (re.compile(r"\binvalid\b", re.I), "reject invalid input", "rejects invalid input"),
)


class TaskTransitionError(ValueError):
    """The requested state change is not allowed."""


def begin_task(root: Path, request: str) -> dict:
    """Open the task for this folder, or record an amendment on the existing one."""
    text = request.strip()
    record = load_task(root)
    if record is None:
        record = {
            "schema": SCHEMA,
            "request": text,
            "amendments": [],
            "requirements": [
                {
                    "id": "r1",
                    "text": text,
                    "acceptance": acceptance_text(text),
                    "procedure": procedure_text(text),
                    "state": "pending",
                }
            ],
            "decisions": [],
            "failures": [],
            "evidence": [],
            "files": {},
            "budget": {"model_calls": 0, "repair_attempts": 0},
        }
    else:
        _invalidate(record, root)
        if text and text != record["request"] and text not in record["amendments"]:
            record["amendments"].append(text)
            record["requirements"].append(
                {
                    "id": f"r{len(record['requirements']) + 1}",
                    "text": text,
                    "acceptance": acceptance_text(text, amendment=True),
                    "procedure": procedure_text(text, amendment=True),
                    "state": "pending",
                }
            )
    for item in record["requirements"]:
        if item["state"] in {"pending", "verified", "failed", "unverified"}:
            move(record, item["id"], "implementing")
    save_task(root, record)
    return record


def add_evidence(root: Path, line: str) -> None:
    record = load_task(root)
    if record is None or not line:
        return
    record["evidence"].append(line)
    if line.startswith("failed:") or line.startswith("error:"):
        record["failures"].append(line)
        record["budget"]["repair_attempts"] = int(record["budget"].get("repair_attempts", 0)) + 1
    save_task(root, record)


def settle_task(root: Path, text: str, checks: list[str] | None = None) -> dict | None:
    """Set the open requirements from checks. Prose from the model is not a pass."""
    record = load_task(root)
    if record is None:
        return None
    for line in checks or []:
        if line and line not in record["evidence"]:
            record["evidence"].append(line)
            if line.startswith("failed:") or line.startswith("error:"):
                record["failures"].append(line)
    record["files"] = file_versions(root)
    target = judge(text, record["evidence"])
    for item in list(record["requirements"]):
        if item["state"] in {"verified", "failed", "unverified", "blocked", "pending"}:
            move(record, item["id"], "implementing")
        if item["state"] == "implementing" and target in {"verified", "failed", "unverified"}:
            move(record, item["id"], "verifying")
        if item["state"] != target:
            move(record, item["id"], target)
    save_task(root, record)
    return record


def judge(text: str, evidence: list[str]) -> str:
    """Return the completion state. Missing, empty, or foreign checks stay unverified or failed."""
    cleaned = text.strip()
    if (
        not cleaned
        or cleaned == "The model returned an empty reply."
        or cleaned.startswith("Stopped after ")
        or cleaned.startswith("Stack failed")
        or cleaned.startswith("Stopped on:")
        or cleaned.startswith("Unverified.")
    ):
        return "failed" if cleaned.startswith("Stopped") or cleaned.startswith("Stack failed") else "unverified"
    latest = ""
    for line in evidence:
        if line.strip() == "passed: launched":
            continue
        if line.startswith(("passed:", "failed:", "error:", "unverified:")):
            latest = line
    if latest.startswith("passed:"):
        return "verified"
    if latest.startswith("unverified:"):
        return "unverified"
    if latest.startswith(("failed:", "error:")):
        return "failed"
    return "unverified"


def move(record: dict, requirement_id: str, state: str) -> None:
    if state not in STATES:
        raise TaskTransitionError(f"unknown state: {state}")
    item = _requirement(record, requirement_id)
    current = item["state"]
    if current == state:
        return
    if state not in _NEXT[current]:
        raise TaskTransitionError(f"{current} cannot move to {state}")
    item["state"] = state


def load_task(root: Path) -> dict | None:
    path = _path(root)
    if not path.is_file():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("schema") != SCHEMA:
        raise TaskTransitionError(f"unsupported task schema: {record.get('schema')}")
    return record


def save_task(root: Path, record: dict) -> None:
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def file_versions(root: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    if not root.is_dir():
        return versions
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _SKIP.intersection(path.parts):
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        versions[path.relative_to(root).as_posix()] = digest
    return versions


def _invalidate(record: dict, root: Path) -> None:
    current = file_versions(root)
    if not record.get("files") or current == record.get("files"):
        return
    record["evidence"] = [line for line in record["evidence"] if not line.startswith("passed:")]
    note = "unverified: files changed after the last check"
    if note not in record["evidence"]:
        record["evidence"].append(note)


def _requirement(record: dict, requirement_id: str) -> dict:
    for item in record["requirements"]:
        if item["id"] == requirement_id:
            return item
    raise TaskTransitionError(f"unknown requirement: {requirement_id}")


def named_behaviors(request: str) -> list[tuple[str, str]]:
    """Return (infinitive, finite) lines for save, list, add, empty, and invalid input."""
    user = request.split("\n\n", 1)[0]
    found: list[tuple[str, str]] = []
    for pattern, infinitive, finite in _BEHAVIORS:
        if pattern.search(user):
            found.append((infinitive, finite))
    return found


def behavior_note(request: str) -> str:
    """A model note for the behavior the request names. Empty when it names none."""
    lines = [infinitive for infinitive, _finite in named_behaviors(request)]
    if not lines:
        return ""
    return "The program must " + _join(lines) + "."


def acceptance_text(request: str, amendment: bool = False) -> str:
    """Requirement acceptance. Named behavior is listed. Otherwise the request stands."""
    lines = [finite for _infinitive, finite in named_behaviors(request)]
    if not lines:
        if amendment:
            return "The change runs and meets this amendment."
        return "The program runs and meets this request."
    return "The program " + _join(["runs", *lines]) + "."


def procedure_text(request: str, amendment: bool = False) -> str:
    lines = [finite for _infinitive, finite in named_behaviors(request)]
    if not lines:
        target = "the amendment" if amendment else "the request"
        return f"Run the program and compare the result with {target}."
    return "Run the program and check that it " + _join(lines) + "."


def check_clause(request: str) -> str:
    """The acceptance half of the one build yes."""
    lines = ["it runs", *[finite for _infinitive, finite in named_behaviors(request)]]
    return "I will check that " + _join(lines) + "."


def _join(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def _path(root: Path) -> Path:
    return root / ".codeharness" / "task.json"
