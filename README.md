# CodeHarness

CodeHarness is a local coding harness. The language model only predicts the next text. The harness is the program around that model: the prompt, the tools, the permission checks, the saved session, and the rule for when to stop.

It talks to any local server that speaks the OpenAI chat completions API. Point it at Ollama, LM Studio, llama.cpp, or another server on your machine. The default setup is Ollama at `http://127.0.0.1:11434/v1`. Chat does not download a model on its own. `codeharness pull` asks a local Ollama server to download the model named in your config.

Python 3.10 or newer. The package itself uses the Python standard library. The only extra install is pytest, and that is a development dependency.

## What a turn does

1. You type a task.
2. The harness builds a short prompt from the session, the matching skills, and the rules.
3. It sends that prompt to the model at temperature 0.
4. If the model calls a tool, the harness checks permission, runs the tool, and sends the result back.
5. That repeats until the model answers in plain text, or the turn hits `max_steps` (default 12).

Reads and searches run immediately. Creating a file, editing a file, staging, committing, and running a shell command ask you first. You answer `y` or `n`.

The CLI names the file that changed. It does not print the source. A new program is judged by checking that program, not by the harness test suite.

## Setup

From the repo root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
copy codeharness.example.json codeharness.json
```

Open `codeharness.json` and set `model` to the name your server exposes. An empty `model` stops the CLI and prints this setup again.

Prepare Ollama and the virtual environment together:

```powershell
.\scripts\harness.ps1 up
```

That starts Ollama if port 11434 is closed, checks that `.venv` exists, and runs `codeharness pull`.

`codeharness.json` stays on your machine. It is listed in `.gitignore` because it holds your local model name, context budget, and check command.

## Commands

```powershell
.\scripts\harness.ps1 test
.\scripts\harness.ps1 eval
.\scripts\harness.ps1 chat
.\scripts\harness.ps1 run
.\scripts\harness.ps1 ui
.\scripts\harness.ps1 chat -Root "C:\path\to\project"
```

| Command | What it does |
|---|---|
| `test` | Runs pytest with a fake model. Ollama is not required. |
| `eval` | Scores the live model on the sample tasks, including a tkinter clock that is checked without opening a window. |
| `chat` | Terminal session. Type a task at `you >`. Type `exit` or `quit` to stop. |
| `run` | Compiles, then starts the newest program in the current project folder. |
| `ui` | Opens http://127.0.0.1:8765/ with the same stages, a token meter, and Allow or Deny. |
| `up` | Starts Ollama if needed, checks `.venv`, and pulls the configured model. |

After `.venv` is activated you can call the installed command directly:

```powershell
.\.venv\Scripts\Activate.ps1
codeharness chat --root .
codeharness pull
codeharness sessions --root .
codeharness chat --session SESSION_ID
```

`chat` hides token counts. The local page shows them.

## Inside chat

| You type | What happens |
|---|---|
| a task, such as `build a clock` | The build agent may create and edit files, after you approve each write. |
| `plan` | Look only. Creates, edits, staging, and commits are denied. |
| `build` | Turns file writes back on. |
| `handoff build a clock` | One plan turn, then a build turn that implements that plan. An empty plan does not build. |
| `run`, `run it`, `launch`, `launch it` | Compile, then start the newest Python, Java, or HTML program. |
| `exit` or `quit` | Ends the session. |

Before a build that has not already named a stack, the harness asks whether you want a realistic web app.

- Yes writes `index.html` with HTML, CSS, React, Tailwind, and shadcn-style pieces. No install step.
- No keeps the program as local Python or Java, and does not create a website.

The local page has the same Plan, Build, Launch, and Handoff actions.

## Languages

The harness can write and check:

| Language | Files | What is checked |
|---|---|---|
| Python | `.py` | Parses, undefined names are reported, then a non-tkinter file runs for about 3 seconds. A tkinter file must stay open. |
| Java | `.java` | The class, interface, enum, or record name must match the file. A file with `main` is compiled and run when `javac` is installed. |
| HTML | `.html`, `.htm` | Must contain markup. A page that opens `<html` must close it. Launch opens it in the browser. |
| CSS | `.css` | Must be non-empty, with balanced braces. |
| JavaScript and JSX | `.js`, `.mjs`, `.cjs`, `.jsx` | Must be non-empty, with balanced brackets. |
| TypeScript | `.ts`, `.tsx` | Same structural check as JavaScript. |

React, Tailwind, and shadcn are not separate compilers here. They are written into `index.html` so the page opens without `npm install`.

A one-line dump of escaped newlines is turned back into real lines before those checks. If the check fails, the error is sent back to the model and the turn does not finish yet.

Shell commands may start Python, Java, `javac`, Node, npm, or npx. Pipes, redirects, backticks, and other programs are blocked. Approval is still required. `pip install` of a module that already ships with Python, such as tkinter, is rejected before it runs.

## Where programs are saved

Generated programs are not written into the harness folder.

- `build an atm` creates `projects/atm/` and writes the files there.
- A follow-up such as `fix the menu` stays in that folder.
- `build a clock` creates `projects/clock/`.
- Launch uses the folder for this session, or the newest folder under `projects/` when you start from the harness itself.

`projects/` is gitignored. Those programs stay on your machine and are not part of this repository. The harness source stays the harness.

## Tools

Each tool returns a short string. A failure is also a string, so the model can try a different action. A path that leaves the project root is rejected. Denied tools are omitted from the prompt.

| Tool | Behavior | Default |
|---|---|---|
| `read_file` | A line range, 200 lines by default, with line numbers | allow |
| `search` | At most 20 matching lines | allow |
| `list_files` | At most 80 paths | allow |
| `write_file` | Create or replace a whole file. The reply is the path and the line count | ask |
| `edit_file` | Replace one exact `old_string`. It does not create a file | ask |
| `shell` | Python, Java, Node, npm, or npx, after you approve | ask |
| `git_status` | Short git status | allow |
| `git_diff` | Unstaged diff, optional path | allow |
| `git_add` | Stage one existing path inside the project | ask |
| `git_commit` | Commit what is already staged. Nothing is pushed | ask |

## Permissions and the doom loop

`PermissionGate` has three rules: `allow`, `deny`, and `ask`.

A doom loop is the same tool call with the same arguments, repeated. After `doom_repeat_limit` attempts (default 3), the harness asks before running it again. If you say no, the model receives an error and can choose another action. Eval denies that extra repeat so a stuck model cannot run forever.

`max_steps` is the hard stop. The default is 12.

## Skills, rules, and agents

Skills live in `src/codeharness/skills/<name>/SKILL.md`. The description decides when a skill is used. Skills are ranked by how many trigger words hit, and the top four bodies are added to the prompt. File order breaks ties.

| Skill | Use when |
|---|---|
| vibe-build | The user asks to build, create, or make a program |
| gui-app | The user wants a desktop window |
| web-app | The user wants HTML, CSS, React, Tailwind, shadcn, or a website |
| java | The user wants Java |
| verify | The user wants a check or a test |
| stdlib | The task mentions the Python standard library or pip |
| opencode | General coding-agent behavior |

Rules live in `src/codeharness/rules`. `alwaysApply: true` is included every turn. A rule with `globs` is included only when a file from this turn matches. If the project has `AGENTS.md`, that text is added as the project rule, capped so it cannot fill the prompt.

Agents live in `src/codeharness/agents`.

- `build` may create and edit files.
- `plan` may not create, edit, stage, or commit.

A failed shell, a failed review, or a failed run stores one lesson on the session. The next step sees only the latest few lessons. The model weights do not change. The files are the coaching this harness can apply on a laptop.

## Context and sessions

The session store keeps every message in SQLite at `.codeharness/sessions.db` in the workspace. The context builder decides what the model actually sees. The history can be complete while the prompt stays small.

`prompt_budget` is `context_limit` minus `response_reserve`. The reserve is room for the reply. When the prompt would pass the budget, the oldest tool results are replaced with a one-line stub. The newest result is kept. The harness does this in code. It does not call the model to summarize.

If the server sends `usage.prompt_tokens` and `usage.completion_tokens`, those numbers are the token meter. If it does not, the harness estimates from character length and labels the line `source=estimated`.

`codeharness chat --session ID` loads a saved history and continues. `codeharness sessions` lists sessions for the workspace.

## Eval

`codeharness eval` copies a fixture into a temporary directory, runs one turn of the same loop, then runs that task's `check.py`. The report is pass or fail, prompt tokens, completion tokens, and tool-call count.

During eval, edits are allowed, new files and shell are denied, and doom-loop repeats are denied. A task can allow `write_file` in its `task.json`. The GUI clock task does that. Its check requires `tkinter` and `mainloop` and does not open a window.

The checked-in pytest suite uses a scripted fake model. It proves the loop. It does not prove that a live model writes a good program.

## Config

`codeharness.example.json` is the template. Copy it to `codeharness.json`.

| Key | Default | Meaning |
|---|---|---|
| `base_url` | `http://127.0.0.1:11434/v1` | OpenAI-compatible server |
| `model` | empty | Model name. Required for chat, eval, and the local page |
| `api_key` | empty | Sent only if your server requires one |
| `context_limit` | 8192 | Prompt budget plus reply reserve |
| `response_reserve` | 1024 | Tokens held back for the reply |
| `max_tool_output_chars` | 4000 | Cap on text returned to the model |
| `max_steps` | 12 | Hard stop for one turn |
| `doom_repeat_limit` | 3 | Repeats of the same call before another ask |
| `request_timeout` | 120 | Seconds for one model call |
| `shell_timeout` | 20 | Seconds for one approved command |
| `check_command` | empty | Extra check when you ask to test the harness |
| `compile_command` | empty | Override for the Python compile step |
| `launch_command` | empty | Override for what launch starts |
| `permissions` | see the example | `allow`, `ask`, or `deny` per tool |

