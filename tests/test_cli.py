from codeharness.cli import main


def test_chat_requires_a_model(tmp_path, capsys) -> None:
    config_path = tmp_path / "codeharness.json"
    config_path.write_text('{"model": ""}', encoding="utf-8")
    code = main(["--config", str(config_path), "--root", str(tmp_path), "chat", "--message", "hi"])
    assert code == 1
    assert "model" in capsys.readouterr().err.lower()


def test_sessions_lists_nothing_at_first(tmp_path, capsys) -> None:
    code = main(["--root", str(tmp_path), "sessions"])
    assert code == 0
    assert "no sessions" in capsys.readouterr().out.lower()
