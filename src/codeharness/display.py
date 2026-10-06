"""Plain terminal transcript: what you asked, what changed, and whether it worked."""

from __future__ import annotations

import re
import shutil
import sys
from typing import TextIO

from codeharness.catalog import skill_catalog, skill_count, tool_catalog, tool_count
from codeharness.config import HarnessConfig
from codeharness.loop import LoopEvent

_GOLD = "#FFD700"
_AMBER = "#FFBF00"
_BRONZE = "#CD7F32"
_DIM = "#B8860B"
_TEXT = "#FFF8DC"


def _glyph(*rows: str) -> tuple[str, ...]:
    width = max(len(row) for row in rows)
    return tuple(row.ljust(width) for row in rows)


# Block letters for the startup wordmark. Same height, our own shapes.
_LETTERS = {
    "A": _glyph(
        " █████╗ ",
        "██╔══██╗",
        "███████║",
        "██╔══██║",
        "██║  ██║",
        "╚═╝  ╚═╝",
    ),
    "C": _glyph(
        " ██████╗",
        "██╔════╝",
        "██║     ",
        "██║     ",
        "╚██████╗",
        " ╚═════╝",
    ),
    "D": _glyph(
        "██████╗ ",
        "██╔══██╗",
        "██║  ██║",
        "██║  ██║",
        "██████╔╝",
        "╚═════╝ ",
    ),
    "E": _glyph(
        "███████╗",
        "██╔════╝",
        "█████╗  ",
        "██╔══╝  ",
        "███████╗",
        "╚══════╝",
    ),
    "H": _glyph(
        "██╗  ██╗",
        "██║  ██║",
        "███████║",
        "██╔══██║",
        "██║  ██║",
        "╚═╝  ╚═╝",
    ),
    "N": _glyph(
        "███╗   ██╗",
        "████╗  ██║",
        "██╔██╗ ██║",
        "██║╚██╗██║",
        "██║ ╚████║",
        "╚═╝  ╚═══╝",
    ),
    "O": _glyph(
        " ██████╗ ",
        "██╔═══██╗",
        "██║   ██║",
        "██║   ██║",
        "╚██████╔╝",
        " ╚═════╝ ",
    ),
    "R": _glyph(
        "██████╗ ",
        "██╔══██╗",
        "██████╔╝",
        "██╔══██╗",
        "██║  ██║",
        "╚═╝  ╚═╝",
    ),
    "S": _glyph(
        "███████╗",
        "██╔════╝",
        "███████╗",
        "╚════██║",
        "███████║",
        "╚══════╝",
    ),
}


def _compose(word: str) -> list[str]:
    glyphs = [_LETTERS[char] for char in word]
    return [" ".join(glyph[row] for glyph in glyphs) for row in range(len(glyphs[0]))]


def _center(lines: list[str], width: int) -> list[str]:
    centered: list[str] = []
    for line in lines:
        pad = max(width - len(line), 0) // 2
        centered.append((" " * pad) + line)
    return centered


def wordmark_lines(width: int) -> list[str]:
    """Giant CODEHARNESS. One row when it fits, otherwise CODE over HARNESS."""
    full = _compose("CODEHARNESS")
    if len(full[0]) <= width:
        return _center(full, width)
    return _center(_compose("CODE"), width) + _center(_compose("HARNESS"), width)

_MARKS = {
    "prompt": ">",
    "harness": "*",
    "ollama": "@",
    "tool": "+",
    "answer": "=",
    "status": "*",
    "run": ">",
    "compile": "!",
    "launch": ">",
    "working": "*",
    "created": "+",
    "needs": "?",
    "result": "=",
}

_TITLES = {
    "prompt": "Prompt",
    "harness": "Harness",
    "ollama": "Ollama",
    "tool": "Tool",
    "answer": "Answer",
    "status": "Harness",
}


