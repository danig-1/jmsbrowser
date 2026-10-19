"""Render six design proposals using the application's terminal character grid.

All data is illustrative. This script does not connect to brokers, send messages,
or change application behavior. Requires the optional Pillow dependency.
Run: python -m tools.render_design_options
"""
import json
from pathlib import Path
import re
import textwrap

from jmsbrowser.config import Config, ConnectionConfig
from jmsbrowser.domain import Consumer, Message, QueueInfo, format_message_datetime, format_size
from jmsbrowser.screen import HEADERS, Frame, ViewOptions, draw_detail, render, short_id, table_widths
from jmsbrowser.screenshot import save_png
from jmsbrowser.state import Controller

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/designs"
WIDTH, HEIGHT = 160, 46
FOOTER = "↑↓ select  Enter connect/depth/detail  n compose  c consumers  r refresh  h tabs  / filter  q quit"


def sample_messages():
    events = ("order.created", "order.paid", "order.updated", "order.cancelled",
              "order.created", "order.shipped", "order.created", "order.paid")
    messages = []
    for index, event in enumerate(events):
        identity = f"ID:7d2b8c10-a943-11f1-bf40-{1042 + index:012}"
        payload = {"event": event, "order_id": f"ORD-{1042 + index}", "total": 129.90 + index,
                   "currency": "EUR", "region": "EU"}
        if index == 0:
            payload["note"] = ("Deliver to reception during office hours. Call the customer before arrival; "
                               "the entrance is on the north side of the building.")
        body = json.dumps(payload, ensure_ascii=False)
        priority = (4, 8, 4, 9, 2, 4, 1, 8)[index]
        headers = {"JMSMessageID": identity, "JMSType": event, "JMSPriority": priority,
                   "JMSTimestamp": 1791981234567 + index * 1000, "JMSExpiration": 0,
                   "JMSDeliveryMode": 2, "JMSRedelivered": False,
                   "JMSCorrelationID": f"req-{1042 + index}", "JMSDestination": "ActiveMQQueue[DEV.ORDERS]"}
        messages.append(Message(identity, "TextMessage", priority, body, len(body.encode()),
                                headers, {"region": "EU", "source": "web-shop", "retryCount": index % 3}))
    return messages


def controller():
    config = Config((ConnectionConfig("Local Artemis", "artemis"),
                     ConnectionConfig("Local IBM MQ", "ibm", port=1414, queue_manager="QM1")))
    result = Controller(config)
    for state in result.connections:
        state.status = "connected"
        state.queues = [QueueInfo("DEV.EMPTY", 0), QueueInfo("DEV.LOADTEST", 1000),
                        QueueInfo("DEV.NOTIFICATIONS", 1), QueueInfo("DEV.ORDERS", 8),
                        QueueInfo("DEV.PAYMENTS", 2)]
        state.queue_index = 3
    result.current.messages = sample_messages()
    result.pane = 2
    result.notice = "DESIGN PROPOSAL · Illustrative data."
    return result


def chrome(frame, number, title, notice, footer=FOOTER):
    frame.fill(0, 0, WIDTH, bg="bar")
    frame.text(2, 0, "JMS BROWSER", "accent", "bar")
    frame.text(17, 0, f"DESIGN {number} / {title.upper()} / EXAMPLE DATA", "accent", "bar", WIDTH - 34)
    frame.text(WIDTH - 15, 0, "NON-CONSUMING", "green", "bar", 15)
    frame.fill(1, HEIGHT - 3, WIDTH - 2)
    frame.text(1, HEIGHT - 3, notice, "muted", width=WIDTH - 2)
    frame.fill(0, HEIGHT - 1, WIDTH, bg="bar")
    frame.text(1, HEIGHT - 1, footer, "accent", "bar", WIDTH - 2)


def save(frame, name, result):
    save_png(frame, OUTPUT / name)
    result.close()


def message_table(frame, state, x, width, balanced=False):
    frame.fill(x, 4, width, 17)
    columns = [24, 19, 7, width - 56] if balanced else table_widths(width)
    cursor = x
    for index, (label, span) in enumerate(zip(HEADERS, columns)):
        text = label.rjust(span) if balanced and index == 2 else label
        frame.text(cursor, 4, text, "muted", width=span)
        cursor += span + 2
    if balanced:
        frame.line(x, 5, width)
    for index, message in enumerate(state.messages[:15]):
        row = 6 + index
        active = index == state.message_index
        bg, role = ("selected", "accent") if active else ("bg", "text")
        frame.fill(x, row, width, bg=bg)
        values = (short_id(message.message_id, columns[0]),
                  format_message_datetime(message.headers.get("JMSTimestamp")),
                  format_size(message.size), " ".join(message.body.split()))
        cursor = x
        for col, (value, span) in enumerate(zip(values, columns)):
            value = value.rjust(span) if balanced and col == 2 else value
            frame.text(cursor, row, value, role, bg, span)
            cursor += span + 2


