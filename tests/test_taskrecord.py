from codeharness.lead import _review_failed
from codeharness.taskrecord import write_calculator_logic
from codeharness.taskrecord import (
    TaskTransitionError,
    begin_task,
    close_known_faults,
    fault_brief,
    inspect_program,
    judge,
    load_task,
    move,
    probe_logic,
    settle_task,
    shape_problem,
    troubleshoot,
)


def test_an_empty_review_is_not_a_pass() -> None:
    assert _review_failed("") is True
    assert _review_failed("looks fine") is True
    assert _review_failed("No problems. The button is still missing.") is True
    assert _review_failed("Stopped after 12 steps. The task is not finished.") is True
    assert _review_failed("No problems.") is False


def test_a_task_survives_a_restart(tmp_path) -> None:
    begin_task(tmp_path, "build a task list")
    first = load_task(tmp_path)
    assert first is not None
    assert first["request"] == "build a task list"
    assert first["requirements"][0]["state"] == "implementing"
    again = load_task(tmp_path)
    assert again is not None
    assert again["requirements"][0]["id"] == "r1"
    assert again["request"] == first["request"]


def test_missing_evidence_cannot_verify(tmp_path) -> None:
    begin_task(tmp_path, "build a task list")
    record = settle_task(tmp_path, "done", [])
    assert record is not None
    assert record["requirements"][0]["state"] == "unverified"
    failed = settle_task(tmp_path, "Stopped after 12 steps. The task is not finished.", ["passed: ran app.py"])
    assert failed is not None
    assert failed["requirements"][0]["state"] == "failed"


def test_a_passing_run_can_verify(tmp_path) -> None:
    begin_task(tmp_path, "build a task list")
    record = settle_task(tmp_path, "built", ["passed: ran tasks.py"])
    assert record is not None
    assert record["requirements"][0]["state"] == "verified"


def test_named_behavior_is_part_of_the_requirement(tmp_path) -> None:
    record = begin_task(tmp_path, "build a task list that saves, lists, adds, and rejects empty input")
    acceptance = record["requirements"][0]["acceptance"]
    procedure = record["requirements"][0]["procedure"]
    assert "saves its data" in acceptance
    assert "lists its data" in acceptance
    assert "adds an item" in acceptance
    assert "rejects empty input" in acceptance
    assert "saves its data" in procedure


def test_a_launch_alone_cannot_verify() -> None:
    assert judge("Launch passed.", ["passed: launched"]) == "unverified"
    assert judge("built", ["passed: ran logic.py"]) == "unverified"
    assert judge("built", ["passed: ran app.py", "passed: launched"]) == "verified"
    assert judge("Launch failed.", ["passed: ran app.py", "failed: launch"]) == "failed"


def test_states_follow_the_allowed_path(tmp_path) -> None:
    record = begin_task(tmp_path, "build a task list")
    move(record, "r1", "verifying")
    try:
        move(record, "r1", "pending")
    except TaskTransitionError:
        pass
    else:
        raise AssertionError("verifying must not return to pending")
    assert judge("Health passed.", ["passed: GET /api/health"]) == "verified"
    assert judge("built", ["unverified: gofmt is not installed."]) == "unverified"
    assert judge("", ["passed: ran app.py"]) == "unverified"


def test_named_behavior_calls_logic(tmp_path) -> None:
    (tmp_path / "logic.py").write_text(
        "def run_check(name):\n    return 'ok' if name == 'empty' else 'no'\n",
        encoding="utf-8",
    )
    assert probe_logic(tmp_path, "build an atm that rejects empty input") == ["passed: empty"]
    assert probe_logic(tmp_path, "build a clock") == []
    assert probe_logic(tmp_path, "build an atm that rejects invalid input") == ["failed: invalid"]


