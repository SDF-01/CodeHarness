import urllib.error

import pytest

from codeharness.config import HarnessConfig
from codeharness.download import pull_model
from codeharness.errors import ConfigError, ModelError


class _Body:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return self.payload

    def __enter__(self) -> "_Body":
        return self

    def __exit__(self, *args) -> bool:
        return False


def test_pull_skips_a_model_that_is_already_present(tmp_path) -> None:
    seen: list[str] = []

    def opener(request, timeout=None):
        del timeout
        seen.append(request.full_url)
        return _Body(b'{"models":[{"name":"demo:7b"}]}')

    config = HarnessConfig(project_root=tmp_path, model="demo:7b", base_url="http://127.0.0.1:11434/v1")
    assert pull_model(config, opener=opener) == "Model ready: demo:7b"
    assert seen == ["http://127.0.0.1:11434/api/tags"]


def test_pull_downloads_a_missing_model(tmp_path) -> None:
    def opener(request, timeout=None):
        del timeout
        if request.full_url.endswith("/api/tags"):
            return _Body(b'{"models":[]}')
        assert request.data
        return _Body(b'{"status":"success"}\n')

    config = HarnessConfig(project_root=tmp_path, model="demo:7b", base_url="http://127.0.0.1:11434/v1")
    assert pull_model(config, opener=opener) == "Downloaded demo:7b"


def test_pull_reports_a_server_error(tmp_path) -> None:
    def opener(request, timeout=None):
        del request, timeout
        raise urllib.error.URLError("offline")

    config = HarnessConfig(project_root=tmp_path, model="demo:7b", base_url="http://127.0.0.1:11434/v1")
    with pytest.raises(ModelError):
        pull_model(config, opener=opener)


def test_pull_requires_a_model_name(tmp_path) -> None:
    config = HarnessConfig(project_root=tmp_path, model="", base_url="http://127.0.0.1:11434/v1")
    with pytest.raises(ConfigError):
        pull_model(config, opener=lambda request, timeout=None: _Body(b"{}"))