def adjustable_panels():
    result = controller()
    state = result.current
    state.queues += [QueueInfo("INT.PAYMENTS.RECONCILIATION.DLQ", 24), QueueInfo("INT.ORDERS.RETRY", 16)]
    frame = Frame(WIDTH, HEIGHT)
    left, middle = 22, 44
    right, right_width = left + middle + 2, WIDTH - left - middle - 2
    for x, span, title, active in ((0, left, "1 Connections", False),
                                  (left + 1, middle, "2 Queues", False),
                                  (right, right_width, "3 Messages", True)):
        frame.box(x, 2, span, 40, title, active)
    for index, item in enumerate(result.connections):
        row, selected = 6 + index * 3, index == 0
        frame.row(2, row, left - 4, f"{'>' if selected else ' '} ● {item.config.name}", selected)
        frame.text(4, row, "●", "green", "selected" if selected else "bg")
        frame.text(4, row + 1, f"{item.config.type.upper()} · {item.config.queue_manager or item.config.port}", "muted", width=left - 6)
    frame.text(2, 39, "2 connected", "muted", width=left - 4)
    qx = left + 3
    frame.text(qx, 4, "Queue", "muted")
    frame.text(qx + middle - 9, 4, "Depth", "muted")
    for index, queue in enumerate(state.queues):
        active = index == state.queue_index
        bg, role = ("selected", "accent") if active else ("bg", "text")
        row = 6 + index
        frame.fill(qx, row, middle - 4, bg=bg)
        frame.text(qx, row, ("> " if active else "  ") + queue.name, role, bg, middle - 10)
        frame.text(qx + middle - 9, row, f"{queue.depth:>5}", role, bg, 5)
    frame.text(qx, 39, "7 queues", "muted")
    message_table(frame, state, right + 2, right_width - 4)
    frame.line(right + 2, 21, right_width - 4)
    frame.text(right + 3, 21, " 1-8 / 8 ", "muted")
    draw_detail(frame, state, result, ViewOptions(), right + 2, 22, right_width - 4, 19)
    # The handle illustrates a proposed draggable divider, not a keyboard action.
    frame.text(right - 1, 18, "↔", "accent", "selected", 1)
    chrome(frame, 1, "Adjustable panel widths",
           "Drag a vertical divider to allocate space · Example: wider queues for long names · Message list/detail stays 50/50",
           "Drag dividers  " + FOOTER)
    save(frame, "design-1-panels.png", result)


def clear_focus():
    result = controller()
    result.pane = 1
    frame = render(result, WIDTH, HEIGHT)
    for name, x, y, width, height in frame.regions:
        if name not in ("connections", "queues", "messages"):
            continue
        frame.box(x, y, width, height, "", False)
        frame.line(x + 1, y, width - 2)
        active = name == "queues"
        title = {"connections": "1 Connections", "queues": "2 Queues", "messages": "3 Messages"}[name]
        frame.fill(x + 1, y, width - 2, bg="accent" if active else "bar")
        frame.text(x + 2, y, title, "bg" if active else "muted", "accent" if active else "bar", width - 4)
        if not active:
            # Keep the selected context visible while reserving accent for focus.
            row_y = 6
            for column in range(x + 2, x + width - 2):
                char, fg, _ = frame.cells[row_y][column]
                frame.cells[row_y][column] = (char, "green" if char == "●" else "muted", "bar")
    # A shape as well as color indicates the active row.
    frame.text(29, 9, ">", "accent", "selected")
    chrome(frame, 2, "Clearer focus",
           "Active: Queues · Bright title + arrow identify keyboard focus · Dim selections retain the server/message context")
    save(frame, "design-2-focus.png", result)


def balanced_columns():
    result = controller()
    frame = render(result, WIDTH, HEIGHT)
    message_table(frame, result.current, 62, 96, balanced=True)
    chrome(frame, 3, "Balanced message columns",
           "Compact Type labels · Prio and Size aligned right · More space for the body preview · Full type remains in message detail")
    save(frame, "design-3-columns.png", result)


TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\b(?:true|false|null)\b|\b\d+(?:\.\d+)?\b')


