"""Composer layout for the shared curses / PNG character grid."""
from __future__ import annotations

import textwrap

from wcwidth import wcwidth, wcswidth

from .domain import format_size
from .screen import safe_text


def cell_slice(value, start):
    used, index = 0, 0
    while index < len(value) and used < start:
        used += max(0, wcwidth(value[index]))
        index += 1
    return value[index:]


def editor(frame, composer, name, buffer, x, y, width, height=1, multiline=False):
    active = composer.focus == name and composer.prompt is None
    lines = buffer.text.split("\n") if multiline else [buffer.text]
    row = buffer.text[:buffer.cursor].count("\n") if multiline else 0
    column = buffer.cursor - (buffer.text.rfind("\n", 0, buffer.cursor) + 1) if multiline else buffer.cursor
    caret = wcswidth(safe_text(lines[row][:column]))
    if active or name == "path":
        buffer.scroll_row = max(0, min(buffer.scroll_row, row))
        if row >= buffer.scroll_row + height:
            buffer.scroll_row = row - height + 1
        buffer.scroll_col = max(0, min(buffer.scroll_col, caret))
        if caret >= buffer.scroll_col + width - 1:
            buffer.scroll_col = max(0, caret - width + 2)
    frame.fill(x, y, width, height, bg="bar")
    for offset, line in enumerate(lines[buffer.scroll_row:buffer.scroll_row + height]):
        frame.text(x, y + offset, cell_slice(safe_text(line), buffer.scroll_col), bg="bar", width=width)
    if active or name == "path":
        cursor_x, cursor_y = x + caret - buffer.scroll_col, y + row - buffer.scroll_row
        char = safe_text(lines[row][column:column + 1]) or " "
        if x <= cursor_x < x + width and y <= cursor_y < y + height:
            frame.text(cursor_x, cursor_y, char, "accent", "selected", min(2, x + width - cursor_x))
    frame.composer_hits.append((name, x, y, width, height))


def button(frame, composer, action, text, x, y, width=None):
    label = f" {text} "
    width = len(label) if width is None else width
    bg = "selected" if composer.focus == action else "bg"
    frame.fill(x, y, width, bg=bg)
    frame.text(x, y, label, "accent", bg, width)
    frame.composer_hits.append((action, x, y, width, 1))


def preview_lines(composer):
    target, draft = composer.target, composer.prepared
    if composer.stage == "sent":
        receipt = composer.job.receipt
        return ["Send completed.", "",
                f"Server: {target['name']}", f"Queue: {target['queue']}",
                "MessageId: " + str(receipt.get("JMSMessageID") or "unavailable"),
                f"JMSTimestamp: {receipt.get('JMSTimestamp')}", f"JMSExpiration: {receipt.get('JMSExpiration')}",
                "Size: " + format_size(receipt["size"]), "", "The queue depth and message list are being refreshed."]
    lines = [f"Server: {target['name']}", f"Endpoint: {target['host']}:{target['port']}   {target['queue_manager']}   {target['channel']}",
             f"Queue: {target['queue']}", "",
             f"TextMessage · {format_size(len(draft.body.encode()))} · priority {draft.priority} · "
             + ("persistent" if draft.persistent else "non-persistent") + f" · TTL {draft.ttl_ms} ms" + (" (no expiry)" if not draft.ttl_ms else ""),
             "JMSType: " + (draft.jms_type or "none"), "JMSCorrelationID: " + (draft.correlation_id or "none"),
             "MessageId and timestamp: assigned by provider when sent.", "", f"Properties ({len(draft.properties)}):"]
    lines += [f"  {item.name}  [{item.kind}]  {item.value}" for item in draft.properties] or ["  none"]
    lines += ["", "Body:", *draft.body.split("\n")]
    return lines


