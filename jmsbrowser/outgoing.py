"""Portable TextMessage drafts, typed JMS properties and local draft files."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
import re
import struct

MAX_CONTENT_BYTES = 4 * 1024 * 1024
PROPERTY_TYPES = ("String", "Boolean", "Byte", "Short", "Int", "Long", "Float", "Double")
RESERVED_NAMES = {"NULL", "TRUE", "FALSE", "NOT", "AND", "OR", "BETWEEN", "LIKE", "IN", "IS", "ESCAPE"}


class SendFailure(RuntimeError):
    def __init__(self, message, outcome="not_sent"):
        super().__init__(message)
        self.outcome = outcome


@dataclass(frozen=True)
class OutgoingProperty:
    name: str
    kind: str = "String"
    value: str = ""

    def converted(self):
        if not isinstance(self.name, str) or not re.fullmatch(r"[^\W\d][\w$]*|\$[\w$]*", self.name, re.UNICODE):
            raise ValueError("Property names must start with a letter, _ or $, followed by letters, digits, _ or $.")
        if self.name.upper() in RESERVED_NAMES or self.name.startswith("JMS"):
            raise ValueError(f"{self.name}: reserved JMS property name; use the dedicated header fields.")
        if self.kind not in PROPERTY_TYPES or not isinstance(self.value, str):
            raise ValueError(f"{self.name}: invalid property type or value.")
        text = self.value.strip()
        try:
            if self.kind == "String":
                return self.value
            if self.kind == "Boolean":
                if text.lower() not in ("true", "false"):
                    raise ValueError()
                return text.lower() == "true"
            if self.kind in ("Byte", "Short", "Int", "Long"):
                if not re.fullmatch(r"[+-]?\d+", text):
                    raise ValueError()
                value = int(text)
                bits = {"Byte": 8, "Short": 16, "Int": 32, "Long": 64}[self.kind]
                if not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
                    raise ValueError()
                return value
            value = float(text)
            if not math.isfinite(value):
                raise ValueError()
            if self.kind == "Float":
                value = struct.unpack("!f", struct.pack("!f", value))[0]
            return value
        except (ValueError, OverflowError, struct.error):
            raise ValueError(f"{self.name}: enter a valid {self.kind} value within its supported range.") from None


@dataclass(frozen=True)
class OutgoingMessage:
    body: str = ""
    priority: int = 4
    persistent: bool = True
    ttl_ms: int = 0
    jms_type: str = ""
    correlation_id: str = ""
    properties: list[OutgoingProperty] = field(default_factory=list)

    def validate(self):
        if type(self.priority) is not int or not 0 <= self.priority <= 9:
            raise ValueError("Priority must be an integer from 0 through 9.")
        if type(self.persistent) is not bool:
            raise ValueError("Persistence must be true or false.")
        if type(self.ttl_ms) is not int or not 0 <= self.ttl_ms <= 2**63 - 1 - 10**13:
            raise ValueError("TTL must be a non-negative millisecond value within the JMS timestamp range.")
        if any(not isinstance(value, str) for value in (self.body, self.jms_type, self.correlation_id)):
            raise ValueError("Body, JMSType and correlation ID must be text.")
        if len(self.jms_type) > 4096 or len(self.correlation_id) > 4096:
            raise ValueError("JMSType and correlation ID must be at most 4096 characters each.")
        if not isinstance(self.properties, list) or len(self.properties) > 100:
            raise ValueError("At most 100 properties are supported.")
        names, converted = set(), []
        content = self.body + self.jms_type + self.correlation_id
        for item in self.properties:
            if not isinstance(item, OutgoingProperty):
                raise ValueError("Invalid property entry.")
            value = item.converted()
            if item.name in names:
                raise ValueError(f"Duplicate property: {item.name}")
            names.add(item.name)
            converted.append((item.name, item.kind, value))
            content += item.name + item.value
        if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise ValueError("Body and metadata together must not exceed 4 MiB in UTF-8.")
        return converted

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) - cls.__dataclass_fields__.keys():
            raise ValueError("Invalid message draft fields.")
        properties = data.get("properties", [])
        if not isinstance(properties, list):
            raise ValueError("Draft properties must be a list.")
        try:
            result = cls(**{**data, "properties": [OutgoingProperty(**item) for item in properties]})
        except TypeError as error:
            raise ValueError("Invalid draft property fields.") from error
        result.validate()
        return result


def destination(config, queue_name):
    return {key: getattr(config, key) for key in ("name", "type", "host", "port", "queue_manager", "channel")} | {"queue": queue_name}


def load_body(path):
    with Path(path).expanduser().open("rb") as source:
        data = source.read(MAX_CONTENT_BYTES + 1)
    if len(data) > MAX_CONTENT_BYTES:
        raise ValueError("Body file must not exceed 4 MiB.")
    return data.decode("utf-8-sig")


def save_draft(path, target, message):
    message.validate()
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"version": 1, "target": target, "message": message.to_dict()}, ensure_ascii=False, indent=2)
    # A draft save never silently replaces an existing file.
    with path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(data + "\n")
    return path.resolve()


def load_draft(path, target):
    with Path(path).expanduser().open("rb") as source:
        raw = source.read(MAX_CONTENT_BYTES * 6 + 65537)
    if len(raw) > MAX_CONTENT_BYTES * 6 + 65536:
        raise ValueError("Draft file is too large.")
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict) or set(data) != {"version", "target", "message"} or data["version"] != 1:
        raise ValueError("Unsupported draft format.")
    if data["target"] != target:
        raise ValueError("Draft destination differs. Select its original connection and queue before loading it.")
    return OutgoingMessage.from_dict(data["message"])
