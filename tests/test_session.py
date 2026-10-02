from codeharness.session import SessionStore, StoredMessage, database_path


def test_session_roundtrip(tmp_path) -> None:
    store = SessionStore(database_path(tmp_path))
    created = store.create(tmp_path)
    store.append(created, StoredMessage(role="user", content="fix the bug"))
    store.append(
        created,
        StoredMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "c1", "name": "read_file", "arguments": {"path": "a.py"}}],
        ),
    )
    store.set_title(created, "fix the bug")
    store.close()

    reloaded = SessionStore(database_path(tmp_path))
    session = reloaded.get(created.id)
    reloaded.close()
    assert session.title == "fix the bug"
    assert session.messages[0].content == "fix the bug"
    assert session.messages[1].tool_calls[0]["name"] == "read_file"


def test_lessons_and_agent_survive_reload(tmp_path) -> None:
    store = SessionStore(database_path(tmp_path))
    created = store.create(tmp_path)
    store.set_agent(created, "plan")
    store.add_lesson(created, "Do not pip install tkinter.")
    store.close()

    reloaded = SessionStore(database_path(tmp_path))
    session = reloaded.get(created.id)
    assert reloaded.get_agent(session) == "plan"
    assert reloaded.lessons(session) == ["Do not pip install tkinter."]
    reloaded.close()