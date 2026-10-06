"""Check that Python the agent just wrote can be parsed and names resolve."""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

from codeharness.languages import language_of, text_problem

_MODULE_NAMES = {
    "__name__",
    "__file__",
    "__doc__",
    "__package__",
    "__spec__",
    "__annotations__",
    "__loader__",
    "__cached__",
    "__builtins__",
}
_BUILTINS = set(dir(builtins)) | _MODULE_NAMES


def normalize_source(content: str) -> str:
    """Turn a one-line dump of escaped newlines into real source lines."""
    if content.count("\\n") < 2:
        return content
    if content.count("\n") > content.count("\\n"):
        return content
    return content.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")


def python_problem(path: Path) -> str | None:
    if path.suffix.lower() != ".py" or not path.is_file():
        return None
    source = path.read_text(encoding="utf-8")
    try:
        ast.parse(source, filename=path.name)
    except SyntaxError as exc:
        line = exc.lineno or 1
        return f"error: {path.name} line {line}: {exc.msg}. Update the file so it is valid Python."
    return None


def undefined_problem(path: Path) -> str | None:
    """Report a loaded name that is never assigned, imported, or built in."""
    if path.suffix.lower() != ".py" or not path.is_file():
        return None
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=path.name)
    except SyntaxError:
        return None
    if _star_import(tree):
        return None
    bound = set(_BUILTINS)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(node.name)
            bound.update(_arg_names(node.args))
        elif isinstance(node, ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, ast.Lambda):
            bound.update(_arg_names(node.args))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    bound.add(alias.asname or alias.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
    missing: list[tuple[str, int]] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
            continue
        if node.id in bound or node.id in seen:
            continue
        seen.add(node.id)
        missing.append((node.id, node.lineno or 1))
    if not missing:
        return None
    shown = ", ".join(f"{name} (line {line})" for name, line in missing[:8])
    return f"error: {path.name} uses undefined names: {shown}. Define or import them."


def source_problem(path: Path) -> str | None:
    """Problem in a file the harness knows how to check. None means it looks usable."""
    if language_of(path) == "python":
        syntax = python_problem(path)
        if syntax:
            return syntax
        return undefined_problem(path)
    if language_of(path) is None or not path.is_file():
        return None
    return text_problem(path, path.read_text(encoding="utf-8"))


def review_paths(paths: list[Path]) -> str:
    """Problems for source files written this turn."""
    problems = [problem for path in paths if (problem := source_problem(path))]
    return "\n".join(problems)


def _star_import(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
            return True
    return False


def _arg_names(args: ast.arguments) -> set[str]:
    names = {arg.arg for group in (args.posonlyargs, args.args, args.kwonlyargs) for arg in group}
    if args.vararg is not None:
        names.add(args.vararg.arg)
    if args.kwarg is not None:
        names.add(args.kwarg.arg)
    return names


def brief_report(report: str) -> str:
    """Keep the failure headline. Drop long source lines from the terminal."""
    kept: list[str] = []
    for line in report.splitlines():
        if len(line) > 160:
            continue
        if line.startswith((" ", "\t")) and "Error" not in line and "File " not in line:
            continue
        kept.append(line)
    text = "\n".join(kept).strip()
    return text or report.splitlines()[0]
