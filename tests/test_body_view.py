import json

import pytest
from wcwidth import wcswidth

from jmsbrowser.body_view import body_layout
from jmsbrowser.terminal_text import safe_text


def test_json_colors_cross_wrap_boundaries_without_changing_body():
    original = json.dumps({"note": "Long message " * 15, "enabled": True, "number": -12.5e20, "missing": None})
    layout = body_layout(original, "TextMessage", False, 25, True)
    assert layout.format == "JSON"
    assert any(row.continuation for row in layout.rows)
    expected = json.dumps(json.loads(original), indent=2, ensure_ascii=False).splitlines()
    for number, line in enumerate(expected, 1):
        parts = [row for row in layout.rows if row.number == number]
        assert "".join(row.text for row in parts) == line
    assert all(wcswidth(row.text) <= 25 for row in layout.rows)
    assert any(row.continuation and any(role == "green" for _, _, role in row.spans) for row in layout.rows)
    assert json.loads(original)["note"] == "Long message " * 15


def test_xml_colors_attributes_comments_and_does_not_expand_entities():
    original = '<!DOCTYPE event [<!ENTITY remote SYSTEM "file:///should-not-be-read">]>\n<event id="1042">\n<!-- first\n second -->\n&remote;\n</event>'
    layout = body_layout(original, "TextMessage", False, 120, False)
    assert layout.format == "XML"
    assert "&remote;" in "\n".join(row.text for row in layout.rows)
    row = next(row for row in layout.rows if '<event id=' in row.text)
    assert any(row.text[a:b] == '"1042"' and role == "green" for a, b, role in row.spans)
    assert all(role == "muted" for row in layout.rows if "first" in row.text or "second" in row.text for _, _, role in row.spans)
    assert any(row.text[a:b] == '</event>' and role == "accent" for row in layout.rows for a, b, role in row.spans)


@pytest.mark.parametrize("body", ["", "plain text", '{"broken":', "hello\x1b[2J\t界e\u0301\u202e", "🙂界e\u0301" * 100])
def test_plain_and_invalid_text_wrap_safely_without_losing_characters(body):
    layout = body_layout(body, "TextMessage", False, 13, True)
    assert layout.format == "Text"
    assert "".join(row.text for row in layout.rows) == safe_text(body)
    assert all(wcswidth(row.text) <= 13 for row in layout.rows)
    assert all(not row.spans for row in layout.rows)
    assert all(not row.text.startswith("\u0301") for row in layout.rows)


def test_truncated_and_non_text_messages_are_not_reformatted_as_json():
    original = '{"long": "value"}'
    for kind, truncated in (("BytesMessage", False), ("TextMessage", True)):
        layout = body_layout(original, kind, truncated, 60, False)
        assert layout.format == "Text"
        assert len(layout.rows) == 1 and layout.rows[0].text == original


def test_many_xml_tokens_wrap_with_correct_continuation_colors():
    original = '<event>' + '<item n="1">value</item>' * 1000 + '</event>'
    layout = body_layout(original, "TextMessage", False, 54, True)
    assert layout.format == "XML"
    assert "".join(row.text for row in layout.rows) == original
    assert all(wcswidth(row.text) <= 54 for row in layout.rows)
    assert all(0 <= a < b <= len(row.text) for row in layout.rows for a, b, _ in row.spans)
