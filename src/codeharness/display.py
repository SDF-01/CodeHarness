"""Plain terminal transcript: what you asked, what changed, and whether it worked."""

from __future__ import annotations

import re
import shutil
import sys
from typing import TextIO

from codeharness.config import HarnessConfig
from codeharness.loop import LoopEvent

_ROBOT = (
    "         ___",
    "        [o_o]",
    "       <[___]>",
    "         | |",
    "        /   \\",
    '    "What should we build?"',
)

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

    def banner(self, config: HarnessConfig, session_id: str) -> None:
        for line in _ROBOT:
            self._write(self._paint(line, "harness"))
        self._write("")
        self._write(self._paint("CodeHarness", "title"))
        self._write("Local coding buddy")
        self._write("")
        self._write("You type at >")
        self._write("  1  Harness lines up the task")
        self._write("  2  Ollama writes the files")
        self._write("  3  You get a short recap, not a code dump")
        self._write("")
        self._write("Prompt > Harness > Ollama > files")
        self._write("")
        self._lines(f"model    {config.model}")
        self._lines(f"ollama   {config.base_url}")
        self._lines(f"project  {config.project_root}")
        self._lines("programs projects/<name>")
        self._lines(f"session  {session_id}")
        self._write("")
        self._write("try      build a clock")
        self._write("plan     look only, no edits")
        self._write("build    create files again")
        self._write("handoff  plan, then build")
        self._write("web      asked before a realistic web app")
        self._write("run      compile, then launch")
        self._write("exit     stop")
        self._write("")

    def prompt_block(self, text: str) -> None:
        self._shown = set()
        self._streamed = ""
        self.block("Prompt", text.strip())

    def event(self, item: LoopEvent) -> None:
        if item.kind == "delta":
            self._status_once("Working", "Thinking")
            return
        if item.kind == "tokens":
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
            self._status_once("Working", f"Reading {path}")
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
        self.block("Needs a yes", approval_sentence(tool_name, detail))
        try:
            answer = input("yes? [y/n] ")
        except EOFError:
            self._write("")
            return False
        return answer.strip().lower() in {"y", "yes"}

    def read_prompt(self) -> str:
        return input("you > ")

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
