"""Modal composer navigation and editing, independent of curses drawing."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .outgoing import OutgoingMessage, OutgoingProperty, PROPERTY_TYPES, load_body, load_draft, save_draft


@dataclass
class TextBuffer:
    text: str = ""
    cursor: int = 0
    scroll_row: int = 0
    scroll_col: int = 0
    preferred_col: int | None = None

    def edit(self, key, multiline=False, rows=8):
        start = self.text.rfind("\n", 0, self.cursor) + 1
        end = self.text.find("\n", self.cursor)
        end = len(self.text) if end < 0 else end
        if key == "LEFT":
            self.cursor = max(0, self.cursor - 1)
        elif key == "RIGHT":
            self.cursor = min(len(self.text), self.cursor + 1)
        elif key == "HOME":
            self.cursor = start if multiline else 0
        elif key == "END":
            self.cursor = end if multiline else len(self.text)
        elif key in ("UP", "DOWN", "PGUP", "PGDOWN") and multiline:
            lines = self.text.split("\n")
            row = self.text[:self.cursor].count("\n")
            col = self.cursor - start if self.preferred_col is None else self.preferred_col
            step = (-1 if key in ("UP", "PGUP") else 1) * (rows if key.startswith("PG") else 1)
            row = max(0, min(len(lines) - 1, row + step))
            self.cursor = sum(len(line) + 1 for line in lines[:row]) + min(col, len(lines[row]))
            self.preferred_col = col
            return
        elif key == "BACKSPACE" and self.cursor:
            self.text = self.text[:self.cursor - 1] + self.text[self.cursor:]
            self.cursor -= 1
        elif key == "DELETE":
            self.text = self.text[:self.cursor] + self.text[self.cursor + 1:]
        elif key == "\x15":
            self.text = self.text[:start] + self.text[end:] if multiline else ""
            self.cursor = start if multiline else 0
        else:
            value = "\n" if key == "ENTER" and multiline else key
            if not isinstance(value, str) or len(value) != 1 or not (value.isprintable() or value == "\n"):
                return
            self.text = self.text[:self.cursor] + value + self.text[self.cursor:]
            self.cursor += len(value)
        self.preferred_col = None

    def place(self, row, column):
        from .screen import safe_text
        from wcwidth import wcswidth
        lines = self.text.split("\n")
        row = max(0, min(len(lines) - 1, row))
        used, index = 0, 0
        for char in lines[row]:
            size = max(0, wcswidth(safe_text(char)))
            if used + size > column:
                break
            used += size
            index += 1
        self.cursor = sum(len(line) + 1 for line in lines[:row]) + index
        self.preferred_col = None


@dataclass
class PropertyEditor:
    name: TextBuffer = field(default_factory=TextBuffer)
    kind: str = "String"
    value: TextBuffer = field(default_factory=TextBuffer)


class Composer:
    def __init__(self, target, index, epoch):
        self.target, self.connection_index, self.epoch = dict(target), index, epoch
        self.stage, self.focus = "edit", "body"
        self.error = self.notice = ""
        self.prompt_kind, self.prompt = None, None
        self.job, self.prepared = None, None
        self.editor_rows, self.property_start = 8, 0
        self.preview_scroll = 0
        self.set_message(OutgoingMessage())

    def set_message(self, draft):
        self.fields = {name: TextBuffer(value, 0 if name == "body" else len(value)) for name, value in
                       (("body", draft.body), ("priority", str(draft.priority)), ("ttl", str(draft.ttl_ms)),
                        ("jms_type", draft.jms_type), ("correlation_id", draft.correlation_id))}
        self.persistent = draft.persistent
        self.properties = [PropertyEditor(TextBuffer(item.name, len(item.name)), item.kind, TextBuffer(item.value, len(item.value))) for item in draft.properties] or [PropertyEditor()]
        self.property_start = 0

    def message(self):
        try:
            priority = int(self.fields["priority"].text)
        except ValueError:
            raise ValueError("Priority must be an integer from 0 through 9.") from None
        try:
            ttl = int(self.fields["ttl"].text)
        except ValueError:
            raise ValueError("TTL must be a non-negative number of milliseconds.") from None
        properties = [OutgoingProperty(item.name.text, item.kind, item.value.text) for item in self.properties
                      if item.name.text or item.value.text]
        draft = OutgoingMessage(self.fields["body"].text, priority, self.persistent, ttl,
                                self.fields["jms_type"].text, self.fields["correlation_id"].text, properties)
        draft.validate()
        return draft

    def order(self):
        if self.stage == "preview":
            return ["edit", "save_draft", "send", "cancel"]
        if self.stage == "sending":
            return []
        if self.stage == "sent":
            return ["new", "close"]
        if self.stage == "error":
            return (["edit"] if self.job and self.job.outcome == "not_sent" else []) + ["save_draft", "close"]
        result = ["priority", "persistent", "ttl", "body", "load_body", "jms_type", "correlation_id"]
        for index in range(len(self.properties)):
            result += [f"p:{index}:{field}" for field in ("name", "kind", "value", "remove")]
        return result + ["add_property", "load_draft", "save_draft", "review", "cancel"]

    def move_focus(self, step):
        order = self.order()
        if order:
            current = order.index(self.focus) if self.focus in order else 0
            self.focus = order[(current + step) % len(order)]

    def buffer(self):
        if self.focus in self.fields:
            return self.fields[self.focus]
        if self.focus.startswith("p:"):
            _, index, name = self.focus.split(":")
            if name in ("name", "value"):
                return getattr(self.properties[int(index)], name)
        return None

    def start_prompt(self, kind):
        self.prompt_kind = kind
        text = ""
        if kind == "save_draft":
            name = "".join(char if char.isalnum() or char in "-_." else "_" for char in self.target["queue"])
            text = str(Path.cwd() / "drafts" / f"{name}-{datetime.now():%Y%m%d-%H%M%S-%f}.json")
        self.prompt = TextBuffer(text, len(text))
        self.error = ""

    def finish_prompt(self):
        path = self.prompt.text.strip().strip('"')
        if not path:
            raise ValueError("Enter a file path.")
        if self.prompt_kind == "load_body":
            self.fields["body"] = TextBuffer(load_body(path))
            self.focus, self.notice = "body", "Loaded body from " + path
        elif self.prompt_kind == "load_draft":
            self.set_message(load_draft(path, self.target))
            self.focus, self.notice = "body", "Loaded draft from " + path
        else:
            draft = self.prepared if self.stage in ("preview", "error") else self.message()
            self.notice = "Draft saved: " + str(save_draft(path, self.target, draft))
        self.prompt_kind, self.prompt, self.error = None, None, ""

    def sync(self):
        if self.stage == "sending" and self.job and self.job.status != "sending":
            self.stage = self.job.status
            self.focus = "close" if self.stage == "sent" or self.job.outcome == "unknown" else "edit"
            self.error = self.job.error

    def activate(self, action, controller):
        self.error = ""
        if action in ("cancel", "close"):
            return True
        if action in ("load_body", "load_draft", "save_draft"):
            self.start_prompt(action)
        elif action == "persistent":
            self.persistent = not self.persistent
        elif action == "add_property":
            if len(self.properties) >= 100:
                raise ValueError("At most 100 properties are supported.")
            self.properties.append(PropertyEditor())
            self.focus = f"p:{len(self.properties) - 1}:name"
        elif action.startswith("p:"):
            _, index, field = action.split(":")
            index = int(index)
            if field == "kind":
                item = self.properties[index]
                item.kind = PROPERTY_TYPES[(PROPERTY_TYPES.index(item.kind) + 1) % len(PROPERTY_TYPES)]
            elif field == "remove":
                del self.properties[index]
                self.focus = "add_property"
        elif action == "review":
            self.prepared = self.message()
            self.stage, self.focus, self.preview_scroll = "preview", "edit", 0
        elif action == "edit":
            self.stage, self.focus = "edit", "body"
        elif action == "new":
            self.set_message(OutgoingMessage())
            self.stage, self.focus, self.notice, self.job = "edit", "body", "", None
        elif action == "send" and self.stage == "preview":
            self.job = controller.send_message(self.connection_index, self.epoch, self.target["queue"], self.prepared)
            self.stage = "sending"
        return False

    def handle_key(self, key, controller):
        self.sync()
        if self.stage == "sending":
            return False
        try:
            if self.prompt is not None:
                if key in ("ESC", "QUIT"):
                    self.prompt, self.prompt_kind, self.error = None, None, ""
                elif key == "ENTER":
                    self.finish_prompt()
                else:
                    self.prompt.edit(key)
                return False
            if key in ("ESC", "QUIT"):
                if self.stage == "preview":
                    self.stage, self.focus = "edit", "body"
                    return False
                return True
            if key in ("TAB", "BACKTAB"):
                self.move_focus(1 if key == "TAB" else -1)
                return False
            if key == "F4" and self.stage != "sent":
                self.start_prompt("save_draft")
                return False
            if self.stage == "edit" and key in ("F2", "F3", "F5", "F6"):
                return self.activate({"F2": "review", "F3": "load_body", "F5": "load_draft", "F6": "add_property"}[key], controller)
            if self.stage != "edit":
                if key == "ENTER":
                    return self.activate(self.focus, controller)
                if key in ("UP", "PGUP", "DOWN", "PGDOWN"):
                    self.preview_scroll = max(0, self.preview_scroll + (-1 if key in ("UP", "PGUP") else 1) * (8 if key.startswith("PG") else 1))
                return False
            buffer = self.buffer()
            if buffer is not None:
                if key == "ENTER" and self.focus != "body":
                    self.move_focus(1)
                else:
                    buffer.edit(key, self.focus == "body", self.editor_rows)
                self.error = ""
            elif key == "ENTER" or (key == " " and (self.focus == "persistent" or self.focus.endswith(":kind"))):
                return self.activate(self.focus, controller)
        except (ValueError, OSError) as error:
            self.error = str(error)
        return False

    def handle_mouse(self, frame, x, y, click, wheel, controller):
        self.sync()
        action, _ = frame.action_at(x, y)
        if click and action == "close_prompt" and self.prompt is not None:
            self.prompt, self.prompt_kind, self.error = None, None, ""
            return False
        if click and action == "close_composer" and self.prompt is None:
            return True
        if self.stage == "sending":
            return False
        try:
            hits = getattr(frame, "composer_hits", [])
            for action, left, top, width, height in hits:
                if left <= x < left + width and top <= y < top + height:
                    if wheel:
                        if action == "body":
                            self.fields["body"].edit("PGUP" if wheel < 0 else "PGDOWN", True, self.editor_rows)
                        elif action == "preview_body":
                            self.preview_scroll = max(0, self.preview_scroll + wheel)
                        elif action.startswith("p:") or action == "property_list":
                            self.property_start = max(0, min(len(self.properties) - 1, self.property_start + wheel))
                            self.focus = f"p:{self.property_start}:name"
                        return False
                    if not click:
                        return False
                    if action in ("property_list", "preview_body"):
                        return False
                    if action == "path" and self.prompt:
                        self.prompt.place(0, self.prompt.scroll_col + x - left)
                        return False
                    if self.prompt is not None:
                        return False
                    self.focus = action
                    buffer = self.buffer() if self.stage == "edit" else None
                    if buffer is not None:
                        buffer.place(buffer.scroll_row + y - top, buffer.scroll_col + x - left)
                        return False
                    return self.activate(action, controller)
        except (ValueError, OSError) as error:
            self.error = str(error)
        return False
