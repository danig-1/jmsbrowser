"""Render three consumer UI proposals; all consumer data is illustrative.

Uses the application's real character-grid renderer. Does not attach consumers,
query brokers, or change queues. Requires the optional Pillow dependency.
"""
from pathlib import Path

from jmsbrowser.config import Config, ConnectionConfig
from jmsbrowser.demo import DemoAdapter
from jmsbrowser.domain import QueueInfo
from jmsbrowser.screen import render
from jmsbrowser.screenshot import save_png
from jmsbrowser.state import Controller

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/designs"
CONSUMERS = [
    ("c-184", "orders-worker-1", "10.20.0.21:50412", "orders-app", "CORE"),
    ("c-185", "orders-worker-2", "10.20.0.22:50418", "orders-app", "CORE"),
    ("c-203", "audit-service", "10.20.0.30:51102", "audit-app", "AMQP"),
]


def base(option, title):
    config = Config((ConnectionConfig("Local Artemis", "artemis"), ConnectionConfig("Local IBM MQ", "ibm")))
    controller = Controller(config)
    adapter = DemoAdapter(config.connections[0])
    state = controller.current
    state.status = "connected"
    state.queues = sorted([*adapter.list_queues(), QueueInfo("DEV.LOADTEST", 1000)], key=lambda q: q.name)
    state.queue_index = next(i for i, q in enumerate(state.queues) if q.name == "DEV.ORDERS")
    state.messages = adapter.browse("DEV.ORDERS")
    controller.pane = 2
    controller.notice = "CONSUMER UI PROPOSAL · Example consumer data; no consumers have been attached."
    frame = render(controller, 160, 46)
    frame.fill(17, 0, 126, bg="bar")
    frame.text(17, 0, f"OPTION {option} / {title.upper()} / EXAMPLE DATA", "accent", "bar", 126)
    return frame


def consumers_tab():
    frame = base(1, "Consumers tab")
    _, x, y, width, height = next(region for region in frame.regions if region[0] == "detail")
    frame.fill(x, y, width, height)
    cursor = x + 1
    for label in ("Body", "Headers", "Properties", "Consumers (3)"):
        text = f" {label} "
        active = label.startswith("Consumers")
        frame.text(cursor, y, text, "accent" if active else "muted", "selected" if active else "bg")
        cursor += len(text) + 3
    frame.text(x + 1, y + 2, "Queue: DEV.ORDERS  ·  3 attached consumers", "accent", width=width - 2)
    offsets = (1, 17, 43)
    for offset, heading in zip(offsets, ("ConsumerId", "Application / Client", "Remote endpoint")):
        frame.text(x + offset, y + 4, heading, "muted")
    for index, (identity, application, address, user, protocol) in enumerate(CONSUMERS):
        row = y + 6 + index * 3
        for offset, value in zip(offsets, (identity, application, address)):
            frame.text(x + offset, row, value)
        frame.text(x + 17, row + 1, f"{protocol} · user: {user} · selector: none", "muted", width=width - 19)
    frame.line(x, y + height - 4, width)
    frame.text(x + 1, y + height - 2, "c refresh consumers  ·  Enter inspect  ·  browser sessions excluded", "muted", width=width - 2)
    save_png(frame, OUTPUT / "consumers-1-tab.png")


def inspector():
    frame = base(2, "Queue consumer inspector")
    _, qx, qy, qw, _ = next(region for region in frame.regions if region[0] == "queues")
    frame.text(qx + 2, qy + 2, "[ Consumers ]", "accent", width=qw - 4)
    x, y, width, height = 10, 7, 140, 30
    frame.fill(x, y, width, height)
    frame.box(x, y, width, height, "Queue consumers · DEV.ORDERS · 3 attached", True)
    frame.text(x + 3, y + 2, "[ Refresh consumers ]    Filter: all    Browser sessions: hidden", "accent")
    offsets = (3, 19, 46, 76, 94)
    headings = ("ConsumerId", "Application / Client", "Remote endpoint", "User", "Protocol")
    for offset, heading in zip(offsets, headings):
        frame.text(x + offset, y + 5, heading, "muted")
    frame.line(x + 2, y + 6, width - 4)
    for index, values in enumerate(CONSUMERS):
        row = y + 8 + index * 4
        for offset, value in zip(offsets, values):
            frame.text(x + offset, row, value, "accent" if index == 0 else "text")
        frame.text(x + 19, row + 1, "Selector: none    Status: attached", "muted")
    frame.line(x + 2, y + height - 6, width - 4)
    frame.text(x + 3, y + height - 4, "Enter: connection / session details    Esc: close inspector", "accent")
    frame.text(x + 3, y + height - 2, "Missing broker fields show n/a; permission failures show Unavailable.", "muted")
    save_png(frame, OUTPUT / "consumers-2-inspector.png")


def sidebar():
    frame = base(3, "Persistent queue sidebar")
    _, x, y, width, height = next(region for region in frame.regions if region[0] == "queues")
    left, top, inner = x + 2, y + 17, width - 4
    frame.fill(left, top, inner, height - 20)
    frame.line(left, top, inner)
    frame.text(left + 1, top, " Consumers (3) ", "accent", width=inner - 2)
    frame.text(left + 1, top + 2, "DEV.ORDERS", "muted", width=inner - 2)
    for index, (identity, application, address, user, protocol) in enumerate(CONSUMERS):
        row = top + 4 + index * 4
        frame.text(left + 1, row, application, "accent", width=inner - 2)
        frame.text(left + 1, row + 1, f"{identity} · {protocol}", "muted", width=inner - 2)
        frame.text(left + 1, row + 2, address, "muted", width=inner - 2)
    frame.text(left + 1, top + 17, "[ Refresh consumers ]", "accent", width=inner - 2)
    save_png(frame, OUTPUT / "consumers-3-sidebar.png")


if __name__ == "__main__":
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for proposal in (consumers_tab, inspector, sidebar):
        proposal()
    print(f"Saved three consumer display proposals to {OUTPUT}")
