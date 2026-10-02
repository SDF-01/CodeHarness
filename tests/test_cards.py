from codeharness.cards import tool_card


def test_write_card_names_the_file_only() -> None:
    content = "\n".join(f"line {index}" for index in range(20))
    card = tool_card("write_file", {"path": "clock.py", "content": content}, "wrote clock.py (20 lines)")
    assert card == "Created clock.py"
    assert "line 0" not in card


def test_edit_card_shows_removed_and_added_lines() -> None:
    card = tool_card(
        "edit_file",
        {"path": "note.txt", "old_string": "old\n", "new_string": "new\n"},
        "edited note.txt",
    )
    assert "Edited note.txt" in card
    assert "- old" in card
    assert "+ new" in card
