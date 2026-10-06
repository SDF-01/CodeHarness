from codeharness.config import HarnessConfig
from codeharness.context import SYSTEM_PROMPT, build_context
from codeharness.agents import AGENTS
from codeharness.playbook import SKILLS, coaching_for, select_skills
from codeharness.web_prompt import apply_web_choice, offer_web_gui
from codeharness.session import StoredMessage


def test_a_build_request_loads_vibe_and_rules() -> None:
    text = coaching_for("build a digital / analog clock")
    assert "vibe-build" in text
    assert "Rules:" in text
    assert "skill tool" in text


def test_skill_and_agent_files_keep_their_schema() -> None:
    assert [skill.name for skill in SKILLS] == [
        "vibe-build",
        "gui-app",
        "web-app",
        "fullstack",
        "java",
        "c",
        "cpp",
        "csharp",
        "go",
        "rust",
        "ruby",
        "php",
        "kotlin",
        "swift",
        "sql",
        "structure",
        "engineering",
        "interface",
        "verify",
        "stdlib",
        "opencode",
    ]
    assert all(skill.description and skill.body for skill in SKILLS)
    assert AGENTS["build"].mode == "primary"
    assert AGENTS["plan"].mode == "primary"


def test_skills_are_ranked_and_capped_at_four() -> None:
    clock = [skill.name for skill in select_skills("build a clock")]
    assert clock == ["vibe-build", "gui-app"]
    many = [
        skill.name
        for skill in select_skills("build a clock window and pip install tkinter then edit the bug")
    ]
    assert many == ["stdlib", "vibe-build", "gui-app", "verify"]


def test_a_screen_loads_the_interface_skill() -> None:
    names = [skill.name for skill in select_skills("design a button with a label")]
    assert "interface" in names


def test_short_language_names_match_whole_words() -> None:
    assert "go" in [skill.name for skill in select_skills("write a go program")]
    assert "go" not in [skill.name for skill in select_skills("build a clock")]
    sharp = [skill.name for skill in select_skills("write a c# program")]
    assert "csharp" in sharp
    assert "c" not in sharp


def test_web_and_java_skills_match_their_stacks() -> None:
    web = [skill.name for skill in select_skills("build a page with react tailwind and shadcn")]
    assert web[0] == "web-app"
    java = [skill.name for skill in select_skills("write a java atm")]
    assert "java" in java
    assert offer_web_gui("build an atm")
    assert not offer_web_gui("build a react dashboard")
    assert not offer_web_gui("fix the bug")
    assert "index.html" in apply_web_choice("build an atm", True)
    assert "Do not create a website." in apply_web_choice("build an atm", False)


def test_a_branch_loads_one_skill() -> None:
    page = coaching_for("build a full stack notes app", "page").splitlines()[0]
    api = coaching_for("build a full stack notes app", "api").splitlines()[0]
    assert page == "Active skills: web-app"
    assert api == "Active skills: fullstack"
    assert coaching_for("build a clock").splitlines()[0].startswith("Active skills: vibe-build")


def test_a_greeting_loads_no_skill() -> None:
    assert coaching_for("hello") == ""
    assert select_skills("hello") == []


def test_context_keeps_the_base_prompt_for_a_greeting(tmp_path) -> None:
    config = HarnessConfig(project_root=tmp_path, model="test")
    built = build_context([StoredMessage(role="user", content="hello")], config)
    prompt = built.messages[0]["content"]
    assert prompt.startswith(SYSTEM_PROMPT)
    assert "standard library" in prompt
    assert "Active skills:" not in prompt


def test_context_adds_the_skill_for_a_build(tmp_path) -> None:
    config = HarnessConfig(project_root=tmp_path, model="test")
    built = build_context([StoredMessage(role="user", content="build a clock")], config)
    prompt = built.messages[0]["content"]
    assert "Active skills: vibe-build" in prompt
    assert SYSTEM_PROMPT in prompt


def test_pip_task_loads_the_stdlib_skill() -> None:
    text = coaching_for("pip install tkinter")
    assert "stdlib" in text
    assert "tkinter" in text


def test_a_python_write_adds_the_glob_rule(tmp_path) -> None:
    config = HarnessConfig(project_root=tmp_path, model="test")
    stored = [
        StoredMessage(role="user", content="hello"),
        StoredMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "w1", "name": "write_file", "arguments": {"path": "app.py"}}],
        ),
    ]
    prompt = build_context(stored, config).messages[0]["content"]
    assert "must parse" in prompt


def test_plan_agent_is_told_not_to_edit(tmp_path) -> None:
    config = HarnessConfig(project_root=tmp_path, model="test")
    prompt = build_context(
        [StoredMessage(role="user", content="hello")],
        config,
        agent="plan",
    ).messages[0]["content"]
    assert "You may write only PLAN.md." in prompt
