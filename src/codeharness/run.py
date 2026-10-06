"""Compile the project, then launch the program that was just written."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from dataclasses import replace
from pathlib import Path

from codeharness.config import HarnessConfig
from codeharness.projects import launch_root
from codeharness.stack import STACK_PORT, listen_port, serves_http
from codeharness.tools import shell

RUN_COMMANDS = {"run", "run it", "launch", "launch it"}
RUNTIME_TIMEOUT = 3


def is_run_command(text: str) -> bool:
    return " ".join(text.strip().lower().split()) in RUN_COMMANDS


def compile_and_launch(config: HarnessConfig) -> tuple[int, str]:
    """Compile the project, then start the newest Python, Java, or HTML program."""
    if not config.launch_command.strip():
        nested = launch_root(config.project_root)
        if nested is not None:
            config = replace(config, project_root=nested)
        stacked = _launch_stack(config)
        if stacked is not None:
            return stacked
        target = _newest_program(config.project_root)
        if target is not None and target.suffix.lower() == ".java":
            return _launch_java(target, config.project_root)
        if target is not None and target.suffix.lower() in {".html", ".htm"}:
            return _launch_html(target)

    compile_command = config.compile_command or (
        f'"{sys.executable}" -m compileall -q -x "\\.venv|\\.git|__pycache__" .'
    )
    compiled = shell.run({"command": compile_command}, config.project_root, config)
    if not _ok(compiled):
        return 1, f"Compile failed.\n{compiled}\nLaunch skipped."

    launch = _launch_command(config)
    if not launch:
        return 0, "Compile passed.\nNo program to launch. Write a Python, Java, or HTML file in the project folder first."

    ok, launched = _start_program(launch, config.project_root)
    status = "Launch passed." if ok else "Launch failed."
    return (0 if ok else 1), f"Compile passed.\n{status}\n{launch}\n{launched}"


def _launch_stack(config: HarnessConfig) -> tuple[int, str] | None:
    """Start server.py and open the page when this folder is a full stack app."""
    root = config.project_root
    server = root / "server.py"
    page = root / "index.html"
    if not server.is_file() or not page.is_file() or not serves_http(server):
        return None
    source = server.read_text(encoding="utf-8", errors="replace")
    port = listen_port(source)
    if port == 0:
        port = STACK_PORT
    subprocess.Popen(
        [sys.executable, str(server)],
        cwd=root.resolve(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    webbrowser.open(f"http://127.0.0.1:{port}/")
    return 0, f"Opened http://127.0.0.1:{port}/ in your browser. The API is running."


def _launch_command(config: HarnessConfig) -> str:
    if config.launch_command.strip():
        return config.launch_command.strip()
    script = _newest_program(config.project_root)
    if script is None:
        return ""
    return f'"{sys.executable}" "{script.name}"'


_PROGRAM_SUFFIXES = {".py", ".java", ".html", ".htm"}


def _newest_program(root: Path) -> Path | None:
    scripts = [
        path
        for path in root.iterdir()
        if path.is_file()
        and path.suffix.lower() in _PROGRAM_SUFFIXES
        and not path.name.startswith("test_")
    ]
    if not scripts:
        return None
    return max(scripts, key=lambda path: path.stat().st_mtime)


def _launch_java(path: Path, root: Path) -> tuple[int, str]:
    if shutil.which("javac") is None or shutil.which("java") is None:
        return 1, "Launch failed.\njavac is not installed."
    compiled = subprocess.run(
        ["javac", path.name],
        cwd=root.resolve(),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if compiled.returncode != 0:
        detail = (compiled.stderr or compiled.stdout or "javac failed").strip()
        return 1, f"Compile failed.\n{detail}\nLaunch skipped."
    ok, launched = _start_argv(["java", path.stem], root)
    status = "Launch passed." if ok else "Launch failed."
    return (0 if ok else 1), f"Compile passed.\n{status}\njava {path.stem}\n{launched}"


def _launch_html(path: Path) -> tuple[int, str]:
    webbrowser.open(path.resolve().as_uri())
    return 0, f"Launch passed.\nOpened {path.name} in the browser."


def _start_argv(argv: list[str], root: Path) -> tuple[bool, str]:
    process = subprocess.Popen(
        argv,
        cwd=root.resolve(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        output, _ = process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        return True, "Launched and left running."
    body = (output or "").strip() or "(no output)"
    return process.returncode == 0, f"{body}\nexit_code={process.returncode}"


def _start_program(command: str, root: Path) -> tuple[bool, str]:
    """Run a short script to completion. Leave a window open when it keeps running."""
    process = subprocess.Popen(
        command,
        shell=True,
        cwd=root.resolve(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        output, _ = process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        return True, "Launched and left running."
    body = (output or "").strip() or "(no output)"
    return process.returncode == 0, f"{body}\nexit_code={process.returncode}"


def _ok(result: str) -> bool:
    return result.rstrip().endswith("exit_code=0")


_SCRIPT = re.compile(r"""(?i)(?:python(?:\.exe)?|py(?:\.exe)?)\s+["']?([^\s"']+\.py)""")


def python_script(command: str, root: Path) -> Path | None:
    """Return a project Python file named in a python command, if there is one."""
    match = _SCRIPT.search(command.strip())
    if match is None:
        return None
    raw = match.group(1)
    candidate = Path(raw)
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    if path.suffix.lower() != ".py" or not path.is_file():
        return None
    return path


def uses_tkinter(path: Path) -> bool:
    if path.suffix.lower() != ".py" or not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    return "tkinter" in text


def probe_java(path: Path, root: Path) -> str:
    """Compile and run a Java file that has a main method. Missing javac is not a model error."""
    if path.suffix.lower() != ".java" or not path.is_file():
        return ""
    source = path.read_text(encoding="utf-8", errors="replace")
    if "void main" not in source:
        return ""
    if shutil.which("javac") is None or shutil.which("java") is None:
        return ""
    with tempfile.TemporaryDirectory() as raw:
        out = Path(raw)
        compiled = subprocess.run(
            ["javac", "-d", str(out), str(path.resolve())],
            cwd=root.resolve(),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if compiled.returncode != 0:
            detail = (compiled.stderr or compiled.stdout or "javac failed").strip().splitlines()
            line = detail[-1] if detail else "javac failed"
            return f"error: {path.name} failed to compile. {line}"
        process = subprocess.Popen(
            ["java", "-cp", str(out), path.stem],
            cwd=root.resolve(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            output, _ = process.communicate(timeout=RUNTIME_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            return f"error: {path.name} ran longer than {RUNTIME_TIMEOUT} seconds."
    if process.returncode == 0:
        return ""
    detail = (output or "").strip().splitlines()
    line = detail[-1] if detail else f"exit_code={process.returncode}"
    return f"error: {path.name} failed at runtime. {line}"


_COMPILED = {
    ".c": ("gcc", ["gcc", "-fsyntax-only"]),
    ".cpp": ("g++", ["g++", "-fsyntax-only"]),
    ".go": ("gofmt", ["gofmt", "-e"]),
    ".rb": ("ruby", ["ruby", "-c"]),
    ".php": ("php", ["php", "-l"]),
    ".swift": ("swiftc", ["swiftc", "-typecheck"]),
}


def probe_compiled(path: Path, root: Path) -> str:
    """Compile a source file when its compiler is installed. A missing compiler is not a model error."""
    spec = _COMPILED.get(path.suffix.lower())
    if path.suffix.lower() == ".rs":
        if shutil.which("rustc") is None or not path.is_file():
            return ""
        with tempfile.TemporaryDirectory() as raw:
            return _compile_result(
                path,
                root,
                ["rustc", "--edition", "2021", "--crate-type", "lib", "--out-dir", raw, str(path.resolve())],
            )
    if path.suffix.lower() == ".kt":
        if shutil.which("kotlinc") is None or not path.is_file():
            return ""
        with tempfile.TemporaryDirectory() as raw:
            return _compile_result(path, root, ["kotlinc", str(path.resolve()), "-d", raw])
    if spec is None or not path.is_file():
        return ""
    tool, argv = spec
    if shutil.which(tool) is None:
        return ""
    return _compile_result(path, root, [*argv, str(path.resolve())])


def _compile_result(path: Path, root: Path, argv: list[str]) -> str:
    compiled = subprocess.run(
        argv,
        cwd=root.resolve(),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if compiled.returncode == 0:
        return ""
    detail = (compiled.stderr or compiled.stdout or "compile failed").strip().splitlines()
    line = detail[-1] if detail else "compile failed"
    return f"error: {path.name} failed to compile. {line}"


def probe_program(path: Path, root: Path) -> str:
    """Run a short Python file. An error or a hang is sent back to the model."""
    process = subprocess.Popen(
        [sys.executable, str(path)],
        cwd=root.resolve(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        output, _ = process.communicate(timeout=RUNTIME_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return f"error: {path.name} did not finish within {RUNTIME_TIMEOUT} seconds."
    if process.returncode == 0:
        return ""
    body = (output or "").strip()
    last = body.splitlines()[-1] if body else f"exit_code={process.returncode}"
    return f"error: {path.name} failed at runtime. {last}"


def open_window(path: Path, root: Path) -> str:
    """Start a tkinter file and leave it running. An instant exit is a failure."""
    command = f'"{sys.executable}" "{path}"'
    process = subprocess.Popen(
        command,
        shell=True,
        cwd=root.resolve(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        output, _ = process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        return f"Opened {path.name}. The window is running."
    body = (output or "").strip()
    detail = f" {body}" if body else ""
    return f"error: {path.name} closed immediately. The window did not stay open.{detail}"
