# How this harness works

A model call is one step. A harness is the loop around those steps. OpenCode, Aider, and similar coding agents all do a version of this. CodeHarness is a small copy of the core, written so you can read it in one sitting.

The session store keeps every message. The context builder decides what the model actually sees. Those are different on purpose: the history can be complete while the prompt stays small.

## 1. Model client

`OpenAICompatibleClient.complete` in [src/codeharness/model.py](../src/codeharness/model.py) sends `POST /chat/completions`. The body has the messages, the tool list, `temperature` 0, and `max_tokens` set to `response_reserve`. An Ollama server also gets a context window of `context_limit` and a 30 minute keep-alive, so the model stays loaded. The response is either plain text, a list of tool calls, or both.

If the server includes `usage.prompt_tokens` and `usage.completion_tokens`, those numbers are the token meter. If it does not, the harness estimates from character length and labels the line `source=estimated`. Local models do not share one tokenizer, so the estimate is a budget tool, not a bill.

An empty `model` in config stops the CLI and tells you how to set one. `codeharness pull` asks the local Ollama server to download that model. Chat does not download on its own.

## 2. Tools

The tools live in [src/codeharness/tools](../src/codeharness/tools). Each one returns a short string. A failure is also a string, so the model can try a different action.

- `read_file` returns a line range, 200 lines by default, with line numbers. A long file tells the model how many lines exist so the next call can ask for a slice.
- `search` returns at most 20 matching lines.
- `list_files` returns at most 80 paths.
- `write_file` creates or replaces a whole file and replies with the path and line count. A new program is saved under `projects/<name>/`, not in the harness folder. A follow-up edit stays in that folder.
- `edit_file` replaces one exact `old_string`. It does not rewrite the whole file, and it does not create a new file.
- After a source file is written, the harness checks it. Python is parsed, names are checked, and a non-tkinter file runs for about 3 seconds. Java must declare a matching class, and a file with `main` is compiled and run when `javac` is installed. HTML, CSS, JavaScript, JSX, and TypeScript must be non-empty and balanced. A tkinter file must stay open. Repo pytest is not that check.
- `check_command` runs when you ask to test the harness, or when the turn changed a file that is not a new program.
- A failed shell blocks the answer until a later command works, or the step limit hits. `pip install` of a standard-library module such as tkinter is rejected before it runs.
- `launch` in the CLI and on the local page starts the newest Python, Java, or HTML file. A folder with `index.html` and `server.py` starts the API and opens it in the browser. Python is compiled first when the program is a script. Java is compiled with `javac`. A lone HTML file is opened in the browser. It does not ask the model to print the program.
- `shell` runs only after you approve it, and only when the command starts Python, Java, Node, npm, npx, or a matching compiler. Pipes, redirects, and other programs are blocked. The working directory is the project root.
- `git_status` and `git_diff` are read-only. `git_add` and `git_commit` ask first. Nothing is pushed.

Every file path is resolved and rejected when it leaves the project root. Denied tools are omitted from the prompt so the model does not spend a call discovering a tool it cannot use.

## 3. Permissions and the doom loop

`PermissionGate` in [src/codeharness/permissions.py](../src/codeharness/permissions.py) has three rules: `allow`, `deny`, and `ask`. Defaults: read, search, list, git status, and git diff are allowed. Edit, write, shell, git add, and git commit ask. In `codeharness chat`, a turn says what you asked, which file changed, and whether it worked. You answer `y` or `n` when it asks to create a file, stage a file, commit, or open a window.

A doom loop is the same tool call with the same arguments, repeated. After `doom_repeat_limit` attempts (default 3), the harness asks before running it again. If you say no, the model receives an error and can choose another action. Eval denies that extra repeat automatically so a stuck model cannot run forever. `max_steps` is the hard stop.

## 4. Skills, rules, and agents

