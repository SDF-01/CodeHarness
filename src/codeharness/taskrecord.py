"""Durable task state. Chat history is not the record of the work."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import sys
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
            "faults": [],
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
        if line.strip() in {"passed: launched", "passed: ran logic.py"}:
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


_PLACE = {"pack", "grid", "place"}
_WIDGETS = {"Button", "Label"}
_SHELL_TITLES = {"my app", "app", "tk", "product"}
_TITLE_SKIP = {
    "a",
    "an",
    "and",
    "api",
    "app",
    "build",
    "create",
    "desktop",
    "for",
    "full",
    "gui",
    "handoff",
    "html",
    "implement",
    "in",
    "it",
    "lets",
    "local",
    "make",
    "me",
    "my",
    "of",
    "please",
    "python",
    "stack",
    "that",
    "the",
    "to",
    "with",
    "write",
    "your",
}


def inspect_program(root: Path, request: str = "") -> str:
    """The file check that runs before a window opens. Empty when the program is real."""
    shape = shape_problem(root, request)
    if shape:
        return shape
    return _placed_problem(root / "app.py", request)


def shape_problem(root: Path, request: str = "") -> str:
    """Empty when logic.py and the window are a real program. Otherwise failed: logic."""
    logic = root / "logic.py"
    app = root / "app.py"
    if not logic.is_file() or not app.is_file():
        return "failed: logic. Write logic.py first, then app.py that imports it and calls mainloop."
    try:
        tree = ast.parse(logic.read_text(encoding="utf-8"))
    except SyntaxError:
        return "failed: logic. logic.py does not parse."
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if not any(not _stub_check(node) for node in functions):
        return "failed: logic. logic.py needs a function for the behavior."
    for node in functions:
        if node.name == "run_check" and not _branches(node):
            return "failed: logic. run_check must branch. A constant return of ok is not a check."
    app_text = app.read_text(encoding="utf-8", errors="replace")
    if not re.search(r"\b(?:import logic|from logic)\b", app_text):
        return "failed: logic. app.py must import logic."
    if "tkinter" not in app_text or "mainloop" not in app_text:
        return "failed: logic. app.py must use tkinter and call mainloop."
    surface = _surface_problem(app_text, request)
    if surface:
        return surface
    owned = _app_owns_behavior(app_text)
    if owned:
        return owned
    return _calculator_problem(root, request, app_text)


def _surface_problem(app_text: str, request: str) -> str:
    lowered = app_text.lower()
    if any(phrase in lowered for phrase in ("unknown command", "enter action", "enter command")):
        return (
            "failed: logic. The window is a command box. "
            "Put the real actions on buttons and show the result in a display."
        )
    words = set(re.findall(r"[a-z0-9]+", request.lower().split("\n\n", 1)[0]))
    if words & {"clock", "watch"}:
        return ""
    calls = len(re.findall(r"\bButton\s*\(", app_text))
    labels = re.findall(r"['\"]([^'\"]{1,12})['\"]", app_text)
    if calls < 4 and not (calls >= 1 and len(labels) >= 4):
        return (
            "failed: logic. The window needs a display and at least four buttons for the real actions. "
            "One entry and a Check button is not the product."
        )
    if "#" not in app_text:
        return "failed: logic. Use a dark background and a blue primary action."
    return ""


def _placed_problem(app: Path, request: str) -> str:
    """Buttons and the display have to be on the window, under the product's name."""
    try:
        tree = ast.parse(app.read_text(encoding="utf-8"))
    except SyntaxError:
        return "failed: logic. app.py does not parse."
    parents = _parents(tree)
    assigns = _name_values(tree)
    labels = 0
    buttons = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_placed(node, parents, tree):
            continue
        kind = _widget_kind(node)
        count = _loop_count(node, parents, assigns)
        if kind == "Label":
            labels += count
        elif kind == "Button":
            buttons += count
    if labels < 1 or buttons < 4:
        return (
            "failed: logic. The window needs a display and at least four buttons on the screen. "
            "Creating a button without pack, grid, or place leaves a blank window."
        )
    title = _title_problem(tree, request)
    if title:
        return title
    if not _has_background(tree):
        return "failed: logic. Set a dark background with configure(bg=) on the window."
    return ""


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    found: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            found[id(child)] = node
    return found


