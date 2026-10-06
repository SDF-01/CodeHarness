# CodeHarness status

Date: 6 October 2026
Version: 0.1.0
Scope: the local coding harness in this repo

This report describes what the program does today. It is grounded in the source under `src/codeharness`, the tests under `tests`, and a pytest run on this machine.

## Where it stands

CodeHarness is a working local coding loop. The harness is the lead. The model works one todo. The done gate decides when to stop. A build writes a repo map, an optional `server.py` and `index.html` skeleton, and `PLAN.md`, then asks once before the file writes. Each todo is a fresh child. Diagnostics, a review child, and a live `GET /api/health` check can send that todo back. A passed gate appends a short note to the project `AGENTS.md`.

Generated programs are saved under `projects/<name>/`. The harness folder stays the harness. `codeharness pull` downloads the configured model from a local Ollama server. `scripts/harness.ps1 up` starts Ollama, checks `.venv`, and runs that pull. Chat does not download on its own. Chat, eval, and the local page still need the server. The unit tests do not.

Pytest on 6 October 2026: **108 passed** in 7.84s, using a fake model. That suite proves the lead, the one-todo child, the patch tool, diagnostics, undo, the health gate, the queue, the CLI banner, the loop, permissions, sessions, handoff, project folders, the full stack branch chain, and the eval fixtures. It does not prove that a live model writes a good program. A new program is judged by running that program.

## What you can run

| Command | What it does |
|---|---|
| `.\scripts\harness.ps1 up` | Start Ollama if port 11434 is closed, confirm `.venv`, pull the model |
| `.\scripts\harness.ps1 test` | Pytest with a fake model |
| `.\scripts\harness.ps1 eval` | Score the live model on the sample tasks, including a GUI clock |
| `.\scripts\harness.ps1 chat` | Terminal session. Optional `-Root` for another project |
| `.\scripts\harness.ps1 ui` | Local page at http://127.0.0.1:8765/ |
| `.\scripts\harness.ps1 run` | Compile the project, then launch the newest program |
| `codeharness pull` | Download the configured model from Ollama |
| `codeharness sessions` | List saved sessions for the project |

Inside `chat`:

- `❯` is the prompt. `/help`, `/tools`, `/skills`, `/status`, `/context`, `/plan`, `/build`, `/undo`, and `/sessions` never call the model.
- `plan` may write only `PLAN.md`. `build` can create and edit files again.
- A build task goes through the lead. `handoff` plus a local task runs a plan turn, then a build turn that implements the plan. A full stack handoff runs route, page, API, and review as separate short sessions in the same project folder.
- A message typed while a turn is running is queued. `undo` restores the latest snapshot.
- `run`, `run it`, `launch`, and `launch it` compile, then start the program just written. A window that stays open is left running. A window that closes immediately is sent back so the model can repair it.
- `exit` or `quit` ends the session.
- Reads, searches, git status, and git diff run immediately. New files, edits, git add, git commit, and shell commands ask `y` or `n`. Shell may only start the current Python interpreter.

## Built pieces

### Model client

`src/codeharness/model.py` posts to `/chat/completions` at temperature 0. The reply can be plain text, tool calls, or both. Streaming deltas are shown while the model writes. Tool calls pasted as text, including Windows paths, are parsed as tool calls. Token counts come from the server when it sends `usage`. Otherwise the harness estimates from character length and labels the source `estimated`. An empty `model` in config stops the CLI and prints how to set one.

### Tools

Ten tools, in `src/codeharness/tools`. Each returns a short string. A failure is also a string, so the model can try something else.

| Tool | Behavior | Default permission |
|---|---|---|
| `read_file` | Line range, 200 lines by default, with line numbers | allow |
| `search` | At most 20 matching lines | allow |
| `list_files` | At most 80 paths | allow |
| `write_file` | Create or replace a whole file. Reply is the path and line count | ask |
| `edit_file` | Replace one exact `old_string`. Does not create a file | ask |
| `shell` | Python, Java, Node, npm, npx, or a matching compiler. Pipes and other programs are blocked | ask |
| `git_status` | Short git status | allow |
| `git_diff` | Unstaged diff, optional path | allow |
| `git_add` | Stage one path inside the project | ask |
| `git_commit` | Commit what is already staged. No push | ask |

Paths that leave the project root are rejected. `pip install` of a standard-library module such as tkinter is rejected before it runs. Denied tools are omitted from the prompt.

### Permissions and doom loop

`PermissionGate` uses `allow`, `deny`, and `ask`. The same tool call with the same arguments is a doom loop. After `doom_repeat_limit` attempts (default 3), the harness asks before running it again. `max_steps` (default 12) is the hard stop. Eval denies that extra repeat automatically.

### One turn

`run_turn` in `src/codeharness/loop.py` is the loop:

1. Save the user message.
2. Build a prompt that fits the token budget.
3. Call the model.
4. Run each tool after the permission check.
5. If the model tries to finish while Python it just wrote does not parse, or uses an undefined name, send that error back.
6. If a shell command failed, block the answer until a later command works or the step limit hits.
7. If a new tkinter file was written and the window closed immediately, send that back.
8. If a new Python file exits with an error, or does not finish in about 3 seconds, send that back. A Java file with `main` is compiled and run the same way when `javac` is installed. HTML, CSS, JavaScript, JSX, and TypeScript are checked for structure.
9. Otherwise store the plain-text answer and stop.