def test_a_branch_in_the_window_is_failed_logic(tmp_path) -> None:
    (tmp_path / "logic.py").write_text("def apply(current, label):\n    return current\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "import logic\nimport tkinter\n\n"
        "def main():\n"
        "    root = tkinter.Tk()\n"
        "    root.configure(bg='#1e1e1e')\n"
        "    shown = tkinter.StringVar(value='0')\n"
        "    tkinter.Label(root, textvariable=shown).pack()\n"
        "    for label in ('1', '2', '3', '4'):\n"
        "        tkinter.Button(root, text=label, command=logic.apply).pack()\n"
        "    if shown.get() == '5+5':\n"
        "        shown.set('Unknown path')\n"
        "    root.mainloop()\n",
        encoding="utf-8",
    )
    assert "deciding the product" in shape_problem(tmp_path, "build a printer")


def test_a_command_box_is_not_the_product(tmp_path) -> None:
    (tmp_path / "logic.py").write_text("def submit(text):\n    return 'Unknown command'\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "import logic\nimport tkinter\n\n"
        "def main():\n"
        "    root = tkinter.Tk()\n"
        "    tkinter.Label(root, text='Enter action:').pack()\n"
        "    tkinter.Entry(root).pack()\n"
        "    tkinter.Button(root, text='Check', command=logic.submit).pack()\n"
        "    root.mainloop()\n",
        encoding="utf-8",
    )
    assert shape_problem(tmp_path, "build a calculator").startswith("failed: logic")


def test_a_calculator_must_compute(tmp_path) -> None:
    digits = " ".join(f"'{digit}'" for digit in "0123456789")
    (tmp_path / "logic.py").write_text("def calculate(expression):\n    return 'ok'\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "import logic\nimport tkinter\n\n"
        "def main():\n"
        "    root = tkinter.Tk()\n"
        "    root.configure(bg='#1e1e1e')\n"
        f"    for label in ({digits}, '='):\n"
        "        tkinter.Button(root, text=label, command=logic.calculate).pack()\n"
        "    root.mainloop()\n",
        encoding="utf-8",
    )
    assert "return 2" in shape_problem(tmp_path, "build a calculator")
    (tmp_path / "logic.py").write_text(
        "def calculate(expression):\n"
        "    if expression == '1+1':\n"
        "        return 2\n"
        "    if expression == '2*3':\n"
        "        return 6\n"
        "    return 'bad expression'\n",
        encoding="utf-8",
    )
    assert shape_problem(tmp_path, "build a calculator") == ""


def test_a_constant_check_is_failed_logic(tmp_path) -> None:
    (tmp_path / "logic.py").write_text("def run_check(name):\n    return 'ok'\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "import logic\nimport tkinter\n\ndef main():\n    root = tkinter.Tk()\n    root.mainloop()\n",
        encoding="utf-8",
    )
    assert shape_problem(tmp_path).startswith("failed: logic")


def _logic() -> str:
    return "def apply(current, name):\n    return current\n"


def test_a_blank_shell_fails_inspection(tmp_path) -> None:
    (tmp_path / "logic.py").write_text(_logic(), encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "import logic\nimport tkinter\n\n"
        "def main():\n"
        "    logic.apply('0', '1')\n"
        "    root = tkinter.Tk()\n"
        "    root.title('My App')\n"
        "    root.mainloop()\n",
        encoding="utf-8",
    )
    assert inspect_program(tmp_path, "build a printer").startswith("failed: logic")


def test_unplaced_buttons_fail_inspection(tmp_path) -> None:
    (tmp_path / "logic.py").write_text(_logic(), encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "import logic\nimport tkinter\n\n"
        "def main():\n"
        "    root = tkinter.Tk()\n"
        "    root.title('Printer')\n"
        "    root.configure(bg='#1e1e1e')\n"
        "    tkinter.Label(root, text='0').pack()\n"
        "    tkinter.Button(root, text='1')\n"
        "    tkinter.Button(root, text='2')\n"
        "    tkinter.Button(root, text='3')\n"
        "    tkinter.Button(root, text='4')\n"
        "    root.mainloop()\n",
        encoding="utf-8",
    )
    problem = inspect_program(tmp_path, "build a printer")
    assert "pack, grid, or place" in problem