class Console:
    """Print one turn as labeled blocks. Color is used only on a real terminal."""

    def __init__(self, out: TextIO | None = None, color: bool | None = None) -> None:
        self.out = out if out is not None else sys.stdout
        self.color = self.out.isatty() if color is None else color
        self._streamed = ""
        self._shown: set[tuple[str, str]] = set()
        self.used = 0
        self.estimated = False
        self.phase = "ready"

    def banner(self, config: HarnessConfig, session_id: str) -> None:
        width = self._banner_width()
        version = "v0.1.0"
        if not self.color:
            gap = max(width - len("CODEHARNESS") - len(version), 1)
            self._write("CODEHARNESS" + (" " * gap) + version)
        else:
            self._write(self._hex(version.rjust(width), _GOLD))
        art = wordmark_lines(width)
        if self._can_encode("".join(art)):
            for line in art:
                self._write(self._hex(line.ljust(width), _GOLD))
        elif self.color:
            gap = max(width - len("CODEHARNESS") - len(version), 1)
            self._write(self._hex("CODEHARNESS" + (" " * gap) + version, _GOLD))
        border = "+" + ("-" * (width - 2)) + "+"
        self._write(self._hex(border, _BRONZE))
        self._write(self._hex("| [==]", _BRONZE) + "  " + self._hex(config.model or "(no model)", _TEXT))
        self._write(self._hex("| <[]>", _BRONZE) + "  " + self._hex(f"session {session_id}", _DIM))
        self._write(self._hex("Available Tools", _AMBER))
        for line in tool_catalog().splitlines():
            self._write("  " + self._hex(_fit(line, width - 2), _TEXT))
        self._write(self._hex("Available Skills", _AMBER))
        for line in skill_catalog().splitlines():
            self._write("  " + self._hex(_fit(line, width - 2), _TEXT))
        self._write(self._hex("Profile: local", _AMBER) + "  " + self._hex(config.model or "(no model)", _TEXT))
        footer = f"{tool_count()} tools · {skill_count()} skills · /help for commands"
        self._write(self._hex(footer, _DIM))
        self._write(self._hex(border, _BRONZE))
        self._write("Welcome to CodeHarness. Type your message or /help for commands.")
        self._write("")

    def status_bar(
        self,
        *,
        model: str,
        used: int,
        limit: int,
        phase: str,
        title: str,
        estimated: bool,
        width: int | None = None,
    ) -> None:
        self._write(
            format_status(
                model=model,
                used=used,
                limit=limit,
                phase=phase,
                title=title,
                estimated=estimated,
                width=width if width is not None else self._width(),
                color=self.color,
            )
        )

    def prompt_block(self, text: str) -> None:
        self._shown = set()
        self._streamed = ""
        self.block("Prompt", text.strip())

    def event(self, item: LoopEvent) -> None:
        if item.kind == "phase":
            self.phase = item.text or self.phase
            return
        if item.kind == "delta":
            self._status_once("Working", "Thinking")
            return
        if item.kind == "tokens":
            self._note_tokens(item.text)
            return
        if item.kind in {"harness", "ollama"}:
            if item.title == "Result" or _is_problem(item.body or item.text):
                self.block("Result", _plain_problem(item.body or item.text))
            else:
                self._status_once("Working", "Thinking")
            return
        if item.kind == "tool":
            self._show_tool(item)
            return
        if item.kind == "answer":
            self.block("Result", (item.body or item.text).strip())
            return
        if item.kind == "status":
            line = progress_line(item.body or item.text)
            if line:
                self._status_once("Working", line)
            return
        title = item.title or _TITLES.get(item.kind, item.kind)
        self.block(title, item.body or item.text)

    def _status_once(self, title: str, body: str) -> None:
        key = (title, body)
        if key in self._shown:
            return
        self._shown.add(key)
        self.block(title, body)

    def _show_tool(self, item: LoopEvent) -> None:
        body = item.body or item.text
        if "window is running" in body:
            self.block("Result", "The window is open.")
            return
        if body.startswith("Created "):
            path = body.removeprefix("Created ").strip()
            self._status_once("Working", f"Creating {path}")
            self.block("Created", path)
            return
        if body.startswith("Edited "):
            path = body.splitlines()[0].removeprefix("Edited ").strip()
            self._status_once("Working", f"Updating {path}")
            self.block("Created", path)
            return
        if body.startswith("Read "):
            path = body.splitlines()[0].removeprefix("Read ").strip()
            self._status_once("Working", f"┊ Reading {path}")
            return
        if "error:" in body or body.startswith("denied:") or body.startswith("Denied."):
            self.block("Result", _plain_problem(body))
            return
        if body.startswith("Shell"):
            if "exit_code=0" in body:
                return
            self.block("Result", _plain_problem(body))
            return
        first = body.strip().splitlines()[0] if body.strip() else "Done"
        self._status_once("Working", first[:120])

    def block(self, title: str, body: str) -> None:
        tone = title.lower().split()[0]
        mark = _MARKS.get(tone, "*")
        self._write(self._paint(f"{mark} {title}", tone))
        for line in (body or "").splitlines() or [""]:
            painted = line
            if line.startswith("+ "):
                painted = self._paint(line, "add")
            elif line.startswith("- "):
                painted = self._paint(line, "cut")
            self._write(f"  {painted}")
        self._write("")

    def rule(self) -> None:
        width = min(max(shutil.get_terminal_size((72, 24)).columns, 48), 88)
        self._write("-" * width)

    def ask(self, tool_name: str, detail: str) -> bool:
        sentence = approval_sentence(tool_name, detail)
        width = min(self._width(), 72)
        line = "+" + ("-" * (width - 2)) + "+"
        self._write(self._hex(line, _BRONZE))
        self._write(self._hex("| Needs a yes", _AMBER))
        for row in sentence.splitlines():
            self._write(self._hex("| " + row, _TEXT))
        self._write(self._hex(line, _BRONZE))
        try:
            answer = input(self._glyph())
        except EOFError:
            self._write("")
            return False
        return answer.strip().lower() in {"y", "yes"}

    def read_prompt(self) -> str:
        return input(self._glyph())

    def ask_text(self, prompt: str) -> str:
        width = min(max(self._width(), 48), 72)
        line = "+" + ("-" * (width - 2)) + "+"
        self._write(self._hex(line, _BRONZE))
        self._write(self._hex("| One question", _AMBER))
        for row in _wrap(prompt, width - 4):
            self._write(self._hex("| " + row, _TEXT))
        self._write(self._hex(line, _BRONZE))
        try:
            return input(self._glyph()).strip()
        except EOFError:
            self._write("")
            return ""

    def _glyph(self) -> str:
        glyph = "❯ "
        encoding = getattr(self.out, "encoding", None) or "utf-8"
        try:
            glyph.encode(encoding)
        except UnicodeEncodeError:
            return "> "
        return glyph

    def _note_tokens(self, text: str) -> None:
        if "prompt=" not in text:
            return
        try:
            self.used = int(text.split("prompt=", 1)[1].split()[0])
        except (IndexError, ValueError):
            return
        self.estimated = "source=estimated" in text
        if "thinking" not in self.phase:
            self.phase = "running" if "tool_calls=" in text and not text.split("tool_calls=", 1)[-1].startswith("0") else "thinking"

    def _banner_width(self) -> int:
        columns = shutil.get_terminal_size((120, 24)).columns
        return min(max(columns, 76), 160)

    def _can_encode(self, text: str) -> bool:
        encoding = getattr(self.out, "encoding", None) or "utf-8"
        try:
            text.encode(encoding)
        except UnicodeEncodeError:
            return False
        return True

    def _width(self) -> int:
        return min(max(shutil.get_terminal_size((72, 24)).columns, 40), 72)

    def _lines(self, text: str) -> None:
        width = self._width()
        remaining = text
        while remaining:
            if len(remaining) <= width:
                self._write(remaining)
                return
            cut = remaining.rfind(" ", 0, width)
            if cut <= 0:
                cut = remaining.rfind("\\", 0, width)
            if cut <= 0:
                cut = width
            self._write(remaining[:cut].rstrip())
            remaining = remaining[cut:].lstrip(" ")

    def _write(self, text: str) -> None:
        print(text, file=self.out)

    def _hex(self, text: str, color: str) -> str:
        if not self.color:
            return text
        red = int(color[1:3], 16)
        green = int(color[3:5], 16)
        blue = int(color[5:7], 16)
        return f"\033[38;2;{red};{green};{blue}m{text}\033[0m"

    def _paint(self, text: str, tone: str) -> str:
        if not self.color:
            return text
        codes = {
            "title": "1;37",
            "prompt": "1;33",
            "harness": "1;36",
            "ollama": "1;32",
            "tool": "1;34",
            "answer": "1;37",
            "status": "1;36",
            "add": "32",
            "cut": "31",
            "run": "1;33",
            "compile": "1;31",
            "launch": "1;32",
        }
        code = codes.get(tone, "0")
        return f"\033[{code}m{text}\033[0m"


