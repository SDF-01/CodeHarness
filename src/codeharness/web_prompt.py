"""Ask before a build becomes a realistic web app."""

from __future__ import annotations

import re

from codeharness.taskrecord import behavior_note

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
    "Build a full stack app in this project. "
    "Write index.html and server.py. "
    "server.py uses the Python standard library http.server, serves this folder, "
    "and answers GET /api/health with JSON on port 8766. "
    "GET /api/health is a readiness check. "
    "index.html uses HTML, CSS, React, and Tailwind, with shadcn-style button, card, input, and dialog components. "
    "The page must implement the request, not only call /api/health. "
    "No install. Do not use tkinter."
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


_KIND_WORDS = {
    "web": {"website", "web", "webpage", "react", "html", "css", "tailwind", "shadcn", "frontend"},
    "desktop": {"desktop", "tkinter", "window", "gui", "plugin"},
    "cli": {"cli", "terminal", "command"},
    "api": {"api", "backend"},
    "game": {"game"},
}
_HINTS = (
    ({"clock", "timer", "watch"}, "A clock can be a desktop window or a web page."),
    ({"atm", "bank", "shop", "store"}, "This can be a desktop app, a command line tool, or a website."),
    ({"chat", "bot"}, "This can be a command line tool, a desktop app, or a website."),
)
_LEADING = {"lets", "let", "please", "build", "create", "make", "design", "a", "an", "the", "me", "my"}


def task_kind(task: str) -> str:
    """Return web, desktop, cli, api, game, or empty when the task does not say."""
    lowered = task.lower()
    words = set(re.findall(r"[a-z0-9]+", lowered))
    if "do not create a website" in lowered:
        words -= {"website", "web", "webpage"}
    if "full" in words and "stack" in words:
        return "web"
    for kind, names in _KIND_WORDS.items():
        if words & names:
            return kind
    return ""


def kind_question(task: str) -> str:
    """A question that changes with the request. Empty when the kind is already named."""
    if task_kind(task) or not (set(re.findall(r"[a-z0-9]+", task.lower())) & _BUILD_WORDS):
        return ""
    words = set(re.findall(r"[a-z0-9]+", task.lower()))
    hint = ""
    for names, sentence in _HINTS:
        if words & names:
            hint = sentence
            break
    subject = _subject(task)
    if subject:
        ask = f"What kind of software is {subject}: a website, a desktop app, a command line tool, an API, or something else?"
    else:
        ask = "What kind of software is this: a website, a desktop app, a command line tool, an API, or something else?"
    if hint:
        return hint + " " + ask
    return ask


def apply_kind(task: str, answer: str) -> str:
    """Attach the stack the answer names. An unclear answer does not force a website."""
    kind = task_kind(answer) or task_kind(task)
    if kind == "web":
        note = _YES
    elif kind == "desktop":
        note = (
            "Build a local desktop program with tkinter. "
            "One window stays open on the computer with mainloop. "
            "Do not use input(). Do not create a website. Do not write index.html or a browser page."
        )
    elif kind == "cli":
        note = "Build a command line program. Do not create a website."
    elif kind == "api":
        note = (
            "Build a local HTTP API with the Python standard library. "
            "Write server.py and answer GET /api/health with JSON on port 8766. "
            "GET /api/health is a readiness check."
        )
    elif kind == "game":
        note = "Build a local game. Do not assume a website."
    else:
        cleaned = " ".join(answer.split())
        if not cleaned or cleaned == task.strip():
            extra = behavior_note(task)
            if not extra:
                return task
            return task.rstrip() + "\n\n" + extra
        note = "Build it as the user described: " + cleaned
    extra = behavior_note(task)
    if extra:
        note = note.rstrip() + "\n" + extra
    return task.rstrip() + "\n\n" + note


def _subject(task: str) -> str:
    words = re.findall(r"[A-Za-z0-9']+", task)
    while words and words[0].lower().rstrip("'s") in _LEADING:
        words.pop(0)
    return " ".join(words[:4])