`--root` or `project_root` in config selects the workspace the tools may touch. Chat still places each new program under `projects/<name>/` inside that workspace.

For a smaller model, lower `context_limit` and `max_tool_output_chars`. The same loop fits a small model when the budget is tighter.

## Repository map

```text
src/codeharness/cli.py          commands: chat, eval, sessions, run, pull, ui
src/codeharness/commands.py     plan, build, launch, and handoff, shared by CLI and the page
src/codeharness/loop.py         call the model, run tools, repeat
src/codeharness/context.py      the short prompt the model sees
src/codeharness/model.py        POST /chat/completions
src/codeharness/tools/          the ten tools
src/codeharness/projects.py     one folder per program under projects/
src/codeharness/languages.py    which files the harness can check
src/codeharness/review.py       parse, names, and structure
src/codeharness/run.py          compile and launch
src/codeharness/session.py      SQLite history
src/codeharness/skills/         ranked skill files
src/codeharness/rules/          always-on and glob rules
src/codeharness/agents/         plan and build
src/codeharness/eval/           sample tasks and their checks
scripts/harness.ps1             up, test, eval, chat, run, ui
docs/how-it-works.md            the same loop, file by file
docs/status.md                  what is built today
```

Follow one chat turn in this order:

1. `cli._chat`
2. `commands.handle_turn`
3. `loop.run_turn`
4. `context.build_context`
5. `model.OpenAICompatibleClient.complete`
6. `tools.run_tool`
7. `session.SessionStore.append`

## What this harness does not do

- It does not train or replace the model. A weak local model still writes weak code. The loop can only send failures back.
- It does not run a real language server, a container, or a package installer for the programs it writes.
- It does not push git remotes. `git_commit` commits what is already staged.
- It does not treat the harness pytest suite as proof that a generated program works. Run that program.
- Shell cannot start arbitrary programs. The allowlist is Python, Java, Node, npm, and npx.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

`.\scripts\harness.ps1 test` does the same. The suite uses a fake model.
