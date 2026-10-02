from codeharness.config import HarnessConfig
from codeharness.context import SYSTEM_PROMPT, build_context
from codeharness.session import StoredMessage


def test_small_history_is_not_pruned(tmp_path) -> None:
    config = HarnessConfig(project_root=tmp_path, model="test")
    built = build_context([StoredMessage(role="user", content="hello")], config)
    assert built.pruned is False
    assert built.messages[0]["content"].startswith(SYSTEM_PROMPT)
    assert "Rules:" in built.messages[0]["content"]
    assert built.messages[1]["content"] == "hello"


def test_old_tool_results_are_pruned_before_the_newest(tmp_path) -> None:
    config = HarnessConfig(
        project_root=tmp_path,
        model="test",
        context_limit=400,
        response_reserve=40,
        max_tool_output_chars=4000,
    )
    body = "x" * 400
    stored = [
        StoredMessage(role="user", content="fix the bug"),
        StoredMessage(role="tool", content=body, tool_name="read_file", tool_call_id="1"),
        StoredMessage(role="tool", content=body, tool_name="search", tool_call_id="2"),
        StoredMessage(role="tool", content="newest result", tool_name="read_file", tool_call_id="3"),
    ]
    built = build_context(stored, config)
    tool_messages = [message for message in built.messages if message["role"] == "tool"]
    assert built.pruned is True
    assert tool_messages[0]["content"].startswith("[pruned tool result:")
    assert tool_messages[-1]["content"] == "newest result"
    assert built.estimated_tokens <= config.prompt_budget
