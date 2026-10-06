"""Keep a local model on a small context and a short reply."""

from __future__ import annotations

from codeharness.config import HarnessConfig


def pace_fields(config: HarnessConfig) -> dict:
    """Fields for one model call. A short reply finishes sooner. Ollama also stays loaded."""
    fields: dict = {"max_tokens": config.response_reserve}
    if _is_ollama(config.base_url):
        fields["keep_alive"] = "30m"
        fields["options"] = {
            "num_ctx": config.context_limit,
            "num_predict": config.response_reserve,
        }
    return fields


def _is_ollama(base_url: str) -> bool:
    lowered = base_url.lower()
    return ":11434" in lowered or "ollama" in lowered
