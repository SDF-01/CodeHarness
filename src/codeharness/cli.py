"""Terminal commands: chat, eval, and sessions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from codeharness.commands import handle_turn
from codeharness.config import SETUP_HELP, HarnessConfig, load_config
from codeharness.display import Console
from codeharness.download import pull_model
from codeharness.errors import ConfigError, ModelError
from codeharness.eval import format_result, run_eval
from codeharness.model import OpenAICompatibleClient
from codeharness.review import brief_report
from codeharness.run import compile_and_launch
from codeharness.session import SessionStore, database_path
from codeharness.ui import serve_ui


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codeharness", description="Local coding harness")
    parser.add_argument("--config", help="Path to codeharness.json")
    parser.add_argument("--root", help="Project directory the tools may touch")
    sub = parser.add_subparsers(dest="command", required=True)

    chat = sub.add_parser("chat", help="Coding session: Prompt, then Harness, then Ollama")
    chat.add_argument("--session", help="Resume a saved session id")
    chat.add_argument("--message", help="Run one task and exit")
    chat.set_defaults(func=_chat)

    evaluate = sub.add_parser("eval", help="Score the configured model on small coding tasks")
    evaluate.add_argument("--task", help="Run one task by name")
    evaluate.set_defaults(func=_eval)

    sessions = sub.add_parser("sessions", help="List saved sessions for this project")
    sessions.set_defaults(func=_sessions)

    run_cmd = sub.add_parser("run", help="Compile the project, then launch it")
    run_cmd.set_defaults(func=_run)

    pull = sub.add_parser("pull", help="Download the configured model from the local Ollama server")
    pull.set_defaults(func=_pull)

    view = sub.add_parser("ui", help="Open a local page that shows each harness step")
    view.add_argument("--port", type=int, default=8765)
    view.set_defaults(func=_ui)
    return parser


def _chat(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    if not config.model:
        print(SETUP_HELP, file=sys.stderr)
        return 1
    store = SessionStore(database_path(config.project_root))
    try:
        session = store.get(args.session) if args.session else store.create(config.project_root)
        view = Console()
        view.banner(config, session.id)
        model = OpenAICompatibleClient(config)
        if args.message:
            return _one_turn(store, session, args.message, model, config, view)
        while True:
            try:
                view.status_bar(
                    model=config.model,
                    used=view.used,
                    limit=config.context_limit,
                    phase=view.phase,
                    title=session.title if session.title != "new session" else "",
                    estimated=view.estimated,
                )
                line = view.read_prompt()
            except EOFError:
                print()
                return 0
            if line.strip().lower() in {"exit", "quit"}:
                return 0
            if not line.strip():
                continue
            try:
                _one_turn(store, session, line, model, config, view)
            except ModelError as exc:
                print(f"error: {exc}", file=sys.stderr)
    except KeyError:
        print(f"error: session not found: {args.session}", file=sys.stderr)
        return 1
    except ModelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0


def _one_turn(store, session, text: str, model, config: HarnessConfig, view: Console) -> int:
    view.prompt_block(text)
    return handle_turn(
        store,
        session,
        text,
        model,
        config,
        view.ask,
        view.event,
        reply=view.ask_text,
        choose=view.choose_folder,
    )


def _eval(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    if not config.model:
        print(SETUP_HELP, file=sys.stderr)
        return 1
    try:
        results = run_eval(config, OpenAICompatibleClient(config), task_name=args.task)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ModelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for result in results:
        print(format_result(result))
    return 0 if all(result.passed for result in results) else 1


def _ui(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    if not config.model:
        print(SETUP_HELP, file=sys.stderr)
        return 1
    serve_ui(config, port=args.port)
    return 0


def _sessions(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    store = SessionStore(database_path(config.project_root))
    try:
        rows = store.list_sessions()
    finally:
        store.close()
    if not rows:
        print("no sessions")
        return 0
    for row in rows:
        print(f"{row.id}  {row.updated_at}  {row.title}")
    return 0


def _pull(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    if not config.model:
        print(SETUP_HELP, file=sys.stderr)
        return 1
    try:
        print(pull_model(config))
    except ModelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _run(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    view = Console()
    return _show_run(config, view)


def _show_run(config: HarnessConfig, view: Console) -> int:
    code, report = compile_and_launch(config)
    view.block("Run", brief_report(report))
    return code


def _config_from_args(args: argparse.Namespace) -> HarnessConfig:
    config_path = Path(args.config) if args.config else None
    root = Path(args.root) if args.root else None
    return load_config(config_path=config_path, project_root=root)


