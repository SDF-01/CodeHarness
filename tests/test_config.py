from pathlib import Path

import pytest

from codeharness.config import HarnessConfig, load_config, validate_config
from codeharness.errors import ConfigError


def test_load_config_reads_model_and_budget(tmp_path: Path) -> None:
    config_path = tmp_path / "codeharness.json"
    config_path.write_text(
        '{"model": "local-coder", "context_limit": 4096, "response_reserve": 512}',
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project.mkdir()
    config = load_config(config_path, project)
    assert config.model == "local-coder"
    assert config.prompt_budget == 3584
    assert config.project_root == project.resolve()


def test_invalid_permission_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "codeharness.json"
    config_path.write_text('{"permissions": {"shell": "yolo"}}', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config_path, tmp_path)


def test_budget_must_leave_room_for_a_reply(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        validate_config(
            HarnessConfig(context_limit=100, response_reserve=100, project_root=tmp_path, model="test")
        )
