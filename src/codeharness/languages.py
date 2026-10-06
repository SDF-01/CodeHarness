"""Languages this harness can write and check."""

from __future__ import annotations

import re
from pathlib import Path

LANGUAGE_NAMES = (
    "Python, Java, C, C++, C#, Go, Rust, Ruby, PHP, Kotlin, Swift, SQL, "
    "HTML, CSS, JavaScript, JSX, and TypeScript"
)

_BY_SUFFIX = {
    ".py": "python",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".swift": "swift",
    ".sql": "sql",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}


def language_of(path: Path) -> str | None:
    return _BY_SUFFIX.get(path.suffix.lower())


def text_problem(path: Path, source: str) -> str | None:
    """Structural problem for a non-Python source file. None means it looks usable."""
    kind = language_of(path)
    if kind == "java":
        return _java_problem(path, source)
    if kind == "go":
        return _balanced_source(path, source, r"\btype\s+([A-Za-z_]\w*)")
    if kind == "csharp":
        return _balanced_source(path, source, r"\b(?:class|struct|interface|record|enum)\s+([A-Za-z_]\w*)")
    if kind in {"c", "cpp", "rust", "ruby", "php", "kotlin", "swift", "sql", "javascript", "typescript"}:
        return _balanced_source(path, source, None)
    if kind == "html":
        return _html_problem(path, source)
    if kind == "css":
        return _css_problem(path, source)
    return None


def _balanced_source(path: Path, source: str, declaration: str | None) -> str | None:
    if not source.strip():
        return f"error: {path.name} is empty."
    if not _balanced(source):
        return f"error: {path.name} has unbalanced brackets."
    if not declaration:
        return None
    names = re.findall(declaration, source)
    if names and path.stem not in names:
        return f"error: {path.name} must declare {path.stem}."
    return None


def _java_problem(path: Path, source: str) -> str | None:
    if not source.strip():
        return f"error: {path.name} is empty."
    if not _balanced(source):
        return f"error: {path.name} has unbalanced braces."
    kind = re.compile(rf"\b(?:class|interface|enum|record)\s+{re.escape(path.stem)}\b")
    if kind.search(source) is None:
        return f"error: {path.name} must declare class {path.stem}."
    return None


def _html_problem(path: Path, source: str) -> str | None:
    if "<" not in source or ">" not in source:
        return f"error: {path.name} is not HTML."
    if "<html" in source.lower() and "</html>" not in source.lower():
        return f"error: {path.name} is missing </html>."
    return None


def _css_problem(path: Path, source: str) -> str | None:
    if not source.strip():
        return f"error: {path.name} is empty."
    if source.count("{") != source.count("}"):
        return f"error: {path.name} has unbalanced braces."
    return None


def _balanced(source: str) -> bool:
    pairs = {")": "(", "]": "[", "}": "{"}
    opening = set(pairs.values())
    stack: list[str] = []
    quote = ""
    i = 0
    while i < len(source):
        char = source[i]
        if quote:
            if char == "\\" and quote != "`":
                i += 2
                continue
            if char == quote:
                quote = ""
            i += 1
            continue
        if char in {'"', "'", "`"}:
            quote = char
            i += 1
            continue
        if char == "/" and i + 1 < len(source) and source[i + 1] == "/":
            newline = source.find("\n", i)
            if newline < 0:
                break
            i = newline
            continue
        if char in opening:
            stack.append(char)
        elif char in pairs:
            if not stack or stack[-1] != pairs[char]:
                return False
            stack.pop()
        i += 1
    return not stack and not quote