def draw_composer(frame, composer):
    composer.sync()
    frame.composer_hits = []
    frame.actions, frame.regions, frame.row_hits = [], [], {}
    frame.dividers = []
    x, y, width, height = 4, 3, frame.width - 8, frame.height - 7
    left, inner = x + 3, width - 6
    title = {"edit": "New TextMessage", "preview": "Review message · Send is explicit", "sending": "Sending one message…",
             "sent": "Message sent", "error": "Send result"}[composer.stage]
    frame.fill(x, y, width, height)
    frame.box(x, y, width, height, title, True)
    frame.close_button(x, y, width, "close_composer")
    frame.fill(frame.width - 15, 0, 15, bg="bar")
    frame.text(frame.width - 15, 0, "COMPOSER", "accent", "bar", 15)
    if composer.stage == "edit":
        frame.text(left, y + 2, f"Destination: {composer.target['name']} / {composer.target['queue']}", "accent", width=inner)
        frame.text(left, y + 3, f"{composer.target['type'].upper()} · {composer.target['host']}:{composer.target['port']}  {composer.target['queue_manager']}  {composer.target['channel']}", "muted", width=inner)
        frame.text(left, y + 4, "TextMessage  Prio", width=17)
        editor(frame, composer, "priority", composer.fields["priority"], left + 18, y + 4, 3)
        frame.text(left + 24, y + 4, "Delivery", width=8)
        button(frame, composer, "persistent", "Persistent" if composer.persistent else "Non-persistent", left + 33, y + 4, 16)
        frame.text(left + 52, y + 4, "TTL (ms)", width=8)
        editor(frame, composer, "ttl", composer.fields["ttl"], left + 61, y + 4, min(20, inner - 61))
        meta_width = max(40, int(inner * 0.38))
        body_width, meta_x = inner - meta_width - 4, left + inner - meta_width
        top, bottom = y + 7, y + height - 7
        rows = bottom - top + 1
        composer.editor_rows = rows
        frame.text(left, y + 6, "Body", "accent")
        button(frame, composer, "load_body", "Load file", left + 7, y + 6)
        size = format_size(len(composer.fields["body"].text.encode("utf-8", errors="replace")))
        frame.text(left + body_width - len(size), y + 6, size, "muted", width=len(size))
        frame.fill(left, top, body_width, rows, bg="bar")
        body = composer.fields["body"]
        editor(frame, composer, "body", body, left + 5, top, body_width - 5, rows, True)
        line_count = body.text.count("\n") + 1
        for offset in range(rows):
            if body.scroll_row + offset < line_count:
                frame.text(left, top + offset, f"{body.scroll_row + offset + 1:>4}", "muted", "bar", 4)
        frame.text(meta_x, y + 6, "Headers", "accent", width=meta_width)
        frame.text(meta_x, y + 7, "JMSType", "muted", width=meta_width)
        editor(frame, composer, "jms_type", composer.fields["jms_type"], meta_x, y + 8, meta_width)
        frame.text(meta_x, y + 9, "JMSCorrelationID", "muted", width=meta_width)
        editor(frame, composer, "correlation_id", composer.fields["correlation_id"], meta_x, y + 10, meta_width)
        frame.text(meta_x, y + 12, "Properties", "accent", width=meta_width)
        name_width, kind_width, value_width = max(9, meta_width // 4), 10, meta_width - max(9, meta_width // 4) - 15
        offsets = (meta_x, meta_x + name_width + 1, meta_x + name_width + kind_width + 2)
        for label, col, span in zip(("Name", "Type", "Value"), offsets, (name_width, kind_width, value_width)):
            frame.text(col, y + 13, label, "muted", width=span)
        count = max(1, bottom - (y + 14) + 1)
        selected = int(composer.focus.split(":")[1]) if composer.focus.startswith("p:") else composer.property_start
        composer.property_start = max(0, min(composer.property_start, selected))
        if selected >= composer.property_start + count:
            composer.property_start = selected - count + 1
        for offset, index in enumerate(range(composer.property_start, min(len(composer.properties), composer.property_start + count))):
            item, row = composer.properties[index], y + 14 + offset
            editor(frame, composer, f"p:{index}:name", item.name, offsets[0], row, name_width)
            button(frame, composer, f"p:{index}:kind", item.kind, offsets[1], row, kind_width)
            editor(frame, composer, f"p:{index}:value", item.value, offsets[2], row, value_width)
            button(frame, composer, f"p:{index}:remove", "-", meta_x + meta_width - 3, row, 3)
        frame.composer_hits.append(("property_list", meta_x, y + 14, meta_width, count))
        button(frame, composer, "add_property", "+ Property", meta_x, bottom + 1)
        frame.text(meta_x + 14, bottom + 1, f"{len(composer.properties)} rows", "muted", width=meta_width - 14)
        for action, label, col in (("load_draft", "Load draft", left), ("save_draft", "Save draft", left + 16),
                                    ("review", "Preview send", x + width - 35), ("cancel", "Cancel", x + width - 17)):
            button(frame, composer, action, label, col, y + height - 3)
    else:
        lines = []
        if composer.stage == "error":
            lines += ["Send not confirmed: " + composer.error,
                      "Delivery is unknown. Inspect the queue before sending again." if composer.job.outcome == "unknown" else "Message was not sent.", ""]
        if composer.stage == "sending":
            lines += ["Sending one message. Waiting for the provider result…", ""]
        for line in preview_lines(composer):
            lines += textwrap.wrap(safe_text(line), width=inner, replace_whitespace=False, drop_whitespace=False) or [""]
        rows = height - 7
        composer.preview_scroll = min(composer.preview_scroll, max(0, len(lines) - rows))
        for offset, line in enumerate(lines[composer.preview_scroll:composer.preview_scroll + rows]):
            frame.text(left, y + 2 + offset, line, "text", width=inner)
        frame.composer_hits.append(("preview_body", left, y + 2, inner, rows))
        if composer.stage == "preview":
            for action, label, col in (("edit", "Edit", left), ("save_draft", "Save draft", left + 12),
                                        ("cancel", "Cancel", left + 29), ("send", "Send 1 message", x + width - 21)):
                button(frame, composer, action, label, col, y + height - 3)
        elif composer.stage == "sent":
            button(frame, composer, "new", "New message", left, y + height - 3)
            button(frame, composer, "close", "Close", x + width - 17, y + height - 3)
        elif composer.stage == "error":
            if composer.job.outcome == "not_sent":
                button(frame, composer, "edit", "Edit", left, y + height - 3)
            button(frame, composer, "save_draft", "Save draft", left + 12, y + height - 3)
            button(frame, composer, "close", "Close", x + width - 17, y + height - 3)
    notice = composer.notice
    if composer.stage in ("preview", "sending", "sent", "error") and not notice:
        notice = f"Review lines {composer.preview_scroll + 1}-{min(len(lines), composer.preview_scroll + rows)} / {len(lines)}"
    frame.text(left, y + height - 2, composer.error or notice, "error" if composer.error else "muted", width=inner)
    if composer.prompt is not None:
        labels = {"load_body": "Load body file · UTF-8", "load_draft": "Load message draft", "save_draft": "Save message draft · JSON"}
        py, ph = max(y + 2, y + height // 2 - 3), 7
        frame.fill(x + 2, py, width - 4, ph)
        frame.box(x + 2, py, width - 4, ph, labels[composer.prompt_kind], True)
        frame.actions = []
        frame.close_button(x + 2, py, width - 4, "close_prompt")
        frame.composer_hits = []
        frame.text(left + 1, py + 1, "Path (Ctrl+U clears)", "muted", width=inner - 2)
        editor(frame, composer, "path", composer.prompt, left + 1, py + 2, inner - 2)
        frame.text(left + 1, py + 4, composer.error or "Enter applies · Escape returns", "error" if composer.error else "muted", width=inner - 2)
