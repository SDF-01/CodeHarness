"""The turn that is running, so a tool can start a child turn."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass

from codeharness.config import HarnessConfig
from codeharness.model import ChatModel

EventHandler = Callable[..., None]
from codeharness.permissions import AskFunc
from codeharness.session import Session, SessionStore

_current: ContextVar[TurnRuntime | None] = ContextVar("codeharness_turn", default=None)


@dataclass
class TurnRuntime:
    store: SessionStore
    session: Session
    model: ChatModel
    config: HarnessConfig
    ask: AskFunc
    on_event: EventHandler | None


def current_runtime() -> TurnRuntime | None:
    return _current.get()


def bind_runtime(runtime: TurnRuntime):
    return _current.set(runtime)


def reset_runtime(token) -> None:
    _current.reset(token)
