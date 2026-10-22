"""Literal, case-insensitive search of the same text shown in message detail."""
from dataclasses import dataclass
import re

from .body_view import document
from .composer import TextBuffer
from .terminal_text import safe_text


@dataclass(frozen=True)
class Match:
    tab: int
    line: int
    field: str
    start: int
    end: int


class MessageSearch:
    def __init__(self, owner, message):
        self.owner = owner
        self.message = message
        self.buffer = TextBuffer()
        self.query = ""
        self.editing = True
        self.pending = False
        self.error = ""
        self.matches = []
        self.by_line = {}
        self.current = -1

    def find(self, query):
        self.query = query
        self.matches, self.by_line, self.current = [], {}, -1
        if not query:
            return
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        sources = []
        for tab in (1, 2):
            for row, (key, value) in enumerate(self.message.metadata(tab)):
                sources.extend(((tab, row, "key", safe_text(key)), (tab, row, "value", safe_text(value))))
        _, lines = document(self.message.body, self.message.type, self.message.truncated)
        sources.extend((0, row, "body", line) for row, (line, _) in enumerate(lines))
        for tab, row, field, value in sources:
            for found in pattern.finditer(value):
                match = Match(tab, row, field, found.start(), found.end())
                self.by_line.setdefault((tab, row, field), []).append(len(self.matches))
                self.matches.append(match)

    def step(self, delta=1, tab=None):
        if not self.matches:
            return None
        if self.current < 0 and tab is not None:
            self.current = next((i for i, match in enumerate(self.matches) if match.tab == tab), 0)
        else:
            self.current = (self.current + delta) % len(self.matches)
        return self.matches[self.current]

    def spans(self, tab, row, field):
        return [(self.matches[i].start, self.matches[i].end, i == self.current)
                for i in self.by_line.get((tab, row, field), ())]

    def status(self):
        if self.pending:
            return "Reading full message for search…"
        if self.error:
            return "Search failed: " + self.error
        if self.editing:
            return "Find: " + self.buffer.text[:self.buffer.cursor] + "▏" + self.buffer.text[self.buffer.cursor:]
        count = len(self.matches)
        return f"Find: {self.query}  ·  " + (f"{self.current + 1} / {count} matches" if count else "No matches")
