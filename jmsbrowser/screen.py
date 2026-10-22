"""Character-grid renderer shared by curses, screenshots and navigation tests."""
from __future__ import annotations

from dataclasses import dataclass, field
import textwrap
import time

from wcwidth import wcwidth, wcswidth

from .domain import format_message_datetime, format_size
from .body_view import body_layout
from .terminal_text import cell_offset, clipped, safe_text

PALETTE = {"bg": "#192322", "text": "#e5e9df", "muted": "#9aaea3", "border": "#4b6257",
           "accent": "#c4d891", "selected": "#364b3c", "green": "#8bd5a0", "error": "#edaaa0", "bar": "#2a3932"}
HEADERS = ("MessageId", "Datetime", "Size", "Body")
MIN_WIDTH, MIN_HEIGHT = 120, 28
ASCII_MAP = str.maketrans({"┌": "+", "┐": "+", "└": "+", "┘": "+", "─": "-", "│": "|", "●": "*", "○": "o", "…": "~", "·": ".", "↑": "^", "↓": "v", "←": "<", "→": ">", "↔": "|"})

MIN_PANEL_WIDTHS = (18, 34, 66)


def panel_widths(width, requested=None, depth_width=5, consumers_width=11):
    """Clamp a session layout to the terminal, keeping all columns usable."""
    left, middle = requested or (max(22, min(28, width // 6)), max(38, min(50, width // 5 + 12)))
    minimum_left, minimum_middle, minimum_right = MIN_PANEL_WIDTHS
    # Keep each sortable header and both counters visible at normal depths.
    minimum_middle = min(max(minimum_middle, depth_width + consumers_width + 13), width - minimum_left - minimum_right - 2)
    left = max(minimum_left, min(left, width - minimum_middle - minimum_right - 2))
    middle = max(minimum_middle, min(middle, width - left - minimum_right - 2))
    return left, middle


@dataclass
class ViewOptions:
    filters: list[str] = field(default_factory=lambda: ["", "", ""])
    queue_sort: str = "name"
    queue_sort_descending: bool = False
    fullscreen: bool = False
    prompt: str | None = None
    horizontal_scroll: int = 0
    consumer_inspector: bool = False
    consumer_detail: bool = False
    consumer_focus: int = 0
    consumer_scroll: int = 0
    consumer_horizontal: int = 0
    composer: object = None
    panel_widths: tuple[int, int] | None = None
    drag_divider: int | None = None
    body_wrap: bool = True
    body_colors: bool = True
    copy_identity: tuple | None = None
    copy_feedback: str = ""
    copy_error: bool = False
    copy_until: float = 0
    copy_flash_until: float = 0
    search: object = None
    export_dialog: object = None


class Frame:
    def __init__(self, width, height, ascii=False):
        self.width, self.height, self.ascii = width, height, ascii
        self.cells = [[(" ", "text", "bg") for _ in range(width)] for _ in range(height)]
        self.regions, self.row_hits, self.actions = [], {}, []
        self.dividers = []
        self.panel_widths = None
        self.detail_height = 1
        self.detail_total_rows = 0
        self.consumer_rows = self.consumer_detail_height = 1

    def text(self, x, y, value, role="text", bg="bg", width=None):
        if not 0 <= y < self.height or x >= self.width:
            return
        width = max(0, min(self.width - x, self.width if width is None else width))
        text = clipped(value, width)
        if self.ascii:
            text = text.translate(ASCII_MAP)
        cursor = x
        for char in text:
            size = max(0, wcwidth(char))
            if size == 0:
                if cursor > x:
                    previous, fg, background = self.cells[y][cursor - 1]
                    self.cells[y][cursor - 1] = previous + char, fg, background
                continue
            if 0 <= cursor < self.width and cursor + size <= x + width:
                self.cells[y][cursor] = char, role, bg
                if size == 2:
                    self.cells[y][cursor + 1] = "", role, bg
            cursor += size

    def fill(self, x, y, width, height=1, bg="bg"):
        for row in range(max(0, y), min(self.height, y + height)):
            for col in range(max(0, x), min(self.width, x + width)):
                self.cells[row][col] = " ", "text", bg

    def line(self, x, y, width, role="border"):
        self.text(x, y, "─" * width, role, width=width)

    def box(self, x, y, width, height, title, focused=False):
        role = "accent" if focused else "border"
        self.text(x, y, "┌" + "─" * (width - 2) + "┐", role)
        self.text(x + 2, y, f" {title} ", role, width=width - 4)
        for row in range(y + 1, y + height - 1):
            self.text(x, row, "│", role)
            self.text(x + width - 1, row, "│", role)
        self.text(x, y + height - 1, "└" + "─" * (width - 2) + "┘", role)

    def row(self, x, y, width, text, selected=False, role="text", focused=True):
        bg = "selected" if selected and focused else "bar" if selected else "bg"
        self.fill(x, y, width, bg=bg)
        self.text(x, y, text, "accent" if selected and focused else "muted" if selected else role, bg, width)

    def close_button(self, x, y, width, action, focused=False):
        left = x + width - 5
        self.text(left, y, " X ", "accent", "selected" if focused else "bar", 3)
        self.actions.insert(0, (action, None, left, y, 3))

    def divider_at(self, x, y):
        for index, left, top, width, height in self.dividers:
            if left <= x < left + width and top <= y < top + height:
                return index
        return None

    def hit(self, x, y):
        for name, left, top, width, height in self.regions:
            if left <= x < left + width and top <= y < top + height:
                return name, self.row_hits.get((name, y))
        return None, None

    def action_at(self, x, y):
        for action, value, left, top, width in self.actions:
            if top == y and left <= x < left + width:
                return action, value
        return None, None

    def plain_lines(self):
        return ["".join(cell[0] for cell in row) for row in self.cells]


def filtered_indices(items, query, key):
    query = query.casefold()
    return [index for index, item in enumerate(items) if not query or query in key(item).casefold()]


def queue_indices(queues, view):
    indices = filtered_indices(queues, view.filters[1], lambda q: q.name)
    indices.sort(key=lambda index: (queues[index].name.casefold(), queues[index].name))
    if view.queue_sort in ("depth", "consumers"):
        # Stable sorting keeps ties alphabetical; unavailable values stay last.
        known = [index for index in indices if getattr(queues[index], view.queue_sort) is not None]
        missing = [index for index in indices if getattr(queues[index], view.queue_sort) is None]
        known.sort(key=lambda index: getattr(queues[index], view.queue_sort), reverse=view.queue_sort_descending)
        indices = known + missing
    elif view.queue_sort_descending:
        indices.reverse()
    return indices


def page(indices, selected, count):
    position = indices.index(selected) if selected in indices else 0
    start = position // count * count
    return indices[start:start + count], start


def table_widths(available):
    identity = min(40, max(14, available // 4))
    widths = [identity, 19, 12, 0]
    widths[3] = available - sum(widths) - 6  # two spaces between columns
    return widths


def short_id(value, width):
    value = safe_text(value)
    if wcswidth(value) <= width:
        return value
    tail = min(12, width // 2)
    return clipped(value, width - tail) + value[-tail:]


def highlight_matches(frame, text, x, y, width, spans, offset=0):
    """Recolor complete terminal cells without splitting wide/combining text."""
    for start, end, active in spans:
        start = max(start, offset)
        if end <= start:
            continue
        left = wcswidth(text[offset:start])
        right = min(width, wcswidth(text[offset:end]))
        for column in range(left, right):
            if 0 <= y < frame.height and 0 <= x + column < frame.width:
                char, _, _ = frame.cells[y][x + column]
                frame.cells[y][x + column] = char, "bg", "accent" if active else "green"


def draw_metadata(frame, entries, x, y, width, height, scroll=0, horizontal=0, empty="No properties.", search=None, tab=None):
    key_width = min(max((wcswidth(safe_text(key)) for key, _ in entries), default=3), max(3, width // 3))
    key_width = max(3, key_width)
    value_x = x + key_width + 3
    frame.text(x + key_width - 3, y, "Key", "muted")
    frame.text(value_x, y, "Value", "muted")
    frame.line(x, y + 1, width)
    rows = max(1, height - 2)
    start = min(scroll, max(0, len(entries) - rows))
    if not entries:
        frame.text(value_x, y + 2, empty, "muted", width=width - key_width - 3)
    for offset, (key, value) in enumerate(entries[start:start + rows]):
        original_key = safe_text(key)
        key_offset = 0
        if search and search.current >= 0:
            match = search.matches[search.current]
            if match.tab == tab and match.line == start + offset and match.field == "key" and wcswidth(original_key[:match.end]) > key_width:
                key_offset = match.start
        key = clipped(original_key[key_offset:], key_width)
        key_x = x + key_width - wcswidth(key)
        frame.text(key_x, y + 2 + offset, key, "muted", width=key_width)
        frame.text(x + key_width + 1, y + 2 + offset, "│", "border")
        value = safe_text(value)
        frame.text(value_x, y + 2 + offset, value[horizontal:], width=width - key_width - 3)
        if search:
            highlight_matches(frame, original_key, key_x, y + 2 + offset, wcswidth(key),
                              search.spans(tab, start + offset, "key"), key_offset)
            highlight_matches(frame, value, value_x, y + 2 + offset, width - key_width - 3,
                              search.spans(tab, start + offset, "value"), horizontal)
    return start, rows


def detail_identity(controller):
    state = controller.current
    message, queue = state.selected_message, state.selected_queue
    return (controller.connection_index, state.epoch, queue.name if queue else None,
            message.message_id, id(message), controller.detail_tab) if message else None


def draw_detail(frame, state, controller, view, x, y, width, height):
    if height < 4:
        return
    message = state.selected_message
    if message is None:
        if state.busy:
            text = "Loading messages…"
        elif state.selected_queue:
            text = "Queue is empty." if state.messages_loaded else "Press Enter on a queue to load messages."
        else:
            text = "Select a connected server and queue."
        if state.error:
            text = state.error
        frame.text(x + 1, y + 1, text, "error" if state.error else "muted", width=width - 2)
        frame.detail_height = max(1, height - 4)
        return
    tabs = [(1, "Headers"), (2, "Properties"), (0, "Body")]
    tab_x = x + 1
    for index, name in tabs:
        label = f" {name} "
        active = controller.detail_tab == index
        frame.text(tab_x, y, label, "accent" if active else "muted", "selected" if active else "bg", width=x + width - 1 - tab_x)
        frame.actions.append(("detail_tab", index, tab_x, y, len(label)))
        tab_x += len(label) + 3
    frame.text(x + 1, y + 2, f"MessageId: {message.message_id}", "muted", width=width - 2)
    identity = detail_identity(controller)
    search = view.search if view.search and view.search.owner == identity[:-1] else None
    now = time.monotonic()
    if search:
        frame.text(x + 1, y + 1, search.status(), "error" if search.error else "accent", width=width - 2)
    if view.copy_identity == identity and now < view.copy_until:
        frame.text(x + 1, y + 1, view.copy_feedback, "error" if view.copy_error else "accent", width=width - 2)
    if controller.detail_tab in (1, 2):
        lines = message.metadata(controller.detail_tab)
        start, rows = draw_metadata(frame, lines, x + 1, y + 4, width - 2, height - 5,
                                    controller.detail_scroll, view.horizontal_scroll,
                                    "No headers." if controller.detail_tab == 1 else "No properties.", search, controller.detail_tab)
        unit = "rows"
        data_x, data_y, data_width = x + 1, y + 6, width - 2
    else:
        wrap = view.fullscreen or view.body_wrap
        colors = view.fullscreen or view.body_colors
        layout = body_layout(message.body, message.type, message.truncated, width - 8, wrap)
        lines = layout.rows
        frame.fill(x + 1, y + 2, width - 2)
        frame.text(x + 1, y + 2, layout.format, "muted", width=6)
        if not view.fullscreen:
            # Inline controls use fixed slots without moving the detail tabs.
            cursor = x + 8
            for action, label, enabled, shortcut in (("body_wrap", "Wrap", view.body_wrap, "w"),
                                                      ("body_colors", "Color", view.body_colors, "s")):
                text = f" {label}: {'on' if enabled else 'off':<3} ({shortcut}) "
                frame.text(cursor, y + 2, text, "accent" if enabled else "muted", "selected" if enabled else "bar")
                frame.actions.append((action, None, cursor, y + 2, len(text)))
                cursor += len(text) + 2
        rows = max(1, height - 6)
        start = min(controller.detail_scroll, max(0, len(lines) - rows))
        for row, line in enumerate(lines[start:start + rows]):
            label = "   >" if line.continuation else f"{line.number:>4}"
            frame.text(x + 1, y + 4 + row, label, "muted", width=5)
            offset = 0 if wrap else cell_offset(line.text, view.horizontal_scroll)
            # Only paint the visible cells plus an ellipsis probe. A large,
            # unwrapped XML line can contain thousands of off-screen tokens.
            end = offset + cell_offset(line.text[offset:], width - 7)
            visible = line.text[offset:end]
            frame.text(x + 7, y + 4 + row, visible, "text", width=width - 8)
            paint_width = width - 8 - (1 if wcswidth(visible) > width - 8 else 0)
            if colors:
                for left, right, role in line.spans:
                    left = max(left, offset)
                    if left >= end:
                        break
                    if right <= left:
                        continue
                    col = wcswidth(line.text[offset:left])
                    stop = min(paint_width, wcswidth(line.text[offset:min(right, end)]))
                    # Recolor the already-safe cells. Repainting token strings
                    # would overwrite the ellipsis or split wide characters.
                    for column in range(col, stop):
                        cell_x, cell_y = x + 7 + column, y + 4 + row
                        char, _, bg = frame.cells[cell_y][cell_x]
                        frame.cells[cell_y][cell_x] = char, role, bg
            if search:
                spans = [(a - line.offset, b - line.offset, active)
                         for a, b, active in search.spans(0, line.number - 1, "body")]
                highlight_matches(frame, line.text, x + 7, y + 4 + row, width - 8, spans, offset)
        unit = "rows" if wrap else "lines"
        data_x, data_y, data_width = x + 7, y + 4, width - 8
    for row in range(len(lines[start:start + rows])):
        frame.actions.append(("copy_detail", identity, data_x, data_y + row, data_width))
    if view.copy_identity == identity and now < view.copy_flash_until:
        for row in range(data_y, min(data_y + rows, frame.height, y + height - 1)):
            for column in range(data_x, min(data_x + data_width, frame.width)):
                char, _, _ = frame.cells[row][column]
                frame.cells[row][column] = char, "bg", "accent"
    frame.detail_height = rows
    frame.detail_total_rows = len(lines)
    footer = f"{message.type}  ·  priority {message.priority}  ·  {format_size(message.size)}"
    if message.truncated:
        footer += "  ·  PREVIEW TRUNCATED"
    footer += f"  ·  {unit} {min(start + 1, len(lines))}-{min(len(lines), start + rows)} / {len(lines)}"
    frame.text(x + 1, y + height - 1, footer, "muted", width=width - 2)


def draw_consumer_inspector(frame, controller, view):
    state = controller.current
    snapshot = state.consumer_snapshot
    consumers = snapshot.consumers if snapshot else []
    unit = snapshot.unit if snapshot else ("input handles" if state.config.type == "ibm" else "consumers")
    selected_queue = state.selected_queue.name if state.selected_queue else "No queue selected"
    x, y, width, height = 6, 5, frame.width - 12, frame.height - 10
    frame.fill(x, y, width, height)
    frame.box(x, y, width, height, "Queue consumers · " + selected_queue, True)
    # The inspector is modal: underlying panel actions cannot receive clicks.
    frame.actions, frame.regions, frame.row_hits = [], [], {}
    frame.dividers = []
    frame.close_button(x, y, width, "close_consumers", focused=view.consumer_focus == 2)
    label = " Refresh consumers "
    frame.text(x + 3, y + 2, label, "accent", "selected" if view.consumer_focus == 1 else "bg")
    frame.actions.append(("refresh_consumers", None, x + 3, y + 2, len(label)))
    status = ("Loading…" if state.consumer_status == "loading" else
              "Unavailable" if state.consumer_status == "error" else
              f"{len(consumers)} attached {unit}" if snapshot else "No consumer data")
    frame.text(x + 3, y + 4, f"{state.config.name}  ·  {status}  ·  browser rows hidden", "muted", width=width - 6)
    selected = consumers[min(state.consumer_index, len(consumers) - 1)] if consumers else None
    if state.consumer_status == "error":
        for offset, line in enumerate(textwrap.wrap(safe_text(state.consumer_error), width - 6)[:height - 12]):
            frame.text(x + 3, y + 7 + offset, line, "error", width=width - 6)
    elif state.consumer_status == "loading":
        frame.text(x + 3, y + 7, "Loading queue consumer metadata…", "muted", width=width - 6)
    elif view.consumer_detail and selected:
        _, rows = draw_metadata(frame, selected.rows(), x + 3, y + 6, width - 6, height - 11,
                                view.consumer_scroll, view.consumer_horizontal)
        frame.consumer_detail_height = rows
        frame.regions.append(("consumer_detail", x + 2, y + 6, width - 4, height - 11))
    else:
        available = width - 6
        user_width = min(24, max(12, available // 7))
        columns = [user_width, 14, 14, available - user_width - 66, 30]
        headings = ("User", "Consumer count", "Producer count", "Remote address", "Creation time")
        cursor = x + 3
        for label, col_width in zip(headings, columns):
            frame.text(cursor, y + 6, label, "muted", width=col_width)
            cursor += col_width + 2
        frame.line(x + 2, y + 7, width - 4)
        rows = max(1, height - 13)
        frame.consumer_rows = rows
        visible, _ = page(list(range(len(consumers))), state.consumer_index, rows)
        for offset, index in enumerate(visible):
            item, row = consumers[index], y + 8 + offset
            active = index == state.consumer_index
            frame.row(x + 3, row, available, "", active)
            created = format_message_datetime(item.creation_time) if type(item.creation_time) is int else item.creation_time
            values = (item.user, str(item.consumer_count) if item.consumer_count is not None else "n/a",
                      str(item.producer_count) if item.producer_count is not None else "n/a", item.remote_address, created)
            cursor = x + 3
            for value, col_width in zip(values, columns):
                frame.text(cursor, row, value or "n/a", "accent" if active else "text", "selected" if active else "bg", col_width)
                cursor += col_width + 2
            frame.row_hits[("consumers", row)] = index
        frame.regions.append(("consumers", x + 2, y + 8, width - 4, rows))
        if not consumers:
            frame.text(x + 3, y + 9, f"0 attached {unit}.", "muted", width=width - 6)
    frame.line(x + 2, y + height - 5, width - 4)
    if snapshot and snapshot.note:
        frame.text(x + 3, y + height - 4, snapshot.note, "muted", width=width - 6)
    hint = "↑↓/PgUp/PgDn scroll  [ / ] values  Enter/Esc back to list" if view.consumer_detail else "↑↓ select  Enter: full consumer / connection / selector details"
    frame.text(x + 3, y + height - 3, hint, "accent", width=width - 6)
    frame.text(x + 3, y + height - 2, "c: refresh consumers  Tab: list / refresh / X  Esc: close inspector", "muted", width=width - 6)


def render(controller, width, height, view=None):
    view = view or ViewOptions()
    frame = Frame(width, height, controller.config.ui.ascii)
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        frame.text(1, 1, f"Resize terminal to at least {MIN_WIDTH} x {MIN_HEIGHT} (current {width} x {height}).", "accent", width=width - 2)
        frame.text(1, 3, "q: quit", width=width - 2)
        return frame
    state = controller.current
    frame.fill(0, 0, width, bg="bar")
    frame.text(2, 0, "JMS BROWSER", "accent", "bar")
    panel_y, panel_h = 2, height - 3
    if view.fullscreen:
        frame.box(0, panel_y, width, panel_h, "Message detail · Escape returns", True)
        frame.close_button(0, panel_y, width, "close_detail")
        frame.regions.append(("detail", 1, panel_y + 1, width - 2, panel_h - 2))
        draw_detail(frame, state, controller, view, 1, panel_y + 2, width - 2, panel_h - 4)
    else:
        queue_order = queue_indices(state.queues, view)
        depth_width = max(7, max((len(str(state.queues[index].depth)) for index in queue_order), default=1))
        count_digits = max(3, max((len(str(state.queues[index].consumers)) if state.queues[index].consumers is not None else 3
                                   for index in queue_order), default=1))
        consumers_width = max(11, count_digits)
        left, middle = frame.panel_widths = panel_widths(width, view.panel_widths, depth_width, consumers_width)
        right_x, right_w = left + middle + 2, width - left - middle - 2
        for pane, x, w, title in ((0, 0, left, "Connections"), (1, left + 1, middle, "Queues"), (2, right_x, right_w, "Messages")):
            focused = controller.pane == pane
            frame.box(x, panel_y, w, panel_h, "")
            frame.fill(x + 1, panel_y, w - 2, bg="accent" if focused else "bar")
            frame.text(x + 2, panel_y, title, "bg" if focused else "muted", "accent" if focused else "bar", w - 4)
            frame.regions.append((("connections", "queues", "messages")[pane], x, panel_y, w, panel_h))
            if view.filters[pane] and pane != 1:
                frame.text(x + 2, panel_y + 1, "/ " + view.filters[pane], "accent", width=w - 4)
        indices = filtered_indices(controller.connections, view.filters[0], lambda s: s.config.name)
        visible, start = page(indices, controller.connection_index, max(1, (panel_h - 7) // 3))
        for offset, index in enumerate(visible):
            item = controller.connections[index]
            y = panel_y + 4 + offset * 3
            icon = "●" if item.status == "connected" else ("!" if item.status == "error" else ("~" if item.status == "connecting" else "○"))
            selected = index == controller.connection_index
            focused = controller.pane == 0
            bg = "selected" if selected and focused else "bar" if selected else "bg"
            frame.row(2, y, left - 4, f"{'>' if selected and focused else ' '} {icon} {item.config.name}", selected, focused=focused)
            frame.text(4, y, icon, "green" if item.status == "connected" else ("error" if item.status == "error" else "muted"), bg)
            subtitle = f"{item.config.type.upper()} · {item.config.queue_manager or item.config.port}" if item.status == "connected" else item.status
            frame.text(4, y + 1, subtitle, "muted", width=left - 6)
            frame.row_hits[("connections", y)] = index
            frame.row_hits[("connections", y + 1)] = index
        qx = left + 3
        queue_available = middle - 4
        queue_width = queue_available - depth_width - consumers_width - 2
        # Very large counters get extra rows when the terminal cannot fit a
        # queue name and both numbers side by side. Never abbreviate a counter.
        compact = queue_width < 7
        split_stats = compact and depth_width + count_digits + 1 > queue_available
        stride = 1 + (2 if split_stats else 1) if compact else 1
        depth_x = qx if compact else qx + queue_width + 1
        consumers_x = qx + queue_available - consumers_width
        if view.filters[1]:
            frame.text(qx, panel_y + 1, "/ " + view.filters[1], "accent", width=middle - 4)
        for column, title, x, span in (("name", "Queue", qx, 7), ("depth", "Depth", depth_x, 7),
                                       ("consumers", "Consumers", consumers_x, 11)):
            active = view.queue_sort == column
            arrow = "↓" if view.queue_sort_descending else "↑"
            label = title + (" " + arrow if active else "")
            header_y = panel_y + 2 + (1 if compact and column != "name" else 0)
            frame.text(x, header_y, label, "accent" if active else "muted", width=span)
            frame.actions.append(("queue_sort", column, x, header_y, span))
        indices = queue_order
        visible, start = page(indices, state.queue_index, max(1, (panel_h - 7 - int(compact)) // stride))
        for offset, index in enumerate(visible):
            item, y = state.queues[index], panel_y + 4 + int(compact) + offset * stride
            selected = index == state.queue_index
            focused = controller.pane == 1
            bg = "selected" if selected and focused else "bar" if selected else "bg"
            role = "accent" if selected and focused else "muted" if selected else "text"
            for row in range(y, y + stride):
                frame.row(qx, row, queue_available, "", selected, focused=focused)
            name_width = queue_available if compact else queue_width
            frame.text(qx, y, ("> " if selected and focused else "  ") + clipped(item.name, name_width - 2), role, bg, name_width)
            pending = state.all_depths_pending or item.name in state.depth_pending
            depth = "..." if pending else str(item.depth)
            consumers = "..." if pending else str(item.consumers) if item.consumers is not None else "n/a"
            if split_stats:
                for row, label, value in ((y + 1, "Depth", depth), (y + 2, "Consumers", consumers)):
                    frame.text(qx, row, label, "muted", bg)
                    frame.text(qx + 10, row, value.rjust(queue_available - 10), role, bg, queue_available - 10)
                    frame.row_hits[("queues", row)] = index
            elif compact:
                stats_depth_width = queue_available - count_digits - 1
                frame.text(qx, y + 1, depth.rjust(stats_depth_width), role, bg, stats_depth_width)
                frame.text(qx + stats_depth_width + 1, y + 1, consumers.rjust(count_digits), role, bg, count_digits)
                frame.row_hits[("queues", y + 1)] = index
            else:
                frame.text(depth_x, y, depth.rjust(depth_width), role, bg, depth_width)
                frame.text(consumers_x, y, consumers.rjust(consumers_width), role, bg, consumers_width)
            frame.row_hits[("queues", y)] = index
        frame.text(qx, panel_y + panel_h - 3, f"{len(indices)} queues", "muted", width=middle - 4)
        mx, available = right_x + 2, right_w - 4
        column_widths = table_widths(available)
        x = mx
        for label, column_width in zip(HEADERS, column_widths):
            frame.text(x, panel_y + 2, label, "muted", width=column_width)
            x += column_width + 2
        # Divide the panel interior evenly, reserving one row for the separator.
        list_height = (panel_h - 3) // 2
        divider = panel_y + 1 + list_height
        detail_y = divider + 1
        detail_height = panel_y + panel_h - 1 - detail_y
        rows = max(1, divider - (panel_y + 4))
        indices = filtered_indices(state.messages, view.filters[2], lambda m: f"{m.message_id} {m.type} {m.priority} {m.body}")
        visible, start = page(indices, state.message_index, rows)
        for offset, index in enumerate(visible):
            message, y = state.messages[index], panel_y + 4 + offset
            selected = index == state.message_index
            focused = controller.pane == 2
            bg = "selected" if selected and focused else "bar" if selected else "bg"
            role = "accent" if selected and focused else "muted" if selected else "text"
            frame.row(mx, y, available, "", selected, focused=focused)
            marked = message.selection_key in state.marked_messages
            if marked:
                role = "accent" if selected and focused else "green"
                frame.text(mx - 1, y, "*", role, bg)
            elif selected and focused:
                frame.text(mx - 1, y, ">", "accent")
            x = mx
            values = (short_id(message.message_id, column_widths[0]), format_message_datetime(message.headers.get("JMSTimestamp")),
                      format_size(message.size), " ".join(message.body.split()))
            for value, column_width in zip(values, column_widths):
                frame.text(x, y, value, role, bg, column_width)
                x += column_width + 2
            frame.row_hits[("messages", y)] = index
        frame.line(mx, divider, available)
        selection = f" · {len(state.export_selection)} selected" if state.export_selection else ""
        frame.text(mx + 1, divider, f" {min(start + 1, len(indices))}-{min(start + rows, len(indices))} / {len(indices)}{selection} ", "muted")
        if state.messages and not indices:
            frame.text(mx + 1, detail_y + 1, "No messages match the filter. Escape clears it.", "muted", width=available - 2)
        else:
            draw_detail(frame, state, controller, view, mx, detail_y, available, detail_height)
        frame.regions.insert(0, ("detail", mx, detail_y, available, detail_height))
        for index, divider_x in enumerate((left, right_x - 1)):
            frame.dividers.append((index, divider_x - 1, panel_y, 3, panel_h))
            frame.text(divider_x, panel_y + panel_h // 2, "↔", "accent", "selected" if view.drag_divider == index else "bar")
    if view.consumer_inspector:
        draw_consumer_inspector(frame, controller, view)
    if view.composer is not None:
        from .composer_screen import draw_composer
        draw_composer(frame, view.composer)
    if view.export_dialog is not None:
        from .message_export import draw_export
        draw_export(frame, view.export_dialog)
    from .footer import draw_footer
    draw_footer(frame, controller, view)
    return frame
