from __future__ import annotations

from contextlib import suppress
import curses
import locale
import time

from .body_view import body_layout
from .clipboard import copy_text
from .screen import MIN_PANEL_WIDTHS, PALETTE, ViewOptions, detail_identity, filtered_indices, panel_widths, queue_indices, render


def navigation_key(key):
    keys = {curses.KEY_UP: "UP", curses.KEY_DOWN: "DOWN", curses.KEY_LEFT: "LEFT", curses.KEY_RIGHT: "RIGHT",
            curses.KEY_PPAGE: "PGUP", curses.KEY_NPAGE: "PGDOWN", curses.KEY_HOME: "HOME", curses.KEY_END: "END",
            curses.KEY_BTAB: "BACKTAB", curses.KEY_BACKSPACE: "BACKSPACE", curses.KEY_ENTER: "ENTER",
            curses.KEY_RESIZE: "RESIZE", curses.KEY_MOUSE: "MOUSE", "\t": "TAB", "\n": "ENTER", "\r": "ENTER",
            curses.KEY_DC: "DELETE", curses.KEY_F2: "F2", curses.KEY_F3: "F3", curses.KEY_F4: "F4", curses.KEY_F5: "F5", curses.KEY_F6: "F6",
            "\x1b": "ESC", "\x7f": "BACKSPACE", "\b": "BACKSPACE", "\x03": "QUIT"}
    return keys.get(key, key)