def _name_values(tree: ast.AST) -> dict[str, ast.AST]:
    found: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and isinstance(node.value, (ast.Tuple, ast.List, ast.Name)):
            found[target.id] = node.value
    return found


def _widget_kind(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name) and func.id in _WIDGETS:
        return func.id
    if isinstance(func, ast.Attribute) and func.attr in _WIDGETS:
        return func.attr
    return ""


def _is_placed(call: ast.Call, parents: dict[int, ast.AST], tree: ast.AST) -> bool:
    if not _widget_kind(call):
        return False
    parent = parents.get(id(call))
    if isinstance(parent, ast.Attribute) and parent.attr in _PLACE:
        return True
    if isinstance(parent, ast.Assign) and len(parent.targets) == 1 and isinstance(parent.targets[0], ast.Name):
        name = parent.targets[0].id
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in _PLACE and isinstance(node.func.value, ast.Name) and node.func.value.id == name:
                return True
    return False


def _loop_count(node: ast.AST, parents: dict[int, ast.AST], assigns: dict[str, ast.AST]) -> int:
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, ast.For):
            return _iter_count(current.iter, assigns, set())
        current = parents.get(id(current))
    return 1


def _iter_count(node: ast.AST, assigns: dict[str, ast.AST], seen: set[str]) -> int:
    if isinstance(node, (ast.Tuple, ast.List)):
        return len(node.elts) or 1
    if isinstance(node, ast.Name):
        if node.id in seen:
            return 1
        value = assigns.get(node.id)
        if value is None:
            return 1
        return _iter_count(value, assigns, seen | {node.id})
    if isinstance(node, ast.Call):
        name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
        if name == "enumerate" and node.args:
            return _iter_count(node.args[0], assigns, seen)
        if name == "range" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, int):
            return max(node.args[0].value, 1)
    return 1


def _title_problem(tree: ast.AST, request: str) -> str:
    titles = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != "title":
            continue
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            titles.append(node.args[0].value)
    if not titles:
        return "failed: logic. The window title must name the product."
    for title in titles:
        if " ".join(title.lower().split()) in _SHELL_TITLES:
            return "failed: logic. The window is a blank shell. Name it after the product, not My App."
    words = [
        word
        for word in re.findall(r"[a-z0-9]+", request.lower().split("\n\n", 1)[0])
        if word not in _TITLE_SKIP
    ]
    if words and not any(word in title.lower() for title in titles for word in words):
        return "failed: logic. The window title must name the product."
    return ""


