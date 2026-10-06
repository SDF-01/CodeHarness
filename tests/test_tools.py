import sys

from codeharness.config import HarnessConfig
from codeharness.tools import edit_file, run_tool, search, shell, write_file


def test_read_rejects_paths_outside_the_root(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("hidden-value", encoding="utf-8")
    config = HarnessConfig(project_root=project, model="test")
    result = run_tool("read_file", {"path": "../secret.txt"}, project, config)
    assert result.startswith("error:")
    assert "hidden-value" not in result


def test_edit_replaces_one_match_and_rejects_two(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = project / "note.txt"
    target.write_text("alpha\n", encoding="utf-8")
    config = HarnessConfig(project_root=project, model="test")
    edited = edit_file.run(
        {"path": "note.txt", "old_string": "alpha\n", "new_string": "beta\n"},
        project,
        config,
    )
    assert edited.startswith("edited")
    assert "beta" in target.read_text(encoding="utf-8")

    target.write_text("same\nsame\n", encoding="utf-8")
    ambiguous = edit_file.run(
        {"path": "note.txt", "old_string": "same\n", "new_string": "other\n"},
        project,
        config,
    )
    assert "more than once" in ambiguous


def test_write_file_saves_source_and_returns_only_the_path(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = HarnessConfig(project_root=project, model="test")
    source = "def add(a, b):\n    return a + b\n"
    result = write_file.run({"path": "math_utils.py", "content": source}, project, config)
    assert result == "wrote math_utils.py (2 lines)"
    assert "return a + b" not in result
    assert (project / "math_utils.py").read_text(encoding="utf-8") == source


def test_write_file_repairs_escaped_newlines_and_rejects_bad_python(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = HarnessConfig(project_root=project, model="test")
    escaped = 'print("ok")\\nprint("next")\\n'
    written = write_file.run({"path": "ok.py", "content": escaped}, project, config)
    assert written.startswith("wrote")
    assert (project / "ok.py").read_text(encoding="utf-8") == 'print("ok")\nprint("next")\n'
    rejected = write_file.run({"path": "bad.py", "content": "def broken("}, project, config)
    assert rejected.startswith("error:")
    assert "bad.py" in rejected


def test_search_returns_a_short_hit(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("keep\nneedle here\n", encoding="utf-8")
    config = HarnessConfig(project_root=project, model="test")
    result = search.run({"query": "needle"}, project, config)
    assert "a.py:2:" in result


def test_pip_install_of_tkinter_is_rejected(tmp_path) -> None:
    config = HarnessConfig(project_root=tmp_path, model="test", shell_timeout=5)
    result = shell.run({"command": "pip install tkinter"}, tmp_path, config)
    assert result.startswith("error:")
    assert "already part of Python" in result
    assert "exit_code=" not in result


def test_shell_runs_python_in_the_project_root(tmp_path) -> None:
    config = HarnessConfig(project_root=tmp_path, model="test", shell_timeout=5)
    command = f"\"{sys.executable}\" -c \"print('hello-sandbox')\""
    result = shell.run({"command": command}, tmp_path, config)
    assert "hello-sandbox" in result
    assert "exit_code=0" in result


def test_shell_blocks_other_programs_and_chained_commands(tmp_path) -> None:
    config = HarnessConfig(project_root=tmp_path, model="test", shell_timeout=5)
    blocked = shell.run({"command": "powershell -Command Get-Date"}, tmp_path, config)
    assert blocked.startswith("error:")
    assert "blocked" in blocked
    assert "exit_code=" not in blocked
    chained = f"\"{sys.executable}\" -c \"print(1)\" | more"
    chained_result = shell.run({"command": chained}, tmp_path, config)
    assert "chain" in chained_result
    for command in ("java -version", "javac -version", "node -v", "npm -v", "npx -v", "go version", "g++ --version"):
        result = shell.run({"command": command}, tmp_path, config)
        assert "blocked" not in result.lower()
        assert "chain" not in result.lower()