class TerminalApp:
    def __init__(self, controller):
        self.controller = controller
        self.view = ViewOptions()
        self.running = True
        self.last_frame = None
        self.pending_high_surrogate = None
        self.drag_start = None
        self.pending_copy = None

    def copy_feedback(self, identity, text, error=False, pending=False):
        view, now = self.view, time.monotonic()
        view.copy_identity, view.copy_feedback, view.copy_error = identity, text, error
        view.copy_until = float("inf") if pending else now + (5 if error else 1.5)
        view.copy_flash_until = now + 0.3 if not error and not pending else 0

    def finish_copy(self, identity, message):
        try:
            if message.copy_requires_fetch(self.controller.detail_tab):
                raise ValueError("The full content could not be read; nothing was copied.")
            copy_text(message.copy_text(self.controller.detail_tab))
        except Exception as error:
            self.copy_feedback(identity, "Copy failed: " + str(error), error=True)
        else:
            self.copy_feedback(identity, "Copied to clipboard")

    def copy_detail(self, identity):
        if identity != detail_identity(self.controller):
            return
        if self.pending_copy:
            # Repeated clicks coalesce; a new selection cancels the old intent.
            if self.pending_copy[0] == identity:
                return
            self.pending_copy[1].cancel()
            self.pending_copy = None
        message = self.controller.current.selected_message
        if message.copy_requires_fetch(self.controller.detail_tab):
            try:
                future = self.controller.full_message(message)
            except Exception as error:
                self.copy_feedback(identity, "Copy failed: " + str(error), error=True)
            else:
                self.pending_copy = identity, future
                self.copy_feedback(identity, "Reading full content to copy…", pending=True)
        else:
            self.finish_copy(identity, message)

    def poll_copy(self):
        if not self.pending_copy:
            return
        identity, future = self.pending_copy
        if (identity != detail_identity(self.controller) or self.view.consumer_inspector
                or self.view.composer is not None or self.view.prompt is not None or not self.running):
            future.cancel()
            self.pending_copy = None
            self.view.copy_until = self.view.copy_flash_until = 0
        elif future.done():
            self.pending_copy = None
            try:
                message = future.result()
            except Exception as error:
                self.copy_feedback(identity, "Copy failed: " + str(error), error=True)
            else:
                self.finish_copy(identity, message)

    def visible(self):
        controller = self.controller
        items, selected, key = (
            (controller.connections, controller.connection_index, lambda s: s.config.name),
            (controller.current.queues, controller.current.queue_index, lambda q: q.name),
            (controller.current.messages, controller.current.message_index, lambda m: f"{m.message_id} {m.type} {m.priority} {m.body}"),
        )[controller.pane]
        if controller.pane == 1:
            return queue_indices(items, self.view), selected
        return filtered_indices(items, self.view.filters[controller.pane], key), selected

    def select(self, index):
        controller = self.controller
        (controller.select_connection, controller.select_queue, controller.select_message)[controller.pane](index)
        self.view.horizontal_scroll = 0

    def move(self, step=0, end=None):
        indices, selected = self.visible()
        if not indices:
            return
        position = indices.index(selected) if selected in indices else 0
        position = end if end is not None else position + step
        self.select(indices[max(0, min(position, len(indices) - 1))])

    def scroll_detail(self, delta):
        message = self.controller.current.selected_message
        if not message:
            return
        rows = self.last_frame.detail_height if self.last_frame else 10
        if self.controller.detail_tab == 0:
            detail = next((region for region in self.last_frame.regions if region[0] == "detail"), None) if self.last_frame else None
            columns = (detail[3] if detail else 70) - 8
            count = len(body_layout(message.body, message.type, message.truncated, columns,
                                    self.view.fullscreen or self.view.body_wrap).rows)
        else:
            count = len(message.metadata(self.controller.detail_tab))
        maximum = max(0, count - rows)
        current = min(maximum, self.controller.detail_scroll)
        self.controller.detail_scroll = max(0, min(maximum, current + delta))

    def toggle_body(self, option):
        if self.view.fullscreen:
            return
        setattr(self.view, option, not getattr(self.view, option))
        if option == "body_wrap":
            self.controller.detail_scroll = 0
            self.view.horizontal_scroll = 0

    def stop_drag(self, restore=False):
        if restore and self.drag_start:
            self.view.panel_widths = self.drag_start[2]
        self.drag_start = None
        self.view.drag_divider = None

    def resize_panels(self, x):
        if not self.drag_start or not self.last_frame:
            return
        start, widths, _ = self.drag_start
        left, middle = widths
        delta = x - start
        if self.view.drag_divider == 0:
            # Move only this divider: trade width between the adjacent panels.
            delta = max(MIN_PANEL_WIDTHS[0] - left, min(delta, middle - MIN_PANEL_WIDTHS[1]))
            requested = left + delta, middle - delta
        else:
            requested = left, middle + delta
        self.view.panel_widths = panel_widths(self.last_frame.width, requested)
        self.controller.detail_scroll = 0

    def divider_mouse(self, x, y, buttons):
        if self.view.drag_divider is not None:
            # PDCurses can return zero bstate for a motion event. A drag already
            # owns the pointer, so those events must still resize the panels.
            self.resize_panels(x)
            if buttons & (curses.BUTTON1_RELEASED | curses.BUTTON1_CLICKED):
                self.stop_drag()
            return True
        divider = self.last_frame.divider_at(x, y)
        if divider is None:
            return False
        if buttons & curses.BUTTON1_DOUBLE_CLICKED:
            self.view.panel_widths = None
            self.controller.detail_scroll = 0
        elif buttons & curses.BUTTON1_PRESSED:
            self.view.drag_divider = divider
            self.drag_start = x, self.last_frame.panel_widths, self.view.panel_widths
        # Divider events never focus a panel or activate an underlying row.
        return True

    def select_detail_tab(self, index):
        self.controller.detail_tab = index % 3
        self.controller.detail_scroll = 0
        self.view.horizontal_scroll = 0

    def open_consumers(self):
        if self.controller.refresh_consumers():
            self.view.consumer_inspector = True
            self.view.consumer_detail = False
            self.view.consumer_focus = self.view.consumer_scroll = self.view.consumer_horizontal = 0

    def open_composer(self):
        state = self.controller.current
        if state.status != "connected" or state.selected_queue is None:
            self.controller.notice = "Select a connected server and queue to compose a message."
            return
        from .composer import Composer
        from .outgoing import destination
        self.view.composer = Composer(destination(state.config, state.selected_queue.name), self.controller.connection_index, state.epoch)

    def move_consumer(self, step=0, end=None):
        state = self.controller.current
        count = len(state.consumer_snapshot.consumers) if state.consumer_snapshot else 0
        state.consumer_index = max(0, min(count - 1, end if end is not None else state.consumer_index + step))
        self.view.consumer_scroll = self.view.consumer_horizontal = 0

    def scroll_consumer(self, step):
        state = self.controller.current
        snapshot = state.consumer_snapshot
        if snapshot and snapshot.consumers:
            rows = self.last_frame.consumer_detail_height if self.last_frame else 10
            maximum = max(0, len(snapshot.consumers[state.consumer_index].rows()) - rows)
            self.view.consumer_scroll = max(0, min(maximum, self.view.consumer_scroll + step))

    def consumer_key(self, key):
        view, state = self.view, self.controller.current
        if key in ("q", "QUIT"):
            self.running = False
        elif key == "ESC":
            if view.consumer_detail:
                view.consumer_detail = False
            else:
                view.consumer_inspector = False
        elif key in ("TAB", "BACKTAB"):
            view.consumer_focus = (view.consumer_focus + (1 if key == "TAB" else -1)) % 3
        elif key == "c":
            self.controller.refresh_consumers()
            view.consumer_scroll = 0
        elif key == "ENTER":
            if view.consumer_focus == 1:
                self.controller.refresh_consumers()
            elif view.consumer_focus == 2:
                view.consumer_inspector = False
            elif state.consumer_status == "ready" and state.consumer_snapshot and state.consumer_snapshot.consumers:
                view.consumer_detail = not view.consumer_detail
                view.consumer_scroll = view.consumer_horizontal = 0
        elif key in ("UP", "DOWN", "PGUP", "PGDOWN"):
            step = -1 if key in ("UP", "PGUP") else 1
            if key in ("PGUP", "PGDOWN"):
                step *= (self.last_frame.consumer_detail_height if view.consumer_detail else self.last_frame.consumer_rows) if self.last_frame else 5
            self.scroll_consumer(step) if view.consumer_detail else self.move_consumer(step)
            view.consumer_focus = 0
        elif key in ("HOME", "END"):
            if view.consumer_detail:
                self.scroll_consumer(-10**9 if key == "HOME" else 10**9)
            else:
                self.move_consumer(end=0 if key == "HOME" else 10**9)
            view.consumer_focus = 0
        elif key in ("[", "]"):
            view.consumer_horizontal = max(0, view.consumer_horizontal + (-8 if key == "[" else 8))

    def handle_key(self, key):
        # Windows curses may deliver a non-BMP character as two UTF-16 units.
        if isinstance(key, str) and len(key) == 1:
            value = ord(key)
            if 0xD800 <= value <= 0xDBFF:
                self.pending_high_surrogate = value
                return
            if 0xDC00 <= value <= 0xDFFF and self.pending_high_surrogate is not None:
                key = chr(0x10000 + ((self.pending_high_surrogate - 0xD800) << 10) + value - 0xDC00)
        self.pending_high_surrogate = None
        key = navigation_key(key)
        c, view = self.controller, self.view
        if view.drag_divider is not None:
            self.stop_drag(restore=key == "ESC")
            if key == "ESC":
                return
        if view.composer is not None:
            if view.composer.handle_key(key, c):
                view.composer = None
            return
        if view.prompt is not None:
            if key == "ESC":
                view.prompt = None
            elif key == "ENTER":
                view.filters[c.pane] = view.prompt
                view.prompt = None
                indices, selected = self.visible()
                if indices and selected not in indices:
                    self.select(indices[0])
            elif key == "BACKSPACE":
                view.prompt = view.prompt[:-1]
            elif isinstance(key, str) and len(key) == 1 and key.isprintable() and len(view.prompt) < 256:
                view.prompt += key
            return
        if view.consumer_inspector:
            self.consumer_key(key)
            return
        if key in ("q", "QUIT"):
            self.running = False
        elif key in ("TAB", "BACKTAB"):
            step = 1 if key == "TAB" else -1
            if view.fullscreen:
                self.select_detail_tab(c.detail_tab + step)
            else:
                c.pane = (c.pane + step) % 3
        elif key in ("UP", "DOWN"):
            if view.fullscreen:
                self.scroll_detail(1 if key == "DOWN" else -1)
            else:
                self.move(1 if key == "DOWN" else -1)
        elif key in ("HOME", "END"):
            indices, _ = self.visible()
            self.move(end=0 if key == "HOME" else len(indices) - 1)
        elif key == "ENTER":
            if c.pane == 0:
                c.connect()
            elif c.pane == 1:
                c.refresh_queue_depth()
                c.browse_queue()
            elif c.current.selected_message:
                view.fullscreen = not view.fullscreen
        elif key == "d":
            c.disconnect()
        elif key == "c":
            self.open_consumers()
        elif key == "n":
            self.open_composer()
        elif key == "r":
            c.refresh_queue_depths() if c.pane == 1 else c.refresh()
        elif key == "h":
            self.select_detail_tab(c.detail_tab + 1)
        elif key in ("w", "s") and c.pane == 2 and c.detail_tab == 0 and not view.fullscreen:
            self.toggle_body("body_wrap" if key == "w" else "body_colors")
        elif key == "/":
            view.prompt = view.filters[c.pane]
        elif key == "ESC":
            if view.fullscreen:
                view.fullscreen = False
            else:
                view.filters[c.pane] = ""
        elif key in ("PGUP", "PGDOWN"):
            rows = self.last_frame.detail_height if self.last_frame else 10
            self.scroll_detail(-rows if key == "PGUP" else rows)
        elif key in ("[", "]"):
            if c.detail_tab != 0 or not (view.fullscreen or view.body_wrap):
                view.horizontal_scroll = max(0, view.horizontal_scroll + (-8 if key == "[" else 8))

    def handle_mouse(self, x, y, buttons):
        if self.last_frame is None or self.view.prompt is not None:
            return
        click = buttons & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED | curses.BUTTON1_DOUBLE_CLICKED)
        if self.view.composer is not None:
            wheel = -3 if buttons & getattr(curses, "BUTTON4_PRESSED", 0) else 3 if buttons & getattr(curses, "BUTTON5_PRESSED", 0) else 0
            # Actions fire once per completed click, including the explicit Send button.
            completed_click = buttons & curses.BUTTON1_CLICKED
            if self.view.composer.handle_mouse(self.last_frame, x, y, completed_click, wheel, self.controller):
                self.view.composer = None
            return
        if self.view.consumer_inspector:
            action, _ = self.last_frame.action_at(x, y)
            if click and action == "refresh_consumers":
                self.view.consumer_focus = 1
                self.controller.refresh_consumers()
                self.view.consumer_scroll = 0
            elif click and action == "close_consumers":
                self.view.consumer_inspector = False
            else:
                name, index = self.last_frame.hit(x, y)
                if name not in ("consumers", "consumer_detail"):
                    return
                self.view.consumer_focus = 0
                step = -3 if buttons & getattr(curses, "BUTTON4_PRESSED", 0) else 3 if buttons & getattr(curses, "BUTTON5_PRESSED", 0) else 0
                if step:
                    self.scroll_consumer(step) if name == "consumer_detail" else self.move_consumer(step)
                elif click and index is not None:
                    self.move_consumer(end=index)
                    if buttons & curses.BUTTON1_DOUBLE_CLICKED:
                        self.consumer_key("ENTER")
            return
        if self.divider_mouse(x, y, buttons):
            return
        if click:
            action, value = self.last_frame.action_at(x, y)
            if action == "queue_sort":
                self.controller.pane = 1
                if buttons & (curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED):
                    view = self.view
                    view.queue_sort_descending = not view.queue_sort_descending if view.queue_sort == value else False
                    view.queue_sort = value
                return
            if action == "detail_tab":
                self.controller.pane = 2
                self.select_detail_tab(value)
                return
            if action == "copy_detail":
                self.controller.pane = 2
                if buttons & (curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED):
                    self.copy_detail(value)
                return
            if action in ("body_wrap", "body_colors"):
                if buttons & (curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED):
                    self.controller.pane = 2
                    self.toggle_body(action)
                return
        wheel = buttons & (getattr(curses, "BUTTON4_PRESSED", 0) | getattr(curses, "BUTTON5_PRESSED", 0))
        if not click and not wheel:
            return
        name, index = self.last_frame.hit(x, y)
        panes = {"connections": 0, "queues": 1, "messages": 2, "detail": 2}
        if name not in panes:
            return
        self.controller.pane = panes[name]
        if buttons & getattr(curses, "BUTTON4_PRESSED", 0):
            if name == "detail":
                self.scroll_detail(-3)
            else:
                self.move(-1)
        elif buttons & getattr(curses, "BUTTON5_PRESSED", 0):
            if name == "detail":
                self.scroll_detail(3)
            else:
                self.move(1)
        elif click:
            if index is not None:
                self.select(index)
            if buttons & curses.BUTTON1_DOUBLE_CLICKED:
                self.handle_key("ENTER")

    def loop(self, window):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        window.keypad(True)
        window.timeout(100)
        if self.controller.config.ui.mouse:
            curses.mousemask(curses.ALL_MOUSE_EVENTS | getattr(curses, "REPORT_MOUSE_POSITION", 0))
            curses.mouseinterval(150)
        colors = initialize_colors()
        next_refresh = time.monotonic() + self.controller.config.ui.refresh_seconds
        while self.running:
            self.controller.poll()
            self.poll_copy()
            refresh = self.controller.config.ui.refresh_seconds
            if refresh and time.monotonic() >= next_refresh:
                if self.controller.current.status == "connected" and not self.controller.current.busy and not self.view.consumer_inspector and self.view.composer is None:
                    self.controller.refresh_queue_depths() if self.controller.pane == 1 else self.controller.refresh()
                next_refresh = time.monotonic() + refresh
            height, width = window.getmaxyx()
            self.last_frame = render(self.controller, width, height, self.view)
            draw_frame(window, self.last_frame, colors)
            try:
                key = window.get_wch()
            except curses.error:
                continue
            if navigation_key(key) == "MOUSE":
                try:
                    _, x, y, _, buttons = curses.getmouse()
                    self.handle_mouse(x, y, buttons)
                except curses.error:
                    pass
            else:
                self.handle_key(key)