`check_command` runs only when you ask to test the harness, or when the turn changed a file that is not a new Python program. Repo pytest is not the check for a new app.

### Context

`build_context` always starts with a short system prompt, then the session. `prompt_budget` is `context_limit` minus `response_reserve` (defaults 8192 and 1024). When the prompt would pass the budget, the oldest tool results become a one-line stub. The newest result stays. The harness does not call the model to summarize.

### Sessions

SQLite at `.codeharness/sessions.db` in the project root. Each project has its own database. A session stores messages, a title, the current agent (`build` or `plan`), and up to a few short lessons from failed shells or failed reviews. `codeharness chat --session ID` continues that history.

### Skills, rules, and agents

These are markdown files. They do not change model weights.

Skills in `src/codeharness/skills`. A turn ranks them by trigger hits and adds at most four bodies. A build that has not already chosen a stack asks whether you want a realistic web app first:

| Skill | When it matches |
|---|---|
| vibe-build | build, create, make, clock, app, game, widget, page |
| gui-app | desktop window, clock |
| web-app | html, css, react, tailwind, shadcn, website, webpage, web |
| java | java, javac |
| verify | fix, bug, error, fail, broken, test, check |
| stdlib | pip, install, tkinter, stdlib |
| opencode | edit, change, update, refactor, add, implement |

Rules in `src/codeharness/rules`:

- `viable-program.md` applies every turn.
- `python-files.md` applies when the turn touches a `.py` file.

If the project has `AGENTS.md`, that text is included, capped so it cannot fill the prompt.

Agents in `src/codeharness/agents`:

- `build` may create and edit files. This is the default.
- `plan` may read. `write_file`, `edit_file`, `git_add`, and `git_commit` are denied.
- `handoff` runs plan, then switches to build and implements the plan text, unless the task is a full stack app. Then it runs route, page, API, and review. An empty plan or an empty route does not start the next step.

### Review and launch

`review.py` checks source the agent just wrote. Python is parsed and undefined names are reported. Java, HTML, CSS, JavaScript, JSX, and TypeScript are checked for structure. A full stack task must also include `index.html` and `server.py`, and the page must call `/api/`. A one-line dump of escaped newlines is turned back into real source before that check. A non-tkinter Python file, and a Java file with `main`, then run for about 3 seconds. An HTTP server is left running. `run.py` launches the newest Python, Java, or HTML program, or starts `server.py` and opens the page when both files are present.

### Local page

`codeharness ui` serves one page on this computer. It shows the same stages as the terminal (prompt, harness, model, tool cards, result), a token meter, Allow or Deny, and the current agent. Plan, Build, Launch, and Handoff use the same commands as the terminal. One session, one turn at a time.

### Eval

Three fixtures:

- `add_function`: add a function beside an existing one.
- `fix_off_by_one`: fix an index bug.
- `gui_clock`: write a tkinter clock. The check looks for `tkinter` and `mainloop` and does not open a window.

`codeharness eval` copies a fixture to a temp directory, runs one turn, then runs `check.py`. During eval, edits are allowed, `write_file` and shell are denied, and doom-loop repeats are denied. `gui_clock` allows `write_file` for that task only. Eval does not open windows. The checked-in tests use a scripted model. Live scores depend on whatever server `codeharness.json` points at.

## Programs written with it

These files sit in the repo root. They are sample programs the harness produced. They are not part of the harness package:

- `clock.py`
- `digital_clock.py`
- `atm.py`
- `atm_gui.py`

## Config

`codeharness.json` (copy from `codeharness.example.json`) holds the server URL, model name, context budget, step limit, timeouts, optional `check_command`, `compile_command`, and `launch_command`, and per-tool permissions. This repo's README says the working copy points at Ollama with `qwen2.5-coder:7b`. The example file ships with an empty `model` so a fresh copy does not assume a download.

Runtime dependencies: the Python standard library. Dev extra: pytest. Python 3.10 or newer.

## Limits that remain

- The shell allowlist allows Python, Java, Node, npm, npx, and the matching compilers. It does not stop code the user already approved. There is no container.
- `codeharness pull` talks to Ollama. It does not download weights for LM Studio or llama.cpp. Chat does not pull on its own.
- Name checks are a scope walk in this process. There is no language-server process.
- Git tools can status, diff, add, and commit. They do not push.
- Handoff is one plan turn, then one build turn in the same session. There is no third agent.
- Old tool output is still stubbed. The model is not asked to summarize.
- Skill matching is still trigger overlap, now ranked, capped at four.
- Eval scores three fixtures, including one GUI source check. It does not score a free-form build or open a window.
- A live model can still write a program that passes the short run and fails later. The loop sends parse errors, undefined names, failed commands, instant-close windows, and runtime errors back.

## How to read the code

Follow one chat turn in this order:

1. `cli._chat`
2. `commands.handle_turn`
3. `loop.run_turn`
4. `context.build_context`
5. `model.OpenAICompatibleClient.complete`
6. `tools.run_tool`
7. `session.SessionStore.append`

Longer walkthrough: [how-it-works.md](how-it-works.md).
