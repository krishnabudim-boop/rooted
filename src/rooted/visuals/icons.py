"""Simple geometric icons on a 24x24 grid (WP4). Fixed vocabulary shown to the LLM.

Each icon is a function ``color -> SVG fragment``; strokes only, so one
palette color styles the whole glyph.
"""


from __future__ import annotations

from typing import Callable


def _stroke(c: str, body: str, fill: str = "none") -> str:
    return (f'<g fill="{fill}" stroke="{c}" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round">{body}</g>')


_ICONS: dict[str, Callable[[str], str]] = {
    "circle": lambda c: _stroke(c, '<circle cx="12" cy="12" r="8"/>'),
    "square": lambda c: _stroke(c, '<rect x="4" y="4" width="16" height="16" rx="1"/>'),
    "triangle": lambda c: _stroke(c, '<path d="M12 4 L21 20 H3 Z"/>'),
    "diamond": lambda c: _stroke(c, '<path d="M12 3 L21 12 L12 21 L3 12 Z"/>'),
    "plus": lambda c: _stroke(c, '<path d="M12 4 V20 M4 12 H20"/>'),
    "minus": lambda c: _stroke(c, '<path d="M4 12 H20"/>'),
    "check": lambda c: _stroke(c, '<path d="M4 12.5 L9.5 18 L20 6.5"/>'),
    "cross": lambda c: _stroke(c, '<path d="M5 5 L19 19 M19 5 L5 19"/>'),
    "arrow_right": lambda c: _stroke(c, '<path d="M4 12 H20 M14 6 L20 12 L14 18"/>'),
    "arrow_up": lambda c: _stroke(c, '<path d="M12 20 V4 M6 10 L12 4 L18 10"/>'),
    "star": lambda c: _stroke(c, '<path d="M12 3 L14.8 9 L21 9.8 L16.4 14.2 L17.6 21 L12 17.7 L6.4 21 L7.6 14.2 L3 9.8 L9.2 9 Z"/>'),
    "heart": lambda c: _stroke(c, '<path d="M12 20 C4 14 3 9 6 6.5 C8.5 4.5 11 6 12 8 C13 6 15.5 4.5 18 6.5 C21 9 20 14 12 20 Z"/>'),
    "home": lambda c: _stroke(c, '<path d="M3 11 L12 3 L21 11 M5.5 9.5 V20 H18.5 V9.5 M10 20 V14 H14 V20"/>'),
    "leaf": lambda c: _stroke(c, '<path d="M5 19 C5 9 11 5 20 4 C20 13 16 19 5 19 Z M5 19 L13 11"/>'),
    "sprout": lambda c: _stroke(c, '<path d="M12 21 V11 M12 11 C12 7 9 5 5 5 C5 9 7 11 12 11 M12 13 C12 9.5 15 8 19 8 C19 12 16 13 12 13"/>'),
    "sun": lambda c: _stroke(c, '<circle cx="12" cy="12" r="4"/><path d="M12 2.5 V5 M12 19 V21.5 M2.5 12 H5 M19 12 H21.5 M5.3 5.3 L7 7 M17 17 L18.7 18.7 M5.3 18.7 L7 17 M17 7 L18.7 5.3"/>'),
    "drop": lambda c: _stroke(c, '<path d="M12 3 C12 3 5.5 10 5.5 14.5 A6.5 6.5 0 0 0 18.5 14.5 C18.5 10 12 3 12 3 Z"/>'),
    "person": lambda c: _stroke(c, '<circle cx="12" cy="8" r="4"/><path d="M4.5 21 C4.5 15.5 8 14 12 14 C16 14 19.5 15.5 19.5 21"/>'),
    "people": lambda c: _stroke(c, '<circle cx="9" cy="8.5" r="3.5"/><path d="M2.5 20 C2.5 15.5 5.5 14 9 14 C12.5 14 15.5 15.5 15.5 20 M16 5.2 A3.5 3.5 0 0 1 16 12 M17.5 14.3 C20 15 21.5 16.5 21.5 20"/>'),
    "chart_bars": lambda c: _stroke(c, '<path d="M4 20 H20 M7 20 V12 M12 20 V5 M17 20 V9"/>'),
    "trend_up": lambda c: _stroke(c, '<path d="M3 17 L9 11 L13 15 L21 6 M15 6 H21 V12"/>'),
    "target": lambda c: _stroke(c, '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>'),
    "shield": lambda c: _stroke(c, '<path d="M12 3 L20 6 V12 C20 16.5 16.5 19.5 12 21 C7.5 19.5 4 16.5 4 12 V6 Z"/>'),
    "key": lambda c: _stroke(c, '<circle cx="8" cy="12" r="4"/><path d="M12 12 H21 M18 12 V16 M21 12 V15"/>'),
    "clock": lambda c: _stroke(c, '<circle cx="12" cy="12" r="9"/><path d="M12 6.5 V12 L16 14"/>'),
    "globe": lambda c: _stroke(c, '<circle cx="12" cy="12" r="9"/><path d="M3 12 H21 M12 3 C8 7 8 17 12 21 M12 3 C16 7 16 17 12 21"/>'),
    "book": lambda c: _stroke(c, '<path d="M4 5 H10 C11.5 5 12 6 12 7 V20 C12 19 11 18 10 18 H4 Z M20 5 H14 C12.5 5 12 6 12 7 V20 C12 19 13 18 14 18 H20 Z"/>'),
    "lightbulb": lambda c: _stroke(c, '<path d="M9 18 H15 M10 21 H14 M12 3 A6 6 0 0 0 8.5 14 C9 15 9 16 9 18 M12 3 A6 6 0 0 1 15.5 14 C15 15 15 16 15 18"/>'),
    "link": lambda c: _stroke(c, '<path d="M10 14 L14 10 M8.5 11.5 L6 14 A3.5 3.5 0 0 0 11 19 L13.5 16.5 M15.5 12.5 L18 10 A3.5 3.5 0 0 0 13 5 L10.5 7.5"/>'),
    "flag": lambda c: _stroke(c, '<path d="M5 21 V4 M5 5 H18 L15 9 L18 13 H5"/>'),
    "pin": lambda c: _stroke(c, '<path d="M12 21 C12 21 5 14.5 5 10 A7 7 0 0 1 19 10 C19 14.5 12 21 12 21 Z"/><circle cx="12" cy="10" r="2.5"/>'),
}

ICON_NAMES: tuple[str, ...] = tuple(_ICONS)


def icon_fragment(name: str, color: str) -> str:
    """SVG fragment (24x24 coordinate space) for ``name`` in ``color``."""
    try:
        return _ICONS[name](color)
    except KeyError:
        raise KeyError(f"unknown icon {name!r}; available: {', '.join(ICON_NAMES)}") from None