def initialize_colors():
    if not curses.has_colors():
        return {}
    curses.start_color()
    result = {}
    basic = {"bg": curses.COLOR_BLACK, "text": curses.COLOR_WHITE, "muted": curses.COLOR_WHITE,
             "border": curses.COLOR_GREEN, "accent": curses.COLOR_YELLOW, "selected": curses.COLOR_GREEN,
             "green": curses.COLOR_GREEN, "error": curses.COLOR_RED, "bar": curses.COLOR_BLACK}

    def color(role):
        if curses.COLORS < 256:
            return basic[role]
        value = PALETTE[role]
        rgb = [int(value[index:index + 2], 16) for index in (1, 3, 5)]
        levels = [0, 95, 135, 175, 215, 255]
        palette = [(16 + 36 * r + 6 * g + b, (levels[r], levels[g], levels[b]))
                   for r in range(6) for g in range(6) for b in range(6)]
        palette += [(232 + i, (8 + 10 * i,) * 3) for i in range(24)]
        return min(palette, key=lambda item: sum((a - b) ** 2 for a, b in zip(rgb, item[1])))[0]

    for bg in ("bg", "selected", "bar", "accent"):
        for fg in PALETTE:
            number = len(result) + 1
            if number >= curses.COLOR_PAIRS:
                return result
            curses.init_pair(number, color(fg), color(bg))
            result[fg, bg] = curses.color_pair(number)
    return result


