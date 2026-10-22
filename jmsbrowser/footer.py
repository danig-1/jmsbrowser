"""Context-sensitive footer buttons shared by every application screen."""
from wcwidth import wcswidth

from .screen import filtered_indices


def footer_options(controller, view):
    state = controller.current
    quit_button = ("q quit", "QUIT")
    arrows = [("↑", "UP"), ("↓", "DOWN")]
    if view.export_dialog is not None:
        dialog = view.export_dialog
        if dialog.working:
            return [], [quit_button], "Exporting messages…"
        if dialog.result:
            return [("Enter close", "ESC")], [quit_button], ""
        buttons = [("Enter export", "EXPORT")]
        if dialog.focus == 0:
            buttons += [("^U clear", "\x15")]
        return buttons, [("Esc cancel", "ESC"), ("^C quit", "QUIT")], ""
    if view.composer is not None:
        composer = view.composer
        if composer.prompt is not None:
            return [("Enter apply", "ENTER"), ("^U clear", "\x15")], [("Esc back", "ESC"), ("^C quit", "QUIT")], ""
        if composer.stage == "sending":
            return [], [quit_button], "Sending one message…"
        if composer.stage == "edit":
            return [("F2 review", "F2"), ("F3 body file", "F3"), ("F4 save draft", "F4"),
                    ("F5 load draft", "F5"), ("F6 property", "F6")], [("Esc cancel", "ESC"), ("^C quit", "QUIT")], ""
        if composer.stage == "preview":
            return [("Edit", "COMPOSER_EDIT"), ("F4 save draft", "F4"), ("Send 1 message", "COMPOSER_SEND")], [("Esc back", "ESC"), quit_button], ""
        buttons = [("New message", "COMPOSER_NEW")] if composer.stage == "sent" else [("F4 save draft", "F4")]
        if composer.stage == "error" and composer.job and composer.job.outcome == "not_sent":
            buttons.insert(0, ("Edit", "COMPOSER_EDIT"))
        return buttons, [("Esc close", "ESC"), quit_button], ""
    if view.prompt is not None:
        return [("Enter apply", "ENTER")], [("Esc cancel", "ESC"), ("^C quit", "QUIT")], "Filter: " + view.prompt + "▏"
    if view.consumer_inspector:
        available = state.consumer_status == "ready" and state.consumer_snapshot and state.consumer_snapshot.consumers
        buttons = arrows if available else []
        if available:
            buttons += [("Enter list" if view.consumer_detail else "Enter details", "ENTER")]
        buttons += [("c refresh consumers", "c")]
        return buttons, [("Esc back", "ESC"), quit_button], ""
    search = view.search
    if search is not None and search.editing:
        return [("Enter find", "ENTER")], [("Esc cancel", "ESC"), ("^C quit", "QUIT")], ""
    if view.fullscreen:
        buttons = arrows + [("PgUp", "PGUP"), ("PgDn", "PGDOWN"), ("^F find", "\x06")]
        if search and search.matches:
            buttons += [("F3 next", "F3"), ("S-F3 previous", "SHIFT_F3")]
        return buttons, [("Esc close find" if search else "Esc back", "ESC"), quit_button], ""
    connected = state.status == "connected"
    visible_messages = filtered_indices(state.messages, view.filters[2], lambda m: f"{m.message_id} {m.type} {m.priority} {m.body}")
    has_message = state.message_index in visible_messages
    buttons = arrows if (visible_messages if controller.pane == 2 else state.queues if controller.pane == 1 else controller.connections) else []
    if controller.pane == 0:
        if connected:
            buttons += [("d disconnect", "d"), ("r refresh", "r")]
        elif state.status != "connecting":
            buttons += [("Enter connect", "ENTER")]
    elif controller.pane == 1 and connected:
        if state.selected_queue:
            buttons += [("Enter browse", "ENTER"), ("c consumers", "c"), ("n compose", "n")]
        buttons += [("r refresh", "r")]
    elif controller.pane == 2 and connected:
        if has_message:
            buttons += [("Enter detail", "ENTER"), ("Space mark", " ")]
        count = len(state.export_selection)
        if count:
            buttons += [(f"e export ({count})", "e")]
        if has_message:
            buttons += [("^F find", "\x06")]
        if search and search.matches:
            buttons += [("F3 next", "F3"), ("S-F3 previous", "SHIFT_F3")]
        buttons += [("r refresh", "r")]
    buttons += [("/ filter", "/")]
    right = []
    if search or view.filters[controller.pane] or (controller.pane == 2 and state.marked_messages):
        right.append(("Esc clear", "ESC"))
    return buttons, right + [quit_button], ""


def draw_footer(frame, controller, view):
    buttons, right, prefix = footer_options(controller, view)
    y = frame.height - 1
    frame.fill(0, y, frame.width, bg="bar")
    frame.actions = [action for action in frame.actions if action[0] != "footer_key"]

    def paint(x, label, key):
        text = f" {label} "
        width = wcswidth(text)
        frame.text(x, y, text, "accent", "selected", width)
        frame.actions.insert(0, ("footer_key", key, x, y, width))
        return x + width

    separator = " | " if frame.ascii else " │ "
    right_width = sum(wcswidth(label) + 2 for label, _ in right) + max(0, len(right) - 1) * 3
    boundary = frame.width - right_width - 1
    x = boundary
    for i, (label, key) in enumerate(right):
        if i:
            frame.text(x, y, separator, "border", "bar", 3)
            x += 3
        x = paint(x, label, key)
    if buttons or prefix:
        frame.text(boundary - 2, y, "|" if frame.ascii else "│", "border", "bar", 1)
    x = 1
    if prefix:
        span = min(max(12, frame.width // 3), max(0, boundary - 25))
        frame.text(x, y, prefix, "text", "bar", span)
        x += span + 2
    for label, key in buttons:
        width = wcswidth(label) + 2
        gap = 3 if x > 1 else 0
        if x + gap + width > boundary - 2:
            continue
        if gap:
            frame.text(x, y, separator, "border", "bar", 3)
            x += 3
        x = paint(x, label, key)
