"""Scripted model for harness tests."""

from codeharness.model import Completion


class ScriptedModel:
    def __init__(self, steps: list[Completion]) -> None:
        self.steps = list(steps)
        self.seen_messages: list[list[dict]] = []
        self.seen_tools: list[list[dict]] = []

    def complete(self, messages: list[dict], tools: list[dict], on_delta=None) -> Completion:
        self.seen_messages.append(messages)
        self.seen_tools.append(tools)
        if not self.steps:
            return Completion(content="done", tool_calls=[], prompt_tokens=1, completion_tokens=1)
        return self.steps.pop(0)