def syntax_line(frame, x, y, text, width, source, offset):
    frame.text(x, y, text, width=width)
    for match in TOKEN.finditer(source):
        start, end = max(0, match.start() - offset), min(len(text), match.end() - offset)
        if start < end:
            role = "accent" if source[match.end():].lstrip().startswith(":") else "green"
            frame.text(x + start, y, text[start:end], role, width=max(0, width - start))


def payload_readability():
    result = controller()
    result.detail_tab = 0  # Body is selected only to demonstrate this proposal.
    frame = render(result, WIDTH, HEIGHT)
    x, y, width, height = 62, 22, 96, 19
    frame.fill(x, y + 2, width, height - 2)
    frame.text(x + 1, y + 2, "JSON   ·   Soft wrap: on   ·   Display formatting", "accent", width=width - 2)
    body = result.current.selected_message.detail()
    available, drawn = width - 8, 0
    for line_number, line in enumerate(body.splitlines(), 1):
        chunks = textwrap.wrap(line, available, replace_whitespace=False, drop_whitespace=False) or [""]
        consumed = 0
        for index, chunk in enumerate(chunks):
            if drawn >= height - 6:
                break
            label = f"{line_number:>4}" if index == 0 else "   >"
            row = y + 4 + drawn
            frame.text(x + 1, row, label, "muted", width=5)
            syntax_line(frame, x + 7, row, chunk, available, line, consumed)
            consumed += len(chunk)
            drawn += 1
    frame.text(x + 1, y + height - 1, "TextMessage · JSON · wrapped lines · Headers remains the default tab", "muted", width=width - 2)
    chrome(frame, 4, "Readable message bodies",
           "Selected Body tab for this example · Optional JSON/XML coloring and soft wrapping · Payload bytes remain unchanged")
    save(frame, "design-4-payload.png", result)


def consumer_rows():
    result = controller()
    frame = render(result, WIDTH, HEIGHT)
    x, y, width, height = 6, 5, 148, 35
    frame.fill(x, y, width, height)
    frame.box(x, y, width, height, "Queue consumers · DEV.ORDERS", True)
    frame.text(x + 3, y + 2, "Local Artemis   ·   4 attached consumers   ·   Browser sessions hidden", "accent", width=width - 6)
    frame.text(x + 3, y + 4, "Selection stays visible while the selector is shown on the second line.", "muted", width=width - 6)
    offsets, spans = (3, 19, 49, 80, 101, 119), (14, 28, 29, 19, 16, 20)
    for offset, span, label in zip(offsets, spans, ("ConsumerId", "Application / Client", "Remote endpoint", "User", "Protocol", "Status")):
        frame.text(x + offset, y + 6, label, "muted", width=span)
    frame.line(x + 2, y + 7, width - 4)
    examples = [Consumer("c-184", "orders-worker-1", "10.20.0.21:50412", "orders-app", "CORE", "region = 'EU'", status="OK"),
                Consumer("c-185", "orders-worker-2", "10.20.0.22:50418", "orders-app", "CORE", "region = 'US'", status="OK"),
                Consumer("c-203", "audit-service", "10.20.0.30:51102", "audit-app", "AMQP", "", status="OK"),
                Consumer("c-211", "priority-worker", "10.20.0.24:50588", "orders-app", "CORE", "JMSPriority >= 7 AND retryCount < 3", status="OK")]
    for index, consumer in enumerate(examples):
        row, active = y + 8 + index * 3, index == 0
        bg, role = ("selected", "accent") if active else ("bg", "text")
        frame.fill(x + 2, row, width - 4, 2, bg=bg)
        values = (consumer.consumer_id, consumer.application, consumer.remote_address,
                  consumer.user, consumer.protocol, consumer.status)
        for offset, span, value in zip(offsets, spans, values):
            frame.text(x + offset, row, value, role, bg, span)
        frame.text(x + 19, row + 1, "Selector: " + (consumer.selector or "none"), "muted", bg, width - 22)
        if active:
            frame.text(x + 2, row, ">", "accent", bg)
    frame.line(x + 2, y + height - 7, width - 4)
    frame.text(x + 3, y + height - 5, "1-4 / 4 consumers   ·   Missing provider fields display n/a", "muted", width=width - 6)
    frame.text(x + 3, y + height - 3, " Refresh (c) ", "accent", "selected")
    frame.text(x + width - 14, y + height - 3, " Close ", "accent")
    chrome(frame, 5, "Two-line consumer rows",
           "Consumer inspector · Example data · Selector and status visible without opening each consumer",
           "c refresh consumers  ↑↓ select  Enter full details  Tab/click controls  Esc return to browser  q quit")
    save(frame, "design-5-consumers.png", result)


