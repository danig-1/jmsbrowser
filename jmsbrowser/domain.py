from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json

from wcwidth import wcswidth


@dataclass(frozen=True)
class QueueInfo:
    name: str
    depth: int
    routing: str = ""


@dataclass(frozen=True)
class Consumer:
    consumer_id: str = ""
    application: str = ""
    remote_address: str = ""
    user: str = ""
    protocol: str = ""
    selector: str | None = None
    connection_id: str = ""
    session_id: str = ""
    status: str = ""
    details: dict = field(default_factory=dict)

    @property
    def identity(self):
        return (self.consumer_id, self.connection_id, self.session_id, self.application, self.remote_address,
                self.details.get("ProcessId"), self.details.get("ThreadId"))

    def rows(self):
        values = {"ConsumerId": self.consumer_id or "n/a", "Application / Client": self.application or "n/a",
                  "Remote endpoint": self.remote_address or "n/a", "User": self.user or "n/a",
                  "Protocol": self.protocol or "n/a", "Selector": "n/a" if self.selector is None else self.selector or "none",
                  "ConnectionId": self.connection_id or "n/a", "SessionId": self.session_id or "n/a",
                  "Status": self.status or "n/a", **self.details}
        return metadata_rows(values)


@dataclass(frozen=True)
class ConsumerSnapshot:
    queue: str
    consumers: list[Consumer] = field(default_factory=list)
    unit: str = "consumers"
    note: str = ""

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**{**data, "consumers": [Consumer(**item) for item in data["consumers"]]})


def metadata_rows(values, timestamp=False):
    rows = []
    for key, value in values.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if timestamp and key == "JMSTimestamp" and type(value) is int:
            if value == 0:
                text += "  (not set)"
            else:
                try:
                    date = datetime.fromtimestamp(value / 1000, timezone.utc).astimezone()
                    text += "  (" + date.isoformat(sep=" ", timespec="milliseconds") + ")"
                except (OverflowError, OSError, ValueError):
                    text += "  (datetime unavailable)"
        for index, line in enumerate(text.split("\n")):
            rows.append((str(key) if index == 0 else "", line))
    return rows


@dataclass(frozen=True)
class Message:
    message_id: str
    type: str
    priority: int
    body: str
    size: int | None
    headers: dict = field(default_factory=dict)
    properties: dict = field(default_factory=dict)
    truncated: bool = False
    size_note: str = "Payload bytes; text measured as UTF-8. Excludes headers."

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**data)

    def detail(self, tab: int = 0) -> str:
        if tab in (1, 2):
            rows = self.metadata(tab)
            width = max((wcswidth(key) for key, _ in rows), default=0)
            return "\n".join(" " * max(0, width - wcswidth(key)) + key + " │ " + value for key, value in rows)
        if not self.truncated and self.type == "TextMessage":
            try:
                return json.dumps(json.loads(self.body), indent=2, ensure_ascii=False)
            except (ValueError, TypeError):
                pass
        return self.body

    def metadata(self, tab):
        return metadata_rows(self.headers if tab == 1 else self.properties, timestamp=tab == 1)

    def copy_text(self, tab):
        """Copy source text or the complete metadata table, independent of layout."""
        return self.body if tab == 0 else self.detail(tab)

    def copy_requires_fetch(self, tab):
        if tab == 0:
            return self.truncated
        values = self.headers if tab == 1 else self.properties
        return any(isinstance(value, dict) and value.get("type") == "bytes" and value.get("truncated")
                   for value in values.values())


def format_size(size: int | None) -> str:
    return "n/a" if size is None else f"{size:,} B"


def format_message_datetime(timestamp):
    if type(timestamp) is not int or timestamp == 0:
        return "n/a"
    try:
        return datetime.fromtimestamp(timestamp / 1000, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "n/a"
