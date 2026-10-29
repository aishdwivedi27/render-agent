"""WCAG relative luminance and contrast ratio, shared by the disclaimer and the wet-tile check."""

from __future__ import annotations

from collections.abc import Sequence


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    """'#8C8C8C' -> (140, 140, 140). Raises ValueError for anything else."""
    text = value.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"'{value}' is not a colour like #8C8C8C")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def luminance(rgb: Sequence[float]) -> float:
    def channel(c: float) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(float(x)) for x in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: Sequence[float], b: Sequence[float]) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)
