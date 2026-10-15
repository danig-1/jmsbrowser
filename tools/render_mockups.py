"""Render reproducible terminal UI proposals. Requires Pillow; no live broker access.

Every element sits on a character grid and maps to curses text/color attributes.
Usage: python tools/render_mockups.py [--font /path/to/monospace.ttf]
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "designs"
COLS, ROWS = 144, 42
CW, CH, PAD = 11, 24, 24

THEMES = {
    "classic": dict(bg="#0d1720", fg="#d7e4eb", muted="#8195a4", border="#456071",
                    accent="#67d4d1", select="#164a5a", green="#75d68b", yellow="#edc778",
                    bar="#203440", key="#9adfe0"),
    "compact": dict(bg="#10121b", fg="#dddfee", muted="#9399b2", border="#42465d",
                    accent="#b6a2ff", select="#3c325b", green="#8fd59d", yellow="#f4c98b",
                    bar="#25283b", key="#c9baff"),
    "reader": dict(bg="#192322", fg="#e5e9df", muted="#9aaea3", border="#4b6257",
                   accent="#c4d891", select="#364b3c", green="#8bd5a0", yellow="#ead29a",
                   bar="#2a3932", key="#d1e2ab"),
}


class Terminal:
    def __init__(self, theme: str, font: Path):
        self.theme = THEMES[theme]
        self.font = ImageFont.truetype(str(font), 20)
        self.cells = [[(" ", "fg", None) for _ in range(COLS)] for _ in range(ROWS)]

    def text(self, x, y, value, color="fg", bg=None, width=None):
        assert 0 <= y < ROWS
        if width is not None:
            value = value[:width].ljust(width)
        assert x >= 0 and x + len(value) <= COLS, (x, y, value)
        for offset, char in enumerate(value):
            self.cells[y][x + offset] = char, color, bg

    def fill(self, x, y, width, height, bg):
        for row in range(y, y + height):
            self.text(x, row, " " * width, bg=bg)

    def line(self, x, y, width, color="border", char="─"):
        self.text(x, y, char * width, color)

    def box(self, x, y, width, height, title, active=False):
        color = "accent" if active else "border"
        self.text(x, y, "┌" + "─" * (width - 2) + "┐", color)
        self.text(x + 2, y, f" {title} ", color)
        for row in range(y + 1, y + height - 1):
            self.text(x, row, "│", color)
            self.text(x + width - 1, row, "│", color)
        self.text(x, y + height - 1, "└" + "─" * (width - 2) + "┘", color)

    def rule(self, x, y, width, label=""):
        self.line(x, y, width)
        if label:
            self.text(x + 1, y, f" {label} ", "muted")

    def selected(self, x, y, width, value, active=True):
        self.text(x, y, value, "accent" if active else "fg", "select", width)

    def header(self, subtitle):
        self.fill(0, 0, COLS, 1, "bar")
        self.text(2, 0, "JMS BROWSER", "accent", "bar")
        self.text(17, 0, subtitle, "fg", "bar")
        self.text(123, 0, "BROWSE ONLY", "green", "bar")
        self.text(1, 2, "Local Artemis  /  DEV.ORDERS", "fg")
        self.text(103, 2, "3 messages  ·  refreshed 14:32:08", "muted")

    def footer(self, value):
        self.text(1, 39, "●", "green")
        self.text(3, 39, "2 connected", "muted")
        self.text(21, 39, "Snapshot: 3 of 3 messages", "muted")
        self.text(84, 39, "Messages remain on the queue", "green")
        self.fill(0, 41, COLS, 1, "bar")
        self.text(1, 41, value, "key", "bar", COLS - 2)

    def save(self, filename):
        image = Image.new("RGB", (COLS * CW + 2 * PAD, ROWS * CH + 2 * PAD), self.theme["bg"])
        draw = ImageDraw.Draw(image)
        for y, row in enumerate(self.cells):
            for x, (char, color, bg) in enumerate(row):
                px, py = PAD + x * CW, PAD + y * CH
                if bg:
                    draw.rectangle((px, py, px + CW - 1, py + CH - 1), fill=self.theme[bg])
                if char != " ":
                    draw.text((px, py + 1), char, font=self.font, fill=self.theme[color], anchor="la")
        path = OUTPUT / filename
        image.save(path)
        print(path)


def servers(t, x, y, width, grouped=False):
    t.text(x, y, "LOCAL", "muted")
    t.selected(x, y + 2, width, "> ● Local Artemis", active=False)
    t.text(x + 2, y + 2, "●", "green", "select")
    t.text(x + 4, y + 3, "Artemis · 61616", "muted")
    t.text(x + 2, y + 5, "●", "green")
    t.text(x + 4, y + 5, "Local IBM MQ")
    t.text(x + 4, y + 6, "IBM MQ · QM1", "muted")
    t.text(x, y + 10, "SAVED", "muted")
    for dy, name in ((12, "Staging Artemis"), (15, "Staging IBM MQ")):
        t.text(x + 2, y + dy, "○", "muted")
        t.text(x + 4, y + dy, name)
        t.text(x + 4, y + dy + 1, "disconnected", "muted")


def json_body(t, x, y, numbers=False):
    payload = json.loads((ROOT / "dev/messages/DEV.ORDERS/01-created.json").read_text())
    lines = json.dumps(payload, indent=2).splitlines()
    for index, line in enumerate(lines):
        if numbers:
            t.text(x, y + index, f"{index + 1:>2}", "muted")
            tx = x + 4
        else:
            tx = x
        t.text(tx, y + index, line, "fg" if line.strip() in ("{", "}", "[", "]", "],", "},") else "accent")


def classic(font):
    t = Terminal("classic", font)
    t.header("01 / Classic inspector")
    t.box(0, 4, 30, 34, "1 Connections")
    t.box(31, 4, 37, 34, "2 Queues")
    t.box(69, 4, 75, 34, "3 Messages · FOCUS", active=True)
    servers(t, 2, 6, 26)
    t.rule(2, 29, 26)
    t.text(3, 31, "localhost:61616", "muted")
    t.text(3, 33, "Enter  connect/disconnect", "muted")
    t.text(33, 6, "Queue                     Depth", "muted")
    queues = [("DEV.ORDERS", 3), ("DEV.PAYMENTS", 2), ("DEV.NOTIFICATIONS", 1), ("DEV.EMPTY", 0)]
    for i, (queue, depth) in enumerate(queues):
        value = f"{'>' if i == 0 else ' '} {queue:<24} {depth:>3}"
        if i == 0:
            t.selected(33, 8 + i * 2, 33, value, active=False)
        else:
            t.text(33, 8 + i * 2, value, "muted" if depth == 0 else "fg")
    t.rule(33, 25, 33, "Selected queue")
    t.text(34, 27, "Routing      ANYCAST", "muted")
    t.text(34, 29, "Durable      yes", "muted")
    t.text(34, 31, "Messages     3", "fg")
    t.text(34, 34, "4 queues", "muted")
    t.text(71, 6, "  Message ID       Type     Preview", "muted")
    for i, (mid, event, order) in enumerate((("...a91f", "created", "1042"), ("...b20c", "created", "1043"), ("...c83a", "updated", "1044"))):
        value = f"{'>' if i == 0 else ' '} {mid:<16} Text     order.{event}  ORD-{order}"
        if i == 0:
            t.selected(71, 8 + i, 71, value)
        else:
            t.text(71, 8 + i, value)
    t.rule(71, 12, 71, "Message 1 / 3")
    t.text(73, 14, "[ Body ]    Headers    Properties", "accent")
    t.text(73, 16, "TextMessage · application/json · persistent", "muted")
    json_body(t, 73, 18)
    t.text(73, 35, "Enter  expand message      h  cycle detail tab", "muted")
    t.footer("Tab/click  panel   Shift+Tab  previous   ↑↓  select   Enter  open/connect   /  filter   r  refresh   ?  help   q  quit")
    t.save("01-classic.png")


def compact(font):
    t = Terminal("compact", font)
    t.header("02 / Compact workbench")
    for x in (26, 64):
        for y in range(4, 38):
            t.text(x, y, "│", "border")
    t.text(1, 4, "1 CONNECTIONS", "muted")
    t.text(28, 4, "2 QUEUES", "muted")
    t.text(66, 4, "3 MESSAGES  [FOCUS]", "accent")
    t.line(0, 5, 144)
    t.text(1, 7, "  Name", "muted")
    names = [("Local Artemis", True), ("Local IBM MQ", True), ("Staging Artemis", False), ("Staging IBM MQ", False)]
    for i, (name, connected) in enumerate(names):
        row = 9 + i
        value = f"{'>' if i == 0 else ' '} {'●' if connected else '○'} {name}"
        if i == 0:
            t.selected(1, row, 24, value, active=False)
        else:
            t.text(1, row, value, "fg" if connected else "muted")
        if connected:
            t.text(3, row, "●", "green", "select" if i == 0 else None)
    t.text(28, 7, "  Queue                      Depth", "muted")
    for i, (name, depth) in enumerate((("DEV.ORDERS", 3), ("DEV.PAYMENTS", 2), ("DEV.NOTIFICATIONS", 1), ("DEV.EMPTY", 0))):
        value = f"{'>' if i == 0 else ' '} {name:<25} {depth:>5}"
        if i == 0:
            t.selected(28, 9 + i, 35, value, active=False)
        else:
            t.text(28, 9 + i, value, "muted" if depth == 0 else "fg")
    t.text(66, 7, "  #  Message ID    Type  Pri  Body preview", "muted")
    for i, (mid, event, order) in enumerate((("...a91f", "created", "1042"), ("...b20c", "created", "1043"), ("...c83a", "updated", "1044"))):
        value = f"{'>' if i == 0 else ' '} {i + 1:<2} {mid:<13} Text  4    order.{event}  ORD-{order}"
        if i == 0:
            t.selected(66, 9 + i, 77, value)
        else:
            t.text(66, 9 + i, value)
    t.rule(66, 14, 77, "PREVIEW  1 / 3")
    t.text(67, 16, "[Body]  Headers  Properties", "accent")
    json_body(t, 68, 18, numbers=True)
    t.text(67, 35, "JSON · persistent · priority 4", "muted")
    t.rule(0, 34, 25)
    t.text(1, 36, "2 online / 4 saved", "muted")
    t.rule(28, 34, 35)
    t.text(29, 36, "4 queues / 6 total messages", "muted")
    t.footer("Tab/click  panel   ↑↓  select   PgUp/PgDn  page   Enter  open/connect   /  filter   h  detail tab   r  refresh   ?  help   q  quit")
    t.save("02-compact.png")


def reader(font):
    t = Terminal("reader", font)
    t.header("03 / Payload reader")
    t.box(0, 4, 25, 34, "1 Connections")
    t.box(26, 4, 31, 34, "2 Queues")
    t.box(58, 4, 86, 34, "3 Messages · FOCUS", active=True)
    servers(t, 2, 6, 21)
    t.text(2, 35, "2 online / 4 saved", "muted")
    t.text(28, 6, "Queue                 Depth", "muted")
    for i, (name, depth) in enumerate((("DEV.ORDERS", 3), ("DEV.PAYMENTS", 2), ("DEV.NOTIFICATIONS", 1), ("DEV.EMPTY", 0))):
        y = 8 + i * 3
        value = f"{'>' if i == 0 else ' '} {name:<19} {depth:>3}"
        if i == 0:
            t.selected(28, y, 27, value, active=False)
        else:
            t.text(28, y, value, "muted" if depth == 0 else "fg")
        t.text(30, y + 1, "ANYCAST · durable", "muted")
    t.text(28, 35, "4 queues", "muted")
    t.selected(60, 6, 82, "> 1  order.created  ORD-1042    |    2  ORD-1043    |    3  ORD-1044")
    t.text(61, 8, "Message 1 of 3     ID ...a91f     TextMessage     priority 4", "muted")
    t.rule(60, 10, 82)
    t.text(61, 12, "[ Body ]   Headers   Properties", "accent")
    t.text(119, 12, "JSON  /  formatted", "muted")
    json_body(t, 62, 15, numbers=True)
    t.rule(60, 32, 82)
    t.text(62, 34, "Persistent   ·   no expiry   ·   source: web-shop", "muted")
    t.text(62, 36, "← →  previous / next message       Enter  full screen", "muted")
    t.footer("Tab/click  panel   ↑↓  connections/queues   ←→  messages   Enter  open/connect   h  detail tab   /  filter   r  refresh   ?  help   q  quit")
    t.save("03-reader.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path)
    args = parser.parse_args()
    candidates = [args.font, Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/consola.ttf",
                  Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")]
    font = next((p for p in candidates if p and p.is_file()), None)
    if font is None:
        parser.error("Pass --font with a monospaced TrueType font.")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for render in (classic, compact, reader):
        render(font)


if __name__ == "__main__":
    main()
