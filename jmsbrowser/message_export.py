"""Complete-message JSON exports and their small destination dialog."""
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

from .composer import TextBuffer


def needs_full_message(message):
    return any(message.copy_requires_fetch(tab) for tab in (0, 1, 2))


def write_export(path, connection, queue, messages):
    path = Path(path).expanduser().resolve()
    if any(needs_full_message(message) for message in messages):
        raise ValueError("The full content could not be read; no export was written.")
    payload = {"format": "jmsbrowser.messages.v1", "exported_at": datetime.now(timezone.utc).isoformat(),
               "connection": connection, "queue": queue, "messages": [message.to_dict() for message in messages]}
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects existing exports. Remove a partial file on a
    # write/encoding failure, while leaving an existing destination untouched.
    output = path.open("x", encoding="utf-8", newline="\n")
    try:
        with output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.write("\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


class MessageExport:
    def __init__(self, messages, connection, queue, owner):
        self.messages = tuple(messages)
        self.connection, self.queue = connection, queue
        self.owner = owner
        name = "".join(char if char.isalnum() or char in "-_." else "_" for char in queue)
        path = str(Path.cwd() / "exports" / f"{name}-{datetime.now():%Y%m%d-%H%M%S-%f}.json")
        self.path = TextBuffer(path, len(path))
        self.focus = 0
        self.future = None
        self.result = None
        self.error = ""

    @property
    def working(self):
        return self.future is not None and not self.future.done()

    def sync(self):
        if self.future is not None and self.future.done():
            try:
                self.result = self.future.result()
            except Exception as error:
                self.error = "Export failed: " + str(error)
            self.future = None

    def start(self, controller):
        if self.working or self.result:
            return
        self.error = ""
        try:
            state = controller.current
            owner = (controller.connection_index, state.epoch, state.selected_queue.name if state.selected_queue else None)
            if self.owner != owner:
                raise ValueError("The queue or connection changed. Reopen the export dialog.")
            path = self.path.text.strip().strip('"')
            if not path:
                raise ValueError("Enter an export file path.")
            if Path(path).expanduser().exists():
                raise FileExistsError("File already exists: " + path)
            self.future = controller.export_messages(self.messages, path)
        except (ValueError, OSError) as error:
            self.error = "Export failed: " + str(error)

    def key(self, key, controller):
        self.sync()
        if self.working:
            return False
        if key in ("ESC", "QUIT"):
            return True
        if self.result:
            return key == "ENTER"
        if key in ("TAB", "BACKTAB"):
            self.focus = (self.focus + (1 if key == "TAB" else -1)) % 3
        elif key == "ENTER":
            if self.focus == 2:
                return True
            self.start(controller)
        elif self.focus == 0:
            self.path.edit(key)
        return False


def draw_export(frame, dialog):
    from .composer_screen import editor
    dialog.sync()
    frame.actions, frame.regions, frame.row_hits, frame.dividers = [], [], {}, []
    frame.composer_hits = []
    x, y, width, height = 5, max(3, (frame.height - 16) // 2), frame.width - 10, 16
    frame.fill(x, y, width, height)
    frame.box(x, y, width, height, "Export selected messages", True)
    if not dialog.working:
        frame.close_button(x, y, width, "close_export")
    frame.text(x + 3, y + 2, f"{dialog.connection} / {dialog.queue}  ·  {len(dialog.messages)} selected", "accent", width=width - 6)
    frame.text(x + 3, y + 4, "JSON: complete bodies, JMS headers and typed properties. Messages stay on the queue.", "muted", width=width - 6)
    frame.text(x + 3, y + 6, "Destination file", "muted", width=width - 6)
    if dialog.result:
        frame.text(x + 3, y + 7, str(dialog.result), width=width - 6)
        frame.text(x + 3, y + 10, f"Exported {len(dialog.messages)} messages.", "green", width=width - 6)
    else:
        # Reuse the Unicode-aware single-line editor, without composer actions.
        proxy = SimpleNamespace(focus="export_path" if dialog.focus == 0 else "", prompt=None)
        editor(frame, proxy, "export_path", dialog.path, x + 3, y + 7, width - 6)
        frame.actions.append(("export_path", None, x + 3, y + 7, width - 6))
        frame.text(x + 3, y + 9, "Reading full content and writing export…" if dialog.working else "Existing files are never overwritten.", "muted", width=width - 6)
        if not dialog.working:
            for action, label, left, focused in (("export_start", " Export JSON ", x + 3, dialog.focus == 1),
                                                ("close_export", " Cancel ", x + 20, dialog.focus == 2)):
                frame.text(left, y + 11, label, "accent", "selected" if focused else "bar")
                frame.actions.append((action, None, left, y + 11, len(label)))
    frame.text(x + 3, y + 13, dialog.error, "error", width=width - 6)
