"""Optional PNG export of the same character grid painted by curses."""
from pathlib import Path
import os

from .screen import PALETTE


def save_png(frame, path, font_path=None):
    from PIL import Image, ImageDraw, ImageFont
    candidates = [Path(font_path)] if font_path else []
    candidates += [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/consola.ttf",
                   Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")]
    font_path = next((p for p in candidates if p.is_file()), None)
    if font_path is None:
        raise ValueError("No monospaced font found. Use --font /path/to/font.ttf.")
    font = ImageFont.truetype(str(font_path), 20)
    cw, ch, padding = round(font.getlength("M")), 25, 24
    result = Image.new("RGB", (frame.width * cw + padding * 2, frame.height * ch + padding * 2), PALETTE["bg"])
    draw = ImageDraw.Draw(result)
    for y, row in enumerate(frame.cells):
        for x, (char, role, bg) in enumerate(row):
            px, py = padding + x * cw, padding + y * ch
            draw.rectangle((px, py, px + cw - 1, py + ch - 1), fill=PALETTE[bg])
    for y, row in enumerate(frame.cells):
        for x, (char, role, bg) in enumerate(row):
            px, py = padding + x * cw, padding + y * ch
            if char.strip():
                draw.text((px, py + 1), char, font=font, fill=PALETTE[role], anchor="la")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    result.save(path)
    return path
