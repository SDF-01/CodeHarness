from codeharness.config import HarnessConfig
from codeharness.languages import text_problem
from codeharness.loop import run_turn
from codeharness.model import Completion, ToolCall
from codeharness.review import source_problem
from codeharness.run import compile_and_launch, probe_java
from codeharness.session import SessionStore, database_path
from codeharness.tools import write_file
from tests.fakes import ScriptedModel

_JAVA = "public class Hello {\n    public static void main(String[] args) {\n        System.out.println(1);\n    }\n}\n"


def test_java_html_css_and_script_problems(tmp_path) -> None:
    java = tmp_path / "Hello.java"
    java.write_text("public class Other {\n}\n", encoding="utf-8")
    assert "must declare class Hello" in (text_problem(java, java.read_text(encoding="utf-8")) or "")

    page = tmp_path / "index.html"
    page.write_text("<html><body>Hi", encoding="utf-8")
    assert "missing </html>" in (text_problem(page, page.read_text(encoding="utf-8")) or "")

    style = tmp_path / "app.css"
    style.write_text("body { color: black;\n", encoding="utf-8")
    assert "unbalanced braces" in (text_problem(style, style.read_text(encoding="utf-8")) or "")

    script = tmp_path / "app.jsx"
    script.write_text("const ok = () => {\n  return 1\n", encoding="utf-8")
    assert "unbalanced brackets" in (text_problem(script, script.read_text(encoding="utf-8")) or "")

    script.write_text("const ok = () => 1\n", encoding="utf-8")
    assert source_problem(script) is None


def test_write_file_repairs_escaped_java_newlines(tmp_path) -> None:
    dumped = _JAVA.replace("\n", "\\n")
    result = write_file.run(
        {"path": "Hello.java", "content": dumped},
        tmp_path,
        HarnessConfig(project_root=tmp_path, model="test"),
    )
    saved = (tmp_path / "Hello.java").read_text(encoding="utf-8")
    assert result.startswith("wrote Hello.java")
    assert "\\n" not in saved
    assert "class Hello" in saved
    assert source_problem(tmp_path / "Hello.java") is None


def test_bad_java_is_sent_back(tmp_path) -> None:
    model = ScriptedModel(
        [
            Completion(
                content="",
                tool_calls=[
                    ToolCall(
                        id="w1",
                        name="write_file",
                        arguments={"path": "Hello.java", "content": "public class Other {\n}\n"},
                    )
                ],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="done", tool_calls=[], prompt_tokens=1, completion_tokens=1),
            Completion(
                content="",
                tool_calls=[
                    ToolCall(id="w2", name="write_file", arguments={"path": "Hello.java", "content": _JAVA})
                ],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            Completion(content="fixed java", tool_calls=[], prompt_tokens=1, completion_tokens=1),
        ]
    )
    store = SessionStore(database_path(tmp_path))
    session = store.create(tmp_path)
    result = run_turn(
        store=store,
        session=session,
        user_text="build a java program",
        model=model,
        config=HarnessConfig(
            project_root=tmp_path,
            model="test",
            permissions={**HarnessConfig().permissions, "write_file": "allow"},
        ),
        ask=lambda name, detail: False,
    )
    store.close()
    assert result.text == "fixed java"
    assert any(message.content.startswith("Review failed.") for message in session.messages)


def test_missing_javac_does_not_fail_a_valid_program(tmp_path, monkeypatch) -> None:
    path = tmp_path / "Hello.java"
    path.write_text(_JAVA, encoding="utf-8")
    monkeypatch.setattr("codeharness.run.shutil.which", lambda name: None)
    assert probe_java(path, tmp_path) == ""


def test_launch_opens_html(tmp_path, monkeypatch) -> None:
    page = tmp_path / "index.html"
    page.write_text("<html><body>Hi</body></html>\n", encoding="utf-8")
    opened: list[str] = []
    monkeypatch.setattr("codeharness.run.webbrowser.open", lambda url: opened.append(url) or True)
    code, report = compile_and_launch(HarnessConfig(project_root=tmp_path, model="test"))
    assert code == 0
    assert "Opened index.html" in report
    assert opened
    assert opened[0].startswith("file:")
