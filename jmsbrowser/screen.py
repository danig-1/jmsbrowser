"""Character-grid renderer shared by curses, screenshots and navigation tests."""
from __future__ import annotations

from dataclasses import dataclass, field
import textwrap

from wcwidth import wcwidth, wcswidth

from .domain import format_size
from .body_view import body_layout
from .terminal_text import cell_offset, clipped, safe_text

PALETTE = {"bg": "#192322", "text": "#e5e9df", "muted": "#9aaea3", "border": "#4b6257",
           "accent": "#c4d891", "selected": "#364b3c", "green": "#8bd5a0", "error": "#edaaa0", "bar": "#2a3932"}
HEADERS = ("MessageId", "Type", "Prio", "Size", "Body")
MIN_WIDTH, MIN_HEIGHT = 120, 28
ASCII_MAP = str.maketrans({"┌": "+", "┐": "+", "└": "+", "┘": "+", "─": "-", "│": "|", "●": "*", "○": "o", "…": "~", "·": ".", "↑": "^", "↓": "v", "←": "<", "→": ">", "↔": "|"})

MIN_PANEL_WIDTHS = (18, 24, 66)


def panel_widths(width, requested=None):
    """Clamp a session layout to the terminal, keeping all columns usable."""
    left, middle = requested or (max(22, min(28, width // 6)), max(26, min(34, width // 5)))
    minimum_left, minimum_middle, minimum_right = MIN_PANEL_WIDTHS
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
    if view.queue_sort == "depth":
        # Keep equal-depth queues alphabetical in either direction.
        indices.sort(key=lambda index: queues[index].depth, reverse=view.queue_sort_descending)
    elif view.queue_sort_descending:
        indices.reverse()
    return indices


def page(indices, selected, count):
    position = indices.index(selected) if selected in indices else 0
    start = position // count * count
    return indices[start:start + count], start


def table_widths(available):
    identity = min(40, max(14, available // 4))
    widths = [identity, 11, 4, 12, 0]
    widths[4] = available - sum(widths) - 8  # two spaces between columns
    return widths


def short_id(value, width):
    value = safe_text(value)
    if wcswidth(value) <= width:
        return value
    tail = min(12, width // 2)
    return clipped(value, width - tail) + value[-tail:]


def draw_metadata(frame, entries, x, y, width, height, scroll=0, horizontal=0, empty="No properties."):
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
        key = clipped(key, key_width)
        frame.text(x + key_width - wcswidth(key), y + 2 + offset, key, "muted", width=key_width)
        frame.text(x + key_width + 1, y + 2 + offset, "│", "border")
        frame.text(value_x, y + 2 + offset, safe_text(value)[horizontal:], width=width - key_width - 3)
    return start, rows


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
    if controller.detail_tab in (1, 2):
        lines = message.metadata(controller.detail_tab)
        start, rows = draw_metadata(frame, lines, x + 1, y + 4, width - 2, height - 5,
                                    controller.detail_scroll, view.horizontal_scroll,
                                    "No headers." if controller.detail_tab == 1 else "No properties.")
        unit = "rows"
    else:
        layout = body_layout(message.body, message.type, message.truncated, width - 8, view.body_wrap)
        lines = layout.rows
        # Body controls use fixed slots and do not change the detail tab positions.
        frame.fill(x + 1, y + 2, width - 2)
        frame.text(x + 1, y + 2, layout.format, "muted", width=6)
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
            offset = 0 if view.body_wrap else cell_offset(line.text, view.horizontal_scroll)
            # Only paint the visible cells plus an ellipsis probe. A large,
            # unwrapped XML line can contain thousands of off-screen tokens.
            end = offset + cell_offset(line.text[offset:], width - 7)
            visible = line.text[offset:end]
            frame.text(x + 7, y + 4 + row, visible, "text", width=width - 8)
            paint_width = width - 8 - (1 if wcswidth(visible) > width - 8 else 0)
            if view.body_colors:
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
        unit = "rows" if view.body_wrap else "lines"
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
    for focus, label, action, left in ((1, " Refresh consumers ", "refresh_consumers", x + 3),
                                       (2, " Close ", "close_consumers", x + width - 11)):
        active = view.consumer_focus == focus
        frame.text(left, y + 2, label, "accent", "selected" if active else "bg")
        frame.actions.append((action, None, left, y + 2, len(label)))
    status = ("Loading…" if state.consumer_status == "loading" else
              "Unavailable" if state.consumer_status == "error" else
              f"{len(consumers)} attached {unit}" if snapshot else "No consumer data")
    frame.text(x + 3, y + 4, f"{state.config.name}  ·  {status}  ·  browser sessions hidden", "muted", width=width - 6)
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
        columns = [16, max(19, (available - 50) // 2), available - 50 - max(19, (available - 50) // 2), 14, 12]
        headings = ("PID / TID" if unit == "input handles" else "ConsumerId", "Application / Client", "Remote endpoint", "User", "Protocol")
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
            identity = f"{item.details.get('ProcessId', 'n/a')} / {item.details.get('ThreadId', 'n/a')}" if unit == "input handles" else item.consumer_id
            values = (identity, item.application, item.remote_address, item.user, item.protocol)
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
    frame.text(x + 3, y + height - 2, "c: refresh consumers  Tab: list / refresh / close  Esc: close inspector", "muted", width=width - 6)


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
    if controller.demo:
        frame.text(17, 0, "DEMO", bg="bar")
    frame.text(width - 15, 0, "NON-CONSUMING", "green", "bar")
    panel_y, panel_h = 2, height - 6
    if view.fullscreen:
        frame.box(0, panel_y, width, panel_h, "Message detail · Escape returns", True)
        frame.regions.append(("detail", 1, panel_y + 1, width - 2, panel_h - 2))
        draw_detail(frame, state, controller, view, 1, panel_y + 2, width - 2, panel_h - 4)
    else:
        left, middle = frame.panel_widths = panel_widths(width, view.panel_widths)
        right_x, right_w = left + middle + 2, width - left - middle - 2
        for pane, x, w, title in ((0, 0, left, "1 Connections"), (1, left + 1, middle, "2 Queues"), (2, right_x, right_w, "3 Messages")):
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
        frame.text(2, panel_y + panel_h - 3, f"{sum(s.status == 'connected' for s in controller.connections)} connected", "muted", width=left - 4)
        qx = left + 3
        if view.filters[1]:
            frame.text(qx, panel_y + 1, "/ " + view.filters[1], "accent", width=middle - 4)
        for column, title, x in (("name", "Queue", qx), ("depth", "Depth", qx + middle - 10)):
            active = view.queue_sort == column
            arrow = "↓" if view.queue_sort_descending else "↑"
            label = title + (" " + arrow if active else "")
            frame.text(x, panel_y + 2, label, "accent" if active else "muted", width=7)
            frame.actions.append(("queue_sort", column, x, panel_y + 2, 7))
        indices = queue_indices(state.queues, view)
        visible, start = page(indices, state.queue_index, max(1, panel_h - 7))
        for offset, index in enumerate(visible):
            item, y = state.queues[index], panel_y + 4 + offset
            selected = index == state.queue_index
            focused = controller.pane == 1
            bg = "selected" if selected and focused else "bar" if selected else "bg"
            role = "accent" if selected and focused else "muted" if selected else "text"
            frame.row(qx, y, middle - 4, "", selected, focused=focused)
            frame.text(qx, y, ("> " if selected and focused else "  ") + clipped(item.name, middle - 12), role, bg, middle - 10)
            depth = "..." if state.all_depths_pending or item.name in state.depth_pending else str(item.depth)
            frame.text(qx + middle - 9, y, f"{depth:>5}", role, bg, 5)
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
            if selected and focused:
                frame.text(mx - 1, y, ">", "accent")
            x = mx
            values = (short_id(message.message_id, column_widths[0]), message.type, str(message.priority), format_size(message.size), " ".join(message.body.split()))
            for value, column_width in zip(values, column_widths):
                frame.text(x, y, value, role, bg, column_width)
                x += column_width + 2
            frame.row_hits[("messages", y)] = index
        frame.line(mx, divider, available)
        frame.text(mx + 1, divider, f" {min(start + 1, len(indices))}-{min(start + rows, len(indices))} / {len(indices)} ", "muted")
        if state.messages and not indices:
            frame.text(mx + 1, detail_y + 1, "No messages match the filter. Escape clears it.", "muted", width=available - 2)
        else:
            draw_detail(frame, state, controller, view, mx, detail_y, available, detail_height)
        frame.regions.insert(0, ("detail", mx, detail_y, available, detail_height))
        for index, divider_x in enumerate((left, right_x - 1)):
            frame.dividers.append((index, divider_x - 1, panel_y, 3, panel_h))
            frame.text(divider_x, panel_y + panel_h // 2, "↔", "accent", "selected" if view.drag_divider == index else "bar")
    status = state.error or controller.notice
    frame.text(1, height - 3, status, "error" if state.error else "muted", width=width - 2)
    frame.fill(0, height - 1, width, bg="bar")
    if view.prompt is not None:
        footer = f"Filter: {view.prompt}_   Enter apply · Escape cancel"
    elif view.consumer_inspector:
        footer = "c refresh consumers  ↑↓ select/scroll  Enter details  Tab controls  Esc back  q quit"
    elif view.fullscreen:
        footer = "Tab/Shift+Tab/click tabs  ↑↓/PgUp/PgDn scroll  n compose  c consumers  r refresh  Esc back  q quit"
        if controller.detail_tab == 0:
            footer = "Tab/click tabs  ↑↓/PgUp/PgDn scroll  w wrap  s color  n compose  c consumers  r refresh  Esc back  q quit"
    else:
        footer = "Tab/click panels  ↑↓ select  Enter connect/browse/detail  n compose  c consumers  r refresh  h tabs  / filter  q quit"
    frame.text(1, height - 1, footer, "accent", "bar", width - 2)
    if view.consumer_inspector:
        draw_consumer_inspector(frame, controller, view)
    if view.composer is not None:
        from .composer_screen import draw_composer
        draw_composer(frame, view.composer)
    return frame