Skills live in `src/codeharness/skills/<name>/SKILL.md`. Each file has `name` and `description`. The description decides when the skill is used. Skills are ranked by how many triggers hit, and the top four bodies are added. The set is vibe-build, gui-app, web-app, java, verify, stdlib, and opencode. Before a build that has not already chosen a stack, the harness asks whether you want a realistic web app. Yes adds HTML, CSS, React, Tailwind, and shadcn. No keeps the program local.

Rules live in `src/codeharness/rules`. `alwaysApply: true` is included every turn. A rule with `globs` is included only when a file from this turn matches. If the project has `AGENTS.md`, that text is the project rule, capped so it cannot fill the prompt.

Agents live in `src/codeharness/agents`. `build` may edit. `plan` and `route` may not, and they also cannot stage or commit. `page` and `api` may write their own file. `review` may edit and may not create a file. Type `plan` or `build` at the prompt, or use those buttons on the local page. Type `handoff` and a task to run one plan turn, then a build turn that implements that plan. A full stack task instead runs route, page, API, and review as separate short sessions in the same project folder. A failed shell, a failed review, or a failed run stores one lesson on the session. The next step sees only the latest few lessons.

The model weights do not change. The files are the training the harness can apply on a laptop.

## 5. Context builder

`build_context` in [src/codeharness/context.py](../src/codeharness/context.py) always starts with the same short system prompt. Then it adds the session.

`prompt_budget` is `context_limit` minus `response_reserve`. The reserve is room for the model's reply. When the prompt would pass the budget, the builder replaces the oldest tool results with a one-line stub and keeps the newest result. It does this in code. It does not call the model to summarize, because a summary spends tokens in order to save tokens.

For a smaller model, lower `context_limit` and `max_tool_output_chars` in `codeharness.json`. The same harness fits an SLM when the budget is tighter.

## 6. Sessions

`SessionStore` in [src/codeharness/session.py](../src/codeharness/session.py) writes each message to SQLite under `.codeharness/sessions.db` in the project root. `codeharness chat --session ID` loads that history and continues. The database is per project, because the file lives in that project.

## 7. Eval

`run_task` in [src/codeharness/eval/runner.py](../src/codeharness/eval/runner.py) copies a fixture into a temp directory, runs one turn of the same loop, then runs `check.py`. The report is pass or fail, prompt tokens, completion tokens, and tool-call count.

During eval, edits are allowed, new files and shell are denied, and doom-loop repeats are denied. A task can allow `write_file` in its `task.json`. The GUI clock task does that, then `check.py` requires `tkinter` and `mainloop` without opening a window. The check is run by the harness, not by the model.

The checked-in tests use a scripted fake model. `codeharness eval` uses whatever server you configured.

## 8. The lead, the workers, and the done gate

`lead.py` sits in front of `run_turn` for a build. `run_turn` is still the worker loop. The lead calls it several times, each time with a fresh child session.

- The repo map is a few lines: files, suffixes, and whether `server.py` or `index.html` exists. Every child prompt includes it.
- Explore can read and search. It cannot write. Review reads the todo and answers problems. General can edit, and it cannot call `task`.
- Plan may write only `PLAN.md`. The lead parses checklist lines into todos. An empty plan does not build.
- One approval covers the file writes for those todos. Every shell command still asks.
- Diagnostics parse the files that todo names. A problem goes back to a new general child for that todo only.
- After the last todo, a folder with `server.py` must answer `GET /api/health` with HTTP 200 and JSON.
- When the gate passes, the harness appends a verified note to `projects/<name>/AGENTS.md`. The next session in that folder already loads that file.
- The lead's answer lists the paths and the checks. A second message typed while a lead is running is queued. `undo` restores the snapshot taken before the scaffold and before each todo, and it does not call the model.

The CLI banner lists the real tools and skills. The status line shows the model, the token fill, and the phase: ready, thinking, running, or review.

## Where to read next

Follow one chat turn in this order:

1. `cli._chat`
2. `commands.handle_turn`
3. `loop.run_turn`
4. `context.build_context`
5. `model.OpenAICompatibleClient.complete`
6. `tools.run_tool`
7. `session.SessionStore.append`