def progress_line(body: str) -> str:
    """A short progress line. Repo maps and stack echoes stay off the screen."""
    text = " ".join(body.split())
    if not text:
        return ""
    lowered = text.lower()
    if lowered.startswith("files:") or "server.py:" in lowered or "suffixes:" in lowered:
        return ""
    if lowered.startswith("local program") or lowered.startswith("realistic web"):
        return ""
    if "do not create a website" in lowered:
        return ""
    if len(text) > 90:
        return ""
    if "planning" in lowered:
        return "Planning"
    if "building" in lowered:
        return "Building"
    if lowered.startswith("wrote ") or lowered.startswith("queued:") or lowered.startswith("project folder:"):
        return text
    if lowered.endswith("branch."):
        return text
    return ""


def format_status(
    *,
    model: str,
    used: int,
    limit: int,
    phase: str,
    title: str,
    estimated: bool,
    width: int,
    color: bool = False,
) -> str:
    """Hermes-style status line. Full at 76 columns, compact from 52, minimal below that."""
    shown = (model or "model")[:26]
    if width < 52:
        return f"{shown}  {phase}"
    safe_limit = limit if limit > 0 else 1
    pct = min(100, int((used / safe_limit) * 100))
    filled = min(10, int(round(pct / 10)))
    bar = "[" + ("#" * filled) + ("-" * (10 - filled)) + "]"
    mark = "~" if estimated else ""
    painted = _paint_bar(bar, pct) if color else bar
    core = f"{shown}  {mark}{used}/{safe_limit}  {painted} {pct}%  {phase}"
    if width < 76 or not title:
        return core
    room = width - len(core) - 2
    badge = title[: max(room, 0)]
    if not badge:
        return core
    return core + "  " + badge