def _has_background(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "configure":
            if any(item.arg == "bg" for item in node.keywords):
                return True
        if isinstance(node, ast.Subscript):
            piece = node.slice
            if isinstance(piece, ast.Constant) and piece.value == "bg":
                return True
    return False


def _app_owns_behavior(app_text: str) -> str:
    """The window may color a button. It may not decide what the product does."""
    try:
        tree = ast.parse(app_text)
    except SyntaxError:
        return "failed: logic. app.py does not parse."
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and not _is_layout_test(node.test):
            return (
                "failed: logic. app.py is deciding the product. "
                "Move that branch into logic.py. A button only calls logic and sets the display."
            )
    return ""


def _is_layout_test(test: ast.AST) -> bool:
    if isinstance(test, ast.BoolOp):
        return all(_is_layout_test(item) for item in test.values)
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False
    left = test.left
    return isinstance(left, ast.Name) and left.id in {"label", "name", "__name__"}


def _calculator_problem(root: Path, request: str, app_text: str) -> str:
    words = set(re.findall(r"[a-z0-9]+", request.lower().split("\n\n", 1)[0]))
    if not words & {"calculator", "calc", "calculate"}:
        return ""
    if not all(re.search(rf"['\"]{digit}['\"]", app_text) for digit in "0123456789"):
        return "failed: logic. The calculator needs a button for every digit from 0 through 9."
    if not re.search(r"['\"]=['\"]", app_text):
        return "failed: logic. The calculator needs an equals button."
    module = _load_logic(root)
    calculate = getattr(module, "calculate", None) if module is not None else None
    if not callable(calculate):
        return "failed: logic. logic.py must define calculate(expression). calculate('1+1') must return 2."
    try:
        added = calculate("1+1")
        multiplied = calculate("2*3")
    except Exception:
        return "failed: logic. calculate('1+1') must return 2."
    if _as_number(added) != 2 or _as_number(multiplied) != 6:
        return "failed: logic. calculate('1+1') must return 2 and calculate('2*3') must return 6."
    return ""


_CALCULATOR_LOGIC = '''import ast


def calculate(expression):
    text = str(expression).replace("x", "*").replace("×", "*").replace("÷", "/")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        return "bad expression"
    try:
        value = _value(tree.body)
    except Exception:
        return "bad expression"
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def apply(shown, key):
    current = "" if shown is None else str(shown)
    if key in {"AC", "Clear"}:
        return "0"
    if key == "Backspace":
        return current[:-1] or "0"
    if key == "=":
        return str(calculate(current))
    if current in {"0", "bad expression"} and str(key)[:1] in "0123456789.":
        return str(key)
    return current + str(key)


def _value(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        number = _value(node.operand)
        return number if isinstance(node.op, ast.UAdd) else -number
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
        left = _value(node.left)
        right = _value(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        return left / right
    raise ValueError("bad expression")
'''


def troubleshoot(root: Path, request: str = "", launch: str = "") -> list[dict]:
    """Open faults. Each one says what is wrong, why, where, and the change that fixes it."""
    window = _window_program(root, request)
    if window:
        faults = _structure_faults(root, request)
    elif (root / "app.py").is_file() or _other_program(root):
        faults = []
    else:
        faults = [
            _fault(
                "The program is incomplete.",
                "logic.py and app.py are missing, so the program cannot run.",
                "logic.py",
                "Write logic.py and app.py. logic.py holds the behavior. app.py imports logic, draws the window, and calls mainloop.",
            )
        ]
    if window and not faults:
        shape = inspect_program(root, request)
        if shape:
            faults.append(_fault_from_line(shape))
    if window and not faults:
        faults.extend(_behavior_faults(root, request))
    if not faults and launch.strip():
        faults.append(_launch_fault(launch))
    for index, fault in enumerate(faults, start=1):
        fault["id"] = f"f{index}"
        fault["state"] = "open"
    return faults


def fault_brief(faults: list[dict]) -> str:
    """The repair prompt. The model gets the cause, not a bare failed line."""
    if not faults:
        return ""
    blocks = [
        "The program is broken. Fix it and leave a working program. A failed check is not the end."
    ]
    for fault in faults:
        blocks.append(
            "\n".join(
                (
                    f"What is wrong: {fault.get('symptom', '')}",
                    f"Why: {fault.get('cause', '')}",
                    f"Where: {fault.get('where', '')}",
                    f"Change: {fault.get('fix', '')}",
                )
            )
        )
    blocks.append("Fix the first fault. Leave the program able to run.")
    return "\n\n".join(blocks)


def save_faults(root: Path, faults: list[dict]) -> None:
    record = load_task(root)
    if record is None:
        return
    record["faults"] = faults
    save_task(root, record)


def close_faults(root: Path) -> None:
    """The open faults were repaired. Keep them so the cause is still on record."""
    record = load_task(root)
    if record is None:
        return
    for fault in record.get("faults") or []:
        fault["state"] = "fixed"
    save_task(root, record)


def close_known_faults(root: Path, request: str) -> bool:
    """Repair breaks the harness can see without asking the model. True when a file changed."""
    return write_calculator_logic(root, request) or ensure_window(root)


def ensure_window(root: Path) -> bool:
    """Open the window the file already built. A defined main that never runs still exits."""
    app = root / "app.py"
    if not app.is_file():
        return False
    text = app.read_text(encoding="utf-8")
    if "tkinter" not in text:
        return False
    updated = text
    if "mainloop" not in updated and re.search(r"\broot\s*=", updated):
        updated = _with_mainloop(updated)
    if re.search(r"(?m)^def main\b", updated) and not _calls_main(updated):
        updated = updated.rstrip() + "\n\nmain()\n"
    if updated == text:
        return False
    if not updated.endswith("\n"):
        updated += "\n"
    app.write_text(updated, encoding="utf-8")
    return True


def write_calculator_logic(root: Path, request: str) -> bool:
    """Replace a stub calculator with working math and keep the window open."""
    words = set(re.findall(r"[a-z0-9]+", request.lower().split("\n\n", 1)[0]))
    if not words & {"calculator", "calc", "calculate"}:
        return False
    changed = False
    module = _load_logic(root)
    calculate = getattr(module, "calculate", None) if module is not None else None
    apply = getattr(module, "apply", None) if module is not None else None
    math_ok = False
    if callable(calculate):
        try:
            math_ok = _as_number(calculate("1+1")) == 2 and _as_number(calculate("2*3")) == 6
        except Exception:
            math_ok = False
    if not math_ok or not callable(apply):
        root.mkdir(parents=True, exist_ok=True)
        (root / "logic.py").write_text(_CALCULATOR_LOGIC, encoding="utf-8")
        changed = True
    if ensure_window(root):
        changed = True
    return changed


def _window_program(root: Path, request: str) -> bool:
    """A desktop pair is diagnosed. A print script is launched as it is."""
    app = root / "app.py"
    logic = root / "logic.py"
    if app.is_file() and logic.is_file():
        return True
    if app.is_file() and "tkinter" in app.read_text(encoding="utf-8"):
        return True
    words = set(re.findall(r"[a-z0-9]+", request.lower().split("\n\n", 1)[0]))
    named = bool(words & {"desktop", "window", "gui", "tkinter", "clock", "calculator", "calc", "calculate"})
    return named and not _other_program(root)


def _other_program(root: Path) -> bool:
    if not root.is_dir():
        return False
    return any(
        path.is_file() and path.suffix.lower() == ".py" and path.name not in {"logic.py", "app.py"}
        for path in root.iterdir()
    )


def _structure_faults(root: Path, request: str) -> list[dict]:
    logic = root / "logic.py"
    app = root / "app.py"
    if logic.is_file() or app.is_file():
        missing = [path.name for path in (logic, app) if not path.is_file()]
        if missing:
            names = " and ".join(missing)
            return [
                _fault(
                    "The program is incomplete.",
                    f"{names} is missing, so the program cannot run.",
                    missing[0],
                    "Write " + names + ". logic.py holds the behavior. app.py imports logic, draws the window, and calls mainloop.",
                )
            ]
    if not logic.is_file() or not app.is_file():
        return []
    faults: list[dict] = []
    for path in (logic, app):
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            faults.append(
                _fault(
                    f"{path.name} cannot run.",
                    f"{path.name} does not parse: {exc.msg}.",
                    path.name,
                    f"Make {path.name} valid Python.",
                )
            )
    if faults:
        return faults
    defined = _function_names(logic)
    missing_calls = [name for name in _logic_calls(app) if name not in defined]
    if missing_calls:
        called = ", ".join(f"logic.{name}" for name in missing_calls)
        have = ", ".join(sorted(defined)) or "no functions"
        faults.append(
            _fault(
                f"The window calls {called}, and that function is not there.",
                f"app.py calls {called}. logic.py defines {have}.",
                "logic.py",
                f"Define {missing_calls[0]} in logic.py and make the button call it.",
            )
        )
    app_text = app.read_text(encoding="utf-8")
    if "tkinter" in app_text and "mainloop" not in app_text:
        faults.append(
            _fault(
                "The window never opens.",
                "app.py builds a window and never calls mainloop, so the program exits before the window can stay open.",
                "app.py",
                "Call root.mainloop() after the widgets are placed, and call main() so that line runs.",
            )
        )
    elif re.search(r"(?m)^def main\b", app_text) and not _calls_main(app_text):
        faults.append(
            _fault(
                "The window never opens.",
                "app.py defines main() and never calls it, so mainloop does not run.",
                "app.py",
                "Call main() after the function so the window opens.",
            )
        )
    words = set(re.findall(r"[a-z0-9]+", request.lower().split("\n\n", 1)[0]))
    if words & {"calculator", "calc", "calculate"}:
        module = _load_logic(root)
        calculate = getattr(module, "calculate", None) if module is not None else None
        if not callable(calculate):
            faults.append(
                _fault(
                    "The calculator does not compute.",
                    "logic.py has no calculate(expression). calculate('1+1') must return 2.",
                    "logic.py",
                    "Define calculate(expression) so calculate('1+1') returns 2 and calculate('2*3') returns 6.",
                )
            )
        else:
            try:
                added = calculate("1+1")
                multiplied = calculate("2*3")
                math_ok = _as_number(added) == 2 and _as_number(multiplied) == 6
            except Exception as exc:
                added = exc
                math_ok = False
            if not math_ok:
                faults.append(
                    _fault(
                        "The calculator does not compute.",
                        f"calculate('1+1') returned {added!r}. It must return 2, and calculate('2*3') must return 6.",
                        "logic.py",
                        "Make calculate do the arithmetic. Do not return a constant.",
                    )
                )
    return faults


def _behavior_faults(root: Path, request: str) -> list[dict]:
    pairs = named_behaviors(request)
    if not pairs:
        return []
    module = _load_logic(root)
    check = getattr(module, "run_check", None) if module is not None else None
    if not callable(check):
        return [
            _fault(
                "A named behavior was not checked.",
                "logic.py does not define run_check, so the requested behavior cannot be proven.",
                "logic.py",
                "Define run_check(name) and return ok only when that behavior works.",
            )
        ]
    names = {
        "save its data": "save",
        "list its data": "list",
        "add an item": "add",
        "reject empty input": "empty",
        "reject invalid input": "invalid",
    }
    faults: list[dict] = []
    for infinitive, _finite in pairs:
        key = names[infinitive]
        try:
            result = str(check(key)).strip()
        except Exception as exc:
            faults.append(
                _fault(
                    f"The {key} behavior failed.",
                    f"logic.run_check({key!r}) raised {exc.__class__.__name__}: {exc}.",
                    "logic.py",
                    f"Make run_check({key!r}) return ok when that behavior works.",
                )
            )
            continue
        if result.lower() != "ok":
            faults.append(
                _fault(
                    f"The {key} behavior failed.",
                    f"logic.run_check({key!r}) returned {result!r}, not ok.",
                    "logic.py",
                    f"Make run_check({key!r}) return ok only when that behavior works.",
                )
            )
    return faults


def _launch_fault(launch: str) -> dict:
    cause = " ".join(launch.split())
    return _fault(
        "The program did not stay running.",
        cause or "The launch failed.",
        "app.py",
        "Repair the file named in the error and keep the window open with mainloop.",
    )


def _fault(symptom: str, cause: str, where: str, fix: str) -> dict:
    return {"symptom": symptom, "cause": cause, "where": where, "fix": fix, "state": "open"}


def _fault_from_line(line: str) -> dict:
    cause = line.removeprefix("failed: logic. ").removeprefix("failed: ").removeprefix("error: ").strip()
    where = "app.py" if "app.py" in line else "logic.py" if "logic.py" in line else "app.py"
    symptom = "The program does not meet the request."
    if "command box" in cause.lower():
        symptom = "The window is not the product."
    elif "mainloop" in cause.lower() or "never opens" in cause.lower():
        symptom = "The window never opens."
    return _fault(symptom, cause, where, cause)


def _function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _logic_calls(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, ast.Load):
            continue
        if isinstance(node.value, ast.Name) and node.value.id == "logic" and node.attr not in found:
            found.append(node.attr)
    return found


def _calls_main(text: str) -> bool:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return bool(re.search(r"(?m)^main\(\)\s*$", text))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "main":
            return True
    return False


def _with_mainloop(text: str) -> str:
    stripped = text.rstrip()
    if stripped.endswith("root.update()"):
        return stripped[: -len("root.update()")] + "root.mainloop()\n"
    return stripped + "\n    root.mainloop()\n"


def _as_number(value: object) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _load_logic(root: Path):
    logic = root / "logic.py"
    if not logic.is_file():
        return None
    sys.modules.pop("codeharness_logic", None)
    spec = importlib.util.spec_from_file_location("codeharness_logic", logic)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module


def _stub_check(node: ast.AST) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run_check" and not _branches(node)


def _branches(node: ast.AST) -> bool:
    return any(isinstance(item, (ast.If, ast.IfExp, ast.Match)) for item in ast.walk(node))


def probe_logic(root: Path, request: str) -> list[str]:
    """Call logic.run_check for each named behavior. A window opening is not this check."""
    pairs = named_behaviors(request)
    if not pairs:
        return []
    module = _load_logic(root)
    if module is None:
        return ["failed: logic"]
    check = getattr(module, "run_check", None)
    if not callable(check):
        return ["failed: logic"]
    names = {
        "save its data": "save",
        "list its data": "list",
        "add an item": "add",
        "reject empty input": "empty",
        "reject invalid input": "invalid",
    }
    lines: list[str] = []
    for infinitive, _finite in pairs:
        key = names[infinitive]
        try:
            result = str(check(key)).strip().lower()
        except Exception:
            lines.append(f"failed: {key}")
            continue
        if result == "ok":
            lines.append(f"passed: {key}")
        else:
            lines.append(f"failed: {key}")
    return lines


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
