from codeharness.config import HarnessConfig
from codeharness.run import compile_and_launch, is_run_command, open_window


def test_run_phrases() -> None:
    assert is_run_command("run")
    assert is_run_command("run it")
    assert is_run_command("launch")
    assert is_run_command("Launch it")
    assert not is_run_command("launch the app")


def test_run_compiles_and_launches_app(tmp_path) -> None:
    (tmp_path / "app.py").write_text("print('launched-ok')\n", encoding="utf-8")
    code, report = compile_and_launch(HarnessConfig(project_root=tmp_path, model="test", shell_timeout=15))
    assert code == 0
    assert "Compile passed." in report
    assert "launched-ok" in report
    assert "Launch passed." in report


def test_run_launches_the_newest_program(tmp_path) -> None:
    (tmp_path / "old.py").write_text("print('old')\n", encoding="utf-8")
    (tmp_path / "clock.py").write_text("print('clock-ok')\n", encoding="utf-8")
    code, report = compile_and_launch(HarnessConfig(project_root=tmp_path, model="test", shell_timeout=15))
    assert code == 0
    assert "clock.py" in report
    assert "clock-ok" in report


def test_a_window_that_closes_immediately_is_a_failure(tmp_path) -> None:
    script = tmp_path / "atm_gui.py"
    script.write_text("import tkinter\nprint('gone')\n", encoding="utf-8")
    result = open_window(script, tmp_path)
    assert result.startswith("error:")
    assert "closed immediately" in result


def test_run_skips_launch_when_compile_fails(tmp_path) -> None:
    (tmp_path / "app.py").write_text("def broken(\n", encoding="utf-8")
    code, report = compile_and_launch(HarnessConfig(project_root=tmp_path, model="test", shell_timeout=15))
    assert code == 1
    assert "Compile failed." in report
    assert "Launch skipped." in report
    assert "launched-ok" not in report
