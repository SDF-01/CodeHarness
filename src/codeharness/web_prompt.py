"""Ask before a build becomes a realistic web app."""

from __future__ import annotations

import re

_BUILD_WORDS = {"build", "create", "make", "design"}
_DECIDED = {
    "react",
    "tailwind",
    "shadcn",
    "html",
    "css",
    "tkinter",
    "desktop",
    "java",
    "javac",
    "cli",
    "terminal",
    "python",
    "website",
    "webpage",
}
WEB_GUI_QUESTION = (
    "Build this as a realistic web app with HTML, CSS, React, Tailwind, and shadcn?"
)
_YES = (
    "Build a realistic web app in this project. "
    "Use HTML, CSS, React, and Tailwind. "
    "Use shadcn-style button, card, input, and dialog components. "
    "Write an index.html that opens without an install. Do not use tkinter."
)
_NO = "Build a local Python or Java program. Do not create a website."


def offer_web_gui(task: str) -> bool:
    """True when a build could be a web app and the user has not chosen a stack."""
    words = set(re.findall(r"[a-z0-9]+", task.lower()))
    if not words & _BUILD_WORDS:
        return False
    if words & _DECIDED:
        return False
    return True


def apply_web_choice(task: str, allowed: bool) -> str:
    note = _YES if allowed else _NO
    return task.rstrip() + "\n\n" + note
