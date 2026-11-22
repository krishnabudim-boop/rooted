"""Small color helpers shared by the design modules (hex, Lab, contrast)."""

from __future__ import annotations

import colorsys
import math
import re

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def normalize_hex(value: str) -> str | None:
    """Return ``#RRGGBB`` uppercase, or None if ``value`` is not a hex color."""
    v = value.strip()
    if re.fullmatch(r"#?[0-9a-fA-F]{3}", v):
        v = v.lstrip("#")
        v = "".join(c * 2 for c in v)
    m = _HEX.match(v)
    return ("#" + m.group(1).upper()) if m else None


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(r: float, g: float, b: float) -> str:
    def c(v: float) -> int:
        return max(0, min(255, int(round(v))))

    return f"#{c(r):02X}{c(g):02X}{c(b):02X}"


def int_to_hex(value: int) -> str:
    return f"#{(value >> 16) & 255:02X}{(value >> 8) & 255:02X}{value & 255:02X}"


def floats_to_hex(color: object) -> str | None:
    """PyMuPDF drawing color (gray / rgb / cmyk floats in 0..1) -> hex."""
    if color is None:
        return None
    vals = list(color)  # type: ignore[call-overload]
    if len(vals) == 1:
        v = vals[0] * 255
        return rgb_to_hex(v, v, v)
    if len(vals) == 3:
        return rgb_to_hex(*(v * 255 for v in vals))
    if len(vals) == 4:
        c, m, y, k = vals
        return rgb_to_hex(255 * (1 - c) * (1 - k), 255 * (1 - m) * (1 - k), 255 * (1 - y) * (1 - k))
    return None


def _lin(v: float) -> float:
    v /= 255
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def luminance(h: str) -> float:
    r, g, b = hex_to_rgb(h)
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def to_lab(h: str) -> tuple[float, float, float]:
    r, g, b = (_lin(v) for v in hex_to_rgb(h))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a: str, b: str) -> float:
    la, lb = to_lab(a), to_lab(b)
    return math.dist(la, lb)


def mix(a: str, b: str, t: float) -> str:
    """Linear mix: t=0 -> a, t=1 -> b."""
    ra, rb = hex_to_rgb(a), hex_to_rgb(b)
    return rgb_to_hex(*(x + (y - x) * t for x, y in zip(ra, rb, strict=True)))


def saturation(h: str) -> float:
    r, g, b = (v / 255 for v in hex_to_rgb(h))
    return colorsys.rgb_to_hsv(r, g, b)[1]


def is_neutral(h: str, tol: float = 0.12) -> bool:
    return saturation(h) < tol
