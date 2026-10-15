"""Cached body formatting, token colors and cell-aware display wrapping.

This is a presentation layer. It never modifies Message.body or parses XML
entities, external resources, or broker data beyond the text already browsed.
"""
from dataclasses import dataclass
from functools import lru_cache
import json
import re

from wcwidth import wcwidth

from .terminal_text import safe_text

JSON_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\b(?:true|false|null)\b|-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?')
JSON_KEY_END = re.compile(r'\s*:')
XML_TOKEN = re.compile(r'<!--[\s\S]*?(?:-->|$)|<!\[CDATA\[[\s\S]*?(?:\]\]>|$)|<\?[\s\S]*?(?:\?>|$)|</?[A-Za-z_:](?:"[^"]*"|\x27[^\x27]*\x27|[^\x27">])*>|<![^>]*>')
XML_VALUE = re.compile(r'"[^"]*"|\x27[^\x27]*\x27')
XML_START = re.compile(r'<(?:\?|!|/?[A-Za-z_:])')


@dataclass(frozen=True)
class BodyRow:
    number: int
    continuation: bool
    text: str
    spans: tuple = ()  # (start character, end character, palette role)


@dataclass(frozen=True)
class BodyLayout:
    format: str
    rows: tuple[BodyRow, ...]
    line_count: int


@lru_cache(maxsize=2)
def document(body, message_type, truncated):
    source, kind = body, "Text"
    if message_type == "TextMessage":
        if not truncated:
            try:
                source = json.dumps(json.loads(body), indent=2, ensure_ascii=False)
                kind = "JSON"
            except (ValueError, TypeError, RecursionError):
                pass
        if kind == "Text" and XML_START.match(body.lstrip()):
            kind = "XML"
    lines = tuple(safe_text(line) for line in source.splitlines()) or ("",)
    source = "\n".join(lines)
    tokens = []
    if kind == "JSON":
        for match in JSON_TOKEN.finditer(source):
            role = "accent" if JSON_KEY_END.match(source, match.end()) else "muted" if match.group() == "null" else "green"
            tokens.append((match.start(), match.end(), role))
    elif kind == "XML":
        for match in XML_TOKEN.finditer(source):
            text = match.group()
            if text.startswith(("<!--", "<?", "<!")):
                tokens.append((match.start(), match.end(), "muted"))
                continue
            cursor = match.start()
            for value in XML_VALUE.finditer(text):
                start, end = match.start() + value.start(), match.start() + value.end()
                if cursor < start:
                    tokens.append((cursor, start, "accent"))
                tokens.append((start, end, "green"))
                cursor = end
            if cursor < match.end():
                tokens.append((cursor, match.end(), "accent"))
    # Project global tokens onto lines, including multiline XML comments.
    decorated, offset, token_index = [], 0, 0
    for line in lines:
        end, spans = offset + len(line), []
        while token_index < len(tokens) and tokens[token_index][1] <= offset:
            token_index += 1
        index = token_index
        while index < len(tokens) and tokens[index][0] < end:
            start, stop, role = tokens[index]
            spans.append((max(start, offset) - offset, min(stop, end) - offset, role))
            index += 1
        decorated.append((line, tuple(spans)))
        offset = end + 1
    return kind, tuple(decorated)


def segments(text, width):
    """Preserve every character; prefer a nearby whitespace boundary."""
    if not text:
        yield 0, 0
    start = 0
    while start < len(text):
        end, used, boundary = start, 0, start
        while end < len(text):
            size = max(0, wcwidth(text[end]))
            if used + size > width:
                break
            used += size
            end += 1
            if text[end - 1].isspace() and used >= width // 2:
                boundary = end
        if end < len(text) and boundary > start:
            end = boundary
        end = max(start + 1, end)
        # Combining marks stay with the preceding character at a wrap boundary.
        while end < len(text) and wcwidth(text[end]) == 0:
            end += 1
        yield start, end
        start = end


@lru_cache(maxsize=4)
def body_layout(body, message_type, truncated, width, wrap):
    kind, lines = document(body, message_type, truncated)
    rows = []
    for number, (line, spans) in enumerate(lines, 1):
        parts = segments(line, max(2, width)) if wrap else ((0, len(line)),)
        span_index = 0
        for start, end in parts:
            while span_index < len(spans) and spans[span_index][1] <= start:
                span_index += 1
            index, colors = span_index, []
            while index < len(spans) and spans[index][0] < end:
                a, b, role = spans[index]
                colors.append((max(a, start) - start, min(b, end) - start, role))
                index += 1
            rows.append(BodyRow(number, start > 0, line[start:end], tuple(colors)))
    return BodyLayout(kind, tuple(rows), len(lines))
