from codeharness.config import HarnessConfig
from codeharness.pace import pace_fields


def test_ollama_stays_loaded_on_a_matching_context() -> None:
    config = HarnessConfig(
        base_url="http://127.0.0.1:11434/v1",
        model="qwen2.5-coder:7b",
        context_limit=4096,
        response_reserve=512,
    )
    fields = pace_fields(config)
    assert fields["max_tokens"] == 512
    assert fields["keep_alive"] == "30m"
    assert fields["options"] == {"num_ctx": 4096, "num_predict": 512}


def test_other_servers_only_cap_the_reply() -> None:
    config = HarnessConfig(base_url="http://127.0.0.1:1234/v1", model="local", response_reserve=256)
    assert pace_fields(config) == {"max_tokens": 256}