def _paint_bar(bar: str, percent: int) -> str:
    if percent >= 95:
        color = "255;0;0"
    elif percent >= 80:
        color = "255;140;0"
    elif percent >= 50:
        color = "255;215;0"
    else:
        color = "50;205;50"
    return f"\033[38;2;{color}m{bar}\033[0m"


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _fit(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def approval_sentence(tool_name: str, detail: str) -> str:
    """Say what will happen. The tool name stays in the loop, not in the sentence."""
    if tool_name == "write_file":
        return f"Create {detail.strip() or 'a file'}?"
    if tool_name == "edit_file":
        return f"Update {detail.strip() or 'a file'}?"
    if tool_name == "git_add":
        return f"Stage {detail.strip() or 'a file'}?"
    if tool_name == "git_commit":
        message = " ".join(detail.split())
        if len(message) > 80:
            message = message[:80] + "..."
        return f"Commit with message {message or 'this change'}?"
    if tool_name == "shell":
        script = _script_name(detail)
        if script:
            return f"Open {script} in a window?"
        shown = " ".join(detail.split())
        if len(shown) > 80:
            shown = shown[:80] + "..."
        return f"Run {shown or 'this command'}?"
    if tool_name == "web_gui":
        return "Build this as a realistic web app with HTML, CSS, React, Tailwind, and shadcn?"
    if tool_name == "doom_loop":
        return "Try that again?"
    return "Allow this step?"


def _script_name(detail: str) -> str:
    match = re.search(r"([A-Za-z0-9_\-]+\.py)", detail)
    if match is None:
        return ""
    return match.group(1)


def _is_problem(body: str) -> bool:
    lowered = body.lower()
    return "not viable" in lowered or "failed" in lowered or lowered.startswith("could not")


def _plain_problem(body: str) -> str:
    lowered = body.lower()
    if "tkinter" in lowered and ("pip" in lowered or "already part of python" in lowered):
        return "Could not install tkinter. It is already part of Python."
    if "closed immediately" in lowered or "did not stay open" in lowered:
        return "The window closed immediately."
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    useful = [
        line
        for line in lines
        if line.lower() not in {"shell", "denied."} and not line.startswith("Tool ") and len(line) <= 160
    ]
    return useful[0] if useful else "That step failed."
