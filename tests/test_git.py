import subprocess

from codeharness.config import HarnessConfig
from codeharness.tools import run_tool


def _repo(path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True)


def test_git_status_add_and_commit(tmp_path) -> None:
    _repo(tmp_path)
    (tmp_path / "note.txt").write_text("hello\n", encoding="utf-8")
    config = HarnessConfig(project_root=tmp_path, model="test", shell_timeout=15)
    status = run_tool("git_status", {}, tmp_path, config)
    assert "note.txt" in status
    assert "exit_code=0" in status
    added = run_tool("git_add", {"path": "note.txt"}, tmp_path, config)
    assert "exit_code=0" in added
    committed = run_tool("git_commit", {"message": "add note"}, tmp_path, config)
    assert "exit_code=0" in committed
    clean = run_tool("git_status", {}, tmp_path, config)
    assert "note.txt" not in clean.split("exit_code=", 1)[0]


def test_git_add_rejects_a_path_outside_the_root(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _repo(project)
    outside = tmp_path / "secret.txt"
    outside.write_text("hidden", encoding="utf-8")
    config = HarnessConfig(project_root=project, model="test", shell_timeout=15)
    result = run_tool("git_add", {"path": "../secret.txt"}, project, config)
    assert result.startswith("error:")
    assert "hidden" not in result


def test_git_status_without_a_repository(tmp_path) -> None:
    config = HarnessConfig(project_root=tmp_path, model="test")
    result = run_tool("git_status", {}, tmp_path, config)
    assert result.startswith("error:")
    assert "not a git repository" in result
