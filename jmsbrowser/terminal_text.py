"""Safe, cell-aware text operations shared by terminal presentation code."""
import unicodedata

from wcwidth import wcwidth, wcswidth


def safe_text(value):
    result = []
    for char in str(value):
        if char == "\t":
            result.append("    ")
        elif unicodedata.category(char) in ("Cc", "Cf", "Cs"):
            result.append(f"\\x{ord(char):02x}" if ord(char) < 256 else f"\\u{ord(char):04x}")
        else:
            result.append(char)
    return "".join(result)


def clipped(value, width):
    value = safe_text(value)
    if wcswidth(value) <= width:
        return value
    result, used = [], 0
    for char in value:
        size = max(0, wcwidth(char))
        if used + size > max(0, width - 1):
            break
        result.append(char)
        used += size
    return "".join(result) + ("…" if width else "")


def cell_offset(value, columns):
    """Return an index at a cell boundary, including trailing combining marks."""
    used, index = 0, 0
    while index < len(value):
        size = max(0, wcwidth(value[index]))
        if size and used >= columns:
            break
        used += size
        index += 1
    return index