def field(frame, x, y, label, value, width=46):
    frame.text(x, y, label, "muted", width=width)
    frame.fill(x, y + 1, width, bg="bar")
    frame.text(x + 1, y + 1, value, bg="bar", width=width - 2)


def grouped_composer():
    result = controller()
    frame = render(result, WIDTH, HEIGHT)
    x, y, width, height = 4, 3, 152, 39
    left, right, meta_width = x + 3, 103, 46
    body_width = right - left - 4
    frame.fill(x, y, width, height)
    frame.box(x, y, width, height, "New TextMessage", True)
    frame.text(left, y + 2, " 1 Draft ", "bg", "accent")
    frame.text(left + 13, y + 2, "→   2 Review   →   3 Result", "muted")
    frame.text(left, y + 4, "Destination   Local Artemis / DEV.ORDERS", "accent", width=width - 6)
    frame.text(left, y + 5, "ARTEMIS · localhost:61616", "muted", width=width - 6)
    frame.line(left, y + 6, width - 6)
    body = json.dumps({"event": "order.created", "order_id": "ORD-2001", "customer": "Ada Lovelace",
                       "total": 129.9, "currency": "EUR", "items": [{"sku": "KB-01", "quantity": 1}]}, indent=2)
    frame.text(left, y + 8, "Body", "accent")
    frame.text(left + 8, y + 8, " Load file (F3) ", "accent", "bar")
    frame.text(left + body_width - 14, y + 8, f"UTF-8 · {len(body.encode())} B", "muted", width=14)
    frame.fill(left, y + 10, body_width, 20, bg="bar")
    for number, line in enumerate(body.splitlines(), 1):
        frame.text(left, y + 9 + number, f"{number:>3}", "muted", "bar", 4)
        frame.text(left + 5, y + 9 + number, line, bg="bar", width=body_width - 6)
    frame.text(left + 5, y + 10, "{", "bg", "accent")
    frame.text(left, y + 31, "TextMessage · body editor", "muted", width=body_width)
    frame.text(right, y + 8, "Delivery", "accent", width=meta_width)
    frame.text(right, y + 10, "Prio", "muted")
    frame.text(right + 6, y + 10, " 4 ", bg="bar")
    frame.text(right + 14, y + 10, " Persistent ", "accent", "bar")
    frame.text(right, y + 12, "TTL (ms)", "muted")
    frame.text(right + 10, y + 12, " 0 ", bg="bar")
    frame.text(right + 16, y + 12, "No expiry", "muted")
    frame.line(right, y + 14, meta_width)
    frame.text(right, y + 16, "Headers", "accent", width=meta_width)
    field(frame, right, y + 18, "JMSType", "order.created", meta_width)
    field(frame, right, y + 21, "JMSCorrelationID", "req-2026-1014-01", meta_width)
    frame.line(right, y + 24, meta_width)
    frame.text(right, y + 26, "Properties", "accent", width=meta_width)
    frame.text(right, y + 28, "Name         Type        Value", "muted", width=meta_width)
    for index, value in enumerate(("region       String      EU", "source       String      jmsbrowser", "replay       Boolean     false")):
        frame.fill(right, y + 29 + index, meta_width, bg="bar")
        frame.text(right, y + 29 + index, value, bg="bar", width=meta_width)
    frame.text(right, y + 32, "+ Property (F6)", "accent", width=meta_width)
    frame.line(left, y + 34, width - 6)
    frame.text(left, y + 36, " Load draft (F5) ", "accent", "bar")
    frame.text(left + 20, y + 36, " Save draft (F4) ", "accent", "bar")
    frame.text(right - 4, y + 36, " Preview send (F2) ", "bg", "accent")
    frame.text(x + width - 14, y + 36, " Cancel ", "muted")
    frame.text(left, y + 37, "Draft · Review opens a separate screen with the explicit Send 1 message action.", "muted", width=width - 6)
    chrome(frame, 6, "Grouped composer",
           "Body on the left · Delivery, Headers and Properties grouped on the right · One primary action per step",
           "Tab/Shift+Tab/click fields  F2 preview  F3 load body  F4 save draft  F5 load draft  F6 property  Esc cancel")
    frame.fill(WIDTH - 15, 0, 15, bg="bar")
    frame.text(WIDTH - 15, 0, "UNSENT DRAFT", "accent", "bar", 15)
    save(frame, "design-6-composer.png", result)


if __name__ == "__main__":
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for proposal in (adjustable_panels, clear_focus, balanced_columns,
                     payload_readability, consumer_rows, grouped_composer):
        proposal()
    print(f"Saved six design proposals to {OUTPUT}")
