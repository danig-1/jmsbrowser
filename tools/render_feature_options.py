"""Render three feature proposals using the application's terminal grid.

All queue contents, samples and drafts in these screenshots are illustrative.
This script does not connect to brokers or implement the proposed actions.
Run with the optional Pillow dependency: python -m tools.render_feature_options
"""
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

from jmsbrowser.config import Config, ConnectionConfig
from tools.sample_data import SampleAdapter
from jmsbrowser.domain import Message, QueueInfo
from jmsbrowser.screen import render
from jmsbrowser.screenshot import save_png
from jmsbrowser.state import Controller

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/designs"
WIDTH, HEIGHT = 160, 46


def example_messages():
    messages = []
    for index, priority in enumerate((8, 9, 7)):
        mid = f"ID:example-orders-{1042 + index:04}"
        payload = {"event": "order.created", "order_id": f"ORD-{1042 + index}", "region": "EU", "total": 129.90 + index}
        body = json.dumps(payload)
        messages.append(Message(mid, "TextMessage", priority, body, len(body.encode()),
                                {"JMSMessageID": mid, "JMSType": "order.created", "JMSPriority": priority,
                                 "JMSTimestamp": 1791981234567 + index * 1000, "JMSExpiration": 0,
                                 "JMSDeliveryMode": 2, "JMSRedelivered": False, "JMSCorrelationID": f"req-{1042 + index}",
                                 "JMSDestination": "ActiveMQQueue[DEV.ORDERS]"},
                                {"region": "EU", "source": "web-shop"}))
    return messages


def base(option, title, queue_name="DEV.ORDERS"):
    config = Config((ConnectionConfig("Local Artemis", "artemis"),
                     ConnectionConfig("Local IBM MQ", "ibm", port=1414, queue_manager="QM1")))
    controller = Controller(config)
    for state in controller.connections:
        adapter = SampleAdapter(state.config)
        state.status, state.queues = "connected", adapter.list_queues()
    state = controller.current
    state.queues = sorted([replace(queue, depth=1000) if queue.name == "DEV.ORDERS" else queue for queue in state.queues]
                          + [QueueInfo("DEV.LOADTEST", 1000)], key=lambda queue: queue.name)
    state.queue_index = next(i for i, queue in enumerate(state.queues) if queue.name == queue_name)
    state.messages = example_messages()
    controller.pane = 2
    controller.notice = "PROPOSAL · Illustrative data."
    frame = render(controller, WIDTH, HEIGHT)
    frame.fill(17, 0, WIDTH - 33, bg="bar")
    frame.text(17, 0, f"PROPOSAL {option} / {title.upper()} / EXAMPLE DATA", "accent", "bar", WIDTH - 33)
    return controller, frame


def footer(frame, text, notice):
    frame.fill(1, HEIGHT - 3, WIDTH - 2)
    frame.text(1, HEIGHT - 3, notice, "muted", width=WIDTH - 2)
    frame.fill(0, HEIGHT - 1, WIDTH, bg="bar")
    frame.text(1, HEIGHT - 1, text, "accent", "bar", WIDTH - 2)


def selector():
    controller, frame = base(1, "JMS selector")
    _, x, y, width, _ = next(region for region in frame.regions if region[0] == "messages")
    frame.text(x + 2, y + 1, "Selector: JMSPriority >= 7 AND region = 'EU'", "accent", width=width - 4)
    footer(frame, "s edit selector  x clear selector  c consumers  r refresh  ↑↓ messages  Enter detail  q quit",
           "3 messages loaded · browse limit 1000 · queue depth 1000 · selector filters JMS headers / properties")
    save_png(frame, OUTPUT / "feature-1-selector.png")
    controller.close()


