"""Tool registry. Denied tools are left out of the model prompt."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from codeharness.config import HarnessConfig
from codeharness.errors import PathEscape, ToolInputError
from codeharness.tools import (
    apply_patch,
    diagnostics_tool,
    edit_file,
    git,
    list_files,
    read_file,
    search,
    shell,
    skill_tool,
    task_tool,
    todo_tool,
    write_file,
)

Runner = Callable[[dict, Path, HarnessConfig], str]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    run: Runner


def _tool(name: str, description: str, properties: dict, required: list[str], runner: Runner) -> Tool:
    return Tool(
        name=name,
        description=description,
        parameters={"type": "object", "properties": properties, "required": required},
        run=runner,
    )


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        _tool(
            "read_file",
            "Read a line range from a text file. Ask for a smaller range when the file is long.",
            {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
            },
            ["path"],
            read_file.run,
        ),
        _tool(
            "edit_file",
            "Replace one exact old_string with new_string. old_string must match once.",
            {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            ["path", "old_string", "new_string"],
            edit_file.run,
        ),
        _tool(
            "write_file",
            "Create or replace a file in the project. Put the full file text in content. The reply is only the path.",
            {"path": {"type": "string"}, "content": {"type": "string"}},
            ["path", "content"],
            write_file.run,
        ),
        _tool(
            "search",
            "Search file lines for a short exact string. Returns at most 20 hits.",
            {"query": {"type": "string"}, "path": {"type": "string"}},
            ["query"],
            search.run,
        ),
        _tool(
            "list_files",
            "List up to 80 files under a directory.",
            {"path": {"type": "string"}},
            [],
            list_files.run,
        ),
        _tool(
            "shell",
            "Run Python, Java, Node, npm, npx, or a matching compiler in the project directory. Other programs are blocked.",
            {"command": {"type": "string"}},
            ["command"],
            shell.run,
        ),
        _tool(
            "git_status",
            "Show short git status for this project. Read only.",
            {},
            [],
            git.status,
        ),
        _tool(
            "git_diff",
            "Show the unstaged git diff. Pass path to limit it to one file. Read only.",
            {"path": {"type": "string"}},
            [],
            git.diff,
        ),
        _tool(
            "git_add",
            "Stage one file that is already inside the project. Does not commit.",
            {"path": {"type": "string"}},
            ["path"],
            git.add,
        ),
        _tool(
            "git_commit",
            "Commit what is already staged. Does not push and does not stage files.",
            {"message": {"type": "string"}},
            ["message"],
            git.commit,
        ),
        _tool(
            "apply_patch",
            "Apply several exact hunks. Each hunk has path, old_string, and new_string.",
            {
                "hunks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "old_string": {"type": "string"},
                            "new_string": {"type": "string"},
                        },
                    },
                }
            },
            ["hunks"],
            apply_patch.run,
        ),
        _tool(
            "skill",
            "Load one skill body by name. The prompt lists names only.",
            {"name": {"type": "string"}},
            ["name"],
            skill_tool.run,
        ),
        _tool(
            "diagnostics",
            "Check project files the same way the done gate does. Pass path to check one file.",
            {"path": {"type": "string"}},
            [],
            diagnostics_tool.run,
        ),
        _tool(
            "todo",
            "List the checklist, or mark one item done.",
            {"action": {"type": "string"}, "text": {"type": "string"}},
            [],
            todo_tool.run,
        ),
        _tool(
            "task",
            "Run explore, review, or general in a fresh child session. The reply is a short summary.",
            {"agent": {"type": "string"}, "prompt": {"type": "string"}},
            ["agent", "prompt"],
            task_tool.run,
        ),
    )
}


def tools_for_model(permissions: dict[str, str]) -> list[dict]:
    visible = []
    for tool in TOOLS.values():
        if permissions.get(tool.name, "ask") == "deny":
            continue
        visible.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
        )
    return visible


def run_tool(name: str, arguments: dict, root: Path, config: HarnessConfig) -> str:
    tool = TOOLS.get(name)
    if tool is None:
        return f"error: unknown tool {name}"
    try:
        return tool.run(arguments, root, config)
    except (ToolInputError, PathEscape, OSError) as exc:
        return f"error: {exc}"