def draw_frame(window, frame, colors):
    for y, row in enumerate(frame.cells):
        x = 0
        while x < frame.width:
            _, role, bg = row[x]
            end = x + 1
            while end < frame.width and row[end][1:] == (role, bg):
                end += 1
            text = "".join(cell[0] for cell in row[x:end])
            attribute = colors.get((role, bg), curses.A_BOLD if role in ("accent", "green", "error") else curses.A_NORMAL)
            if not colors and bg in ("selected", "accent"):
                attribute |= curses.A_REVERSE
            try:
                window.addstr(y, x, text, attribute)
            except curses.error:
                pass  # Writing the terminal's bottom-right cell reports ERR on ncurses.
            x = end
    window.noutrefresh()
    curses.doupdate()


def run(controller):
    def run_screen(window):
        try:
            TerminalApp(controller).loop(window)
        finally:
            # PDCurses may leave the last frame in PowerShell's screen buffer.
            # Erase it while curses still owns the terminal, before endwin().
            with suppress(curses.error):
                curses.mousemask(0)
            with suppress(curses.error):
                curses.curs_set(1)
            with suppress(curses.error):
                window.bkgd(" ", curses.A_NORMAL)
                window.attrset(curses.A_NORMAL)
            with suppress(curses.error):
                window.clear()
                window.refresh()

    try:
        locale.setlocale(locale.LC_ALL, "")
        curses.wrapper(run_screen)
    finally:
        controller.close()