def test_a_placed_window_passes_inspection(tmp_path) -> None:
    (tmp_path / "logic.py").write_text(_logic(), encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "import logic\nimport tkinter\n\n"
        "def main():\n"
        "    root = tkinter.Tk()\n"
        "    root.title('Printer')\n"
        "    root.configure(bg='#1e1e1e')\n"
        "    tkinter.Label(root, text='0').grid(row=0, column=0)\n"
        "    for label in ('Save', 'Open', 'Clear', 'Add'):\n"
        "        tkinter.Button(root, text=label, command=logic.apply).grid()\n"
        "    root.mainloop()\n",
        encoding="utf-8",
    )
    assert inspect_program(tmp_path, "build a printer") == ""


def test_a_hello_world_calculator_gets_real_math(tmp_path) -> None:
    (tmp_path / "logic.py").write_text("def main():\n    print('Hello, World!')\n", encoding="utf-8")
    digits = ", ".join(f"'{digit}'" for digit in "0123456789")
    (tmp_path / "app.py").write_text(
        "import logic\nimport tkinter\n\n"
        "def main():\n"
        "    root = tkinter.Tk()\n"
        "    root.title('Calculator')\n"
        "    root.configure(bg='#1e1e1e')\n"
        "    tkinter.Label(root, text='0').grid(row=0, column=0)\n"
        f"    for label in ({digits}, '.', 'AC', 'Backspace', '/', 'x', '-', '+', '='):\n"
        "        tkinter.Button(root, text=label, command=logic.apply).grid()\n"
        "    root.mainloop()\n",
        encoding="utf-8",
    )
    assert write_calculator_logic(tmp_path, "build a calculator") is True
    assert inspect_program(tmp_path, "build a calculator") == ""
    assert write_calculator_logic(tmp_path, "build a clock") is False


def test_a_calculator_without_keys_or_a_loop_is_repaired(tmp_path) -> None:
    (tmp_path / "logic.py").write_text("def calculate(expression):\n    return eval(expression)\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "import logic\nimport tkinter\n\n"
        "def main():\n"
        "    root = tkinter.Tk()\n"
        "    root.title('Calculator')\n"
        "    root.configure(bg='#1e1e1e')\n"
        "    tkinter.Label(root, text='0').grid()\n"
        "    for label in ('0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '='):\n"
        "        tkinter.Button(root, text=label).grid()\n"
        "    root.update()\n",
        encoding="utf-8",
    )
    assert write_calculator_logic(tmp_path, "build a calculator") is True
    window = (tmp_path / "app.py").read_text(encoding="utf-8")
    assert "mainloop" in window
    assert "main()" in window
    assert "def apply" in (tmp_path / "logic.py").read_text(encoding="utf-8")
    assert inspect_program(tmp_path, "build a calculator") == ""


def test_a_broken_calculator_records_what_and_why(tmp_path) -> None:
    (tmp_path / "logic.py").write_text(
        "def calculate(expression):\n    return eval(expression)\n",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        "import logic\nimport tkinter\n\n"
        "def main():\n"
        "    root = tkinter.Tk()\n"
        "    root.title('Calculator')\n"
        "    root.configure(bg='#1e1e1e')\n"
        "    shown = tkinter.StringVar(value='0')\n"
        "    tkinter.Label(root, textvariable=shown).grid(row=0, column=0, columnspan=4)\n"
        "    labels = ('0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '.', 'AC', 'Backspace', '/', 'x', '-', '+', '=')\n"
        "    for index, label in enumerate(labels):\n"
        "        tkinter.Button(root, text=label, command=lambda name=label: shown.set(str(logic.apply(shown.get(), name)))).grid()\n"
        "    root.update()\n",
        encoding="utf-8",
    )
    faults = troubleshoot(tmp_path, "build a calculator")
    brief = fault_brief(faults)
    assert any("logic.apply" in fault["cause"] for fault in faults)
    assert any("mainloop" in fault["cause"] for fault in faults)
    assert "What is wrong:" in brief
    assert "Why:" in brief
    begin_task(tmp_path, "build a calculator")
    assert close_known_faults(tmp_path, "build a calculator") is True
    assert troubleshoot(tmp_path, "build a calculator") == []