def history():
    controller, frame = base(2, "Queue depth history", "DEV.LOADTEST")
    x, y, width, height = 8, 5, 144, 35
    frame.fill(x, y, width, height)
    frame.box(x, y, width, height, "Queue depth history · DEV.LOADTEST", True)
    frame.text(x + 3, y + 2, "Local Artemis   Range: last 15 minutes   Sample interval: 30 seconds   [ Pause ]", "accent", width=width - 6)
    samples = [760, 780, 820, 860, 900, 880, 840, 780, 720, 680, 640, 660, 700, 760, 830, 900,
               970, 1040, 1110, 1140, 1080, 1000, 920, 860, 900, 940, 980, 1040, 1020, 990, 1000]
    frame.text(x + 3, y + 4, "Depth  1,000       Net change  +240       Minimum  640       Maximum  1,140       Samples  31", width=width - 6)
    left, right, top, bottom = x + 12, x + width - 7, y + 8, y + 23
    frame.text(x + 3, y + 6, "Messages", "muted")
    for value in (1200, 900, 600, 300, 0):
        row = top + round((1200 - value) / 1200 * (bottom - top))
        frame.text(x + 3, row, f"{value:>5,}", "muted", width=5)
        frame.line(left, row, right - left + 1, "border")
    for col in range(left, right + 1):
        position = (col - left) / (right - left) * (len(samples) - 1)
        index = min(len(samples) - 2, int(position))
        value = samples[index] + (samples[index + 1] - samples[index]) * (position - index)
        row = top + round((1200 - value) / 1200 * (bottom - top))
        frame.text(col, row, "●" if col == right else "•", "accent")
    start = datetime(2026, 10, 14, 12, 0)
    for index in (0, 10, 20, 30):
        col = left + round(index / 30 * (right - left))
        label = (start + timedelta(seconds=index * 30)).strftime("%H:%M")
        frame.text(max(left, min(col - 2, right - 4)), bottom + 2, label, "muted")
    frame.line(x + 2, y + height - 6, width - 4)
    frame.text(x + 3, y + height - 4, "Depth is sampled over time. Net change does not imply a send or receive rate.", "muted", width=width - 6)
    frame.text(x + 3, y + height - 2, "Space pause / resume   t sample now   Esc return to browser", "accent", width=width - 6)
    footer(frame, "t queue history  c consumers  r refresh  Esc back  q quit", "PROPOSAL · Example depth samples for DEV.LOADTEST.")
    save_png(frame, OUTPUT / "feature-2-history.png")
    controller.close()


def composer():
    controller, frame = base(3, "Message composer")
    frame.fill(WIDTH - 15, 0, 15, bg="bar")
    frame.text(WIDTH - 15, 0, "UNSENT DRAFT", "accent", "bar", 15)
    x, y, width, height = 8, 5, 144, 35
    frame.fill(x, y, width, height)
    frame.box(x, y, width, height, "New message · preview before send", True)
    frame.text(x + 3, y + 2, "Server: Local Artemis       Queue: DEV.ORDERS", "accent", width=width - 6)
    frame.text(x + 3, y + 4, "Type: TextMessage    Priority: [ 4 ]    Delivery: [ Persistent ]    TTL: [ 0 ms / no expiry ]", width=width - 6)
    body = json.dumps({"event": "order.created", "order_id": "ORD-2001", "customer": "Ada Lovelace", "total": 129.9, "currency": "EUR"}, indent=2)
    editor_x, editor_y, editor_width = x + 3, y + 8, 79
    meta_x, meta_width = editor_x + editor_width + 4, width - editor_width - 10
    frame.text(editor_x, y + 6, f"Body   [ Load file ]   UTF-8 · {len(body.encode())} B", "accent", width=editor_width)
    frame.fill(editor_x, editor_y, editor_width, 16, bg="bar")
    for index, line in enumerate(body.splitlines()):
        frame.text(editor_x + 1, editor_y + 1 + index, f"{index + 1:>3}", "muted", "bar", 4)
        frame.text(editor_x + 6, editor_y + 1 + index, line, "text", "bar", editor_width - 7)
    frame.text(meta_x, y + 6, "Headers", "accent", width=meta_width)
    header_rows = [("JMSType", "order.created"), ("JMSCorrelationID", "req-2026-1014-01"), ("MessageId / timestamp", "Assigned when sent")]
    for index, (key, value) in enumerate(header_rows):
        frame.text(meta_x, y + 8 + index * 2, key, "muted", width=meta_width)
        frame.text(meta_x, y + 9 + index * 2, value, width=meta_width)
    frame.text(meta_x, y + 16, "Properties", "accent", width=meta_width)
    frame.text(meta_x, y + 18, "Name         Type      Value", "muted", width=meta_width)
    frame.line(meta_x, y + 19, meta_width)
    for index, row in enumerate(("region       String    EU", "source       String    jmsbrowser", "replay       Boolean   false")):
        frame.text(meta_x, y + 20 + index, row, width=meta_width)
    frame.line(x + 2, y + height - 9, width - 4)
    frame.text(x + 3, y + height - 7, "Preview: 1 TextMessage → Local Artemis / DEV.ORDERS", "accent", width=width - 6)
    frame.text(x + 3, y + height - 5, f"Payload {len(body.encode())} B · 3 properties · persistent · priority 4 · no expiry", "muted", width=width - 6)
    frame.text(x + 3, y + height - 3, " Send 1 message ", "accent", "selected")
    frame.text(x + 24, y + height - 3, "[ Save draft ]    [ Cancel ]", "accent", width=width - 27)
    footer(frame, "Tab/click fields  Enter activate button  Esc cancel  q quit", "PROPOSAL · Illustrative unsent draft.")
    save_png(frame, OUTPUT / "feature-3-composer.png")
    controller.close()


if __name__ == "__main__":
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for proposal in (selector, history, composer):
        proposal()
    print(f"Saved three feature proposals to {OUTPUT}")
