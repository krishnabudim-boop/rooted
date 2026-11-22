"""Color-name table for brief parsing (WP2).

``CSS_COLORS`` is the CSS named-color set. ``CURATED`` overrides or adds
design-friendly names (navy, gold, forest...) that read better in a report than
the harsh CSS values. ``DETECTABLE`` is the subset of names that is safe to
detect inside free-form brief text (words like "snow" or "peru" are excluded).
"""

from __future__ import annotations

import re

CSS_COLORS: dict[str, str] = {
    "aliceblue": "#F0F8FF",
    "antiquewhite": "#FAEBD7",
    "aqua": "#00FFFF",
    "aquamarine": "#7FFFD4",
    "azure": "#F0FFFF",
    "beige": "#F5F5DC",
    "bisque": "#FFE4C4",
    "black": "#000000",
    "blanchedalmond": "#FFEBCD",
    "blue": "#0000FF",
    "blueviolet": "#8A2BE2",
    "brown": "#A52A2A",
    "burlywood": "#DEB887",
    "cadetblue": "#5F9EA0",
    "chartreuse": "#7FFF00",
    "chocolate": "#D2691E",
    "coral": "#FF7F50",
    "cornflowerblue": "#6495ED",
    "cornsilk": "#FFF8DC",
    "crimson": "#DC143C",
    "cyan": "#00FFFF",
    "darkblue": "#00008B",
    "darkcyan": "#008B8B",
    "darkgoldenrod": "#B8860B",
    "darkgray": "#A9A9A9",
    "darkgreen": "#006400",
    "darkgrey": "#A9A9A9",
    "darkkhaki": "#BDB76B",
    "darkmagenta": "#8B008B",
    "darkolivegreen": "#556B2F",
    "darkorange": "#FF8C00",
    "darkorchid": "#9932CC",
    "darkred": "#8B0000",
    "darksalmon": "#E9967A",
    "darkseagreen": "#8FBC8F",
    "darkslateblue": "#483D8B",
    "darkslategray": "#2F4F4F",
    "darkslategrey": "#2F4F4F",
    "darkturquoise": "#00CED1",
    "darkviolet": "#9400D3",
    "deeppink": "#FF1493",
    "deepskyblue": "#00BFFF",
    "dimgray": "#696969",
    "dimgrey": "#696969",
    "dodgerblue": "#1E90FF",
    "firebrick": "#B22222",
    "floralwhite": "#FFFAF0",
    "forestgreen": "#228B22",
    "fuchsia": "#FF00FF",
    "gainsboro": "#DCDCDC",
    "ghostwhite": "#F8F8FF",
    "gold": "#FFD700",
    "goldenrod": "#DAA520",
    "green": "#008000",
    "greenyellow": "#ADFF2F",
    "grey": "#808080",
    "honeydew": "#F0FFF0",
    "hotpink": "#FF69B4",
    "indianred": "#CD5C5C",
    "indigo": "#4B0082",
    "ivory": "#FFFFF0",
    "khaki": "#F0E68C",
    "lavender": "#E6E6FA",
    "lavenderblush": "#FFF0F5",
    "lawngreen": "#7CFC00",
    "lemonchiffon": "#FFFACD",
    "lightblue": "#ADD8E6",
    "lightcoral": "#F08080",
    "lightcyan": "#E0FFFF",
    "lightgoldenrodyellow": "#FAFAD2",
    "lightgray": "#D3D3D3",
    "lightgreen": "#90EE90",
    "lightgrey": "#D3D3D3",
    "lightpink": "#FFB6C1",
    "lightsalmon": "#FFA07A",
    "lightseagreen": "#20B2AA",
    "lightskyblue": "#87CEFA",
    "lightslategray": "#778899",
    "lightslategrey": "#778899",
    "lightsteelblue": "#B0C4DE",
    "lightyellow": "#FFFFE0",
    "lime": "#00FF00",
    "limegreen": "#32CD32",
    "linen": "#FAF0E6",
    "magenta": "#FF00FF",
    "maroon": "#800000",
    "mediumaquamarine": "#66CDAA",
    "mediumblue": "#0000CD",
    "mediumorchid": "#BA55D3",
    "mediumpurple": "#9370DB",
    "mediumseagreen": "#3CB371",
    "mediumslateblue": "#7B68EE",
    "mediumspringgreen": "#00FA9A",
    "mediumturquoise": "#48D1CC",
    "mediumvioletred": "#C71585",
    "midnightblue": "#191970",
    "mintcream": "#F5FFFA",
    "mistyrose": "#FFE4E1",
    "moccasin": "#FFE4B5",
    "navajowhite": "#FFDEAD",
    "navy": "#000080",
    "oldlace": "#FDF5E6",
    "olive": "#808000",
    "olivedrab": "#6B8E23",
    "orange": "#FFA500",
    "orangered": "#FF4500",
    "orchid": "#DA70D6",
    "palegoldenrod": "#EEE8AA",
    "palegreen": "#98FB98",
    "paleturquoise": "#AFEEEE",
    "palevioletred": "#DB7093",
    "papayawhip": "#FFEFD5",
    "peachpuff": "#FFDAB9",
    "peru": "#CD853F",
    "pink": "#FFC0CB",
    "plum": "#DDA0DD",
    "powderblue": "#B0E0E6",
    "purple": "#800080",
    "rebeccapurple": "#663399",
    "red": "#FF0000",
    "rosybrown": "#BC8F8F",
    "royalblue": "#4169E1",
    "saddlebrown": "#8B4513",
    "salmon": "#FA8072",
    "sandybrown": "#F4A460",
    "seagreen": "#2E8B57",
    "seashell": "#FFF5EE",
    "sienna": "#A0522D",
    "silver": "#C0C0C0",
    "skyblue": "#87CEEB",
    "slateblue": "#6A5ACD",
    "slategray": "#708090",
    "slategrey": "#708090",
    "snow": "#FFFAFA",
    "springgreen": "#00FF7F",
    "steelblue": "#4682B4",
    "tan": "#D2B48C",
    "teal": "#008080",
    "thistle": "#D8BFD8",
    "tomato": "#FF6347",
    "turquoise": "#40E0D0",
    "violet": "#EE82EE",
    "wheat": "#F5DEB3",
    "white": "#FFFFFF",
    "whitesmoke": "#F5F5F5",
    "yellow": "#FFFF00",
    "yellowgreen": "#9ACD32",
    "gray": "#808080",
}

CURATED: dict[str, str] = {
    "navy": "#1F2A44",
    "gold": "#C9A227",
    "forest": "#1F5C3A",
    "teal": "#127C7C",
    "burgundy": "#7A1F3D",
    "charcoal": "#36454F",
    "cream": "#F5F0E1",
    "sage": "#9CAF88",
    "emerald": "#1B7F5C",
    "mustard": "#D4A017",
    "rust": "#B7410E",
    "slate": "#4A5568",
    "royal blue": "#2B4C9B",
    "sky blue": "#87CEEB",
    "mint": "#98D8C8",
    "blush": "#F4C7C3",
    "terracotta": "#C8553D",
    "copper": "#B87333",
    "bronze": "#8C6A2F",
    "plum": "#6B2D5C",
    "ochre": "#CC7722",
    "midnight": "#191970",
    "red": "#C62828",
    "blue": "#1F4FA3",
    "green": "#2E7D32",
    "orange": "#EF6C00",
    "yellow": "#F2C200",
    "purple": "#5E35B1",
    "pink": "#D81B60",
    "black": "#111111",
    "white": "#FFFFFF",
    "grey": "#6B7280",
    "gray": "#6B7280",
}

# Names that may appear as ordinary words are excluded from free-text detection.
_AMBIGUOUS = {
    "snow",
    "linen",
    "peru",
    "wheat",
    "tan",
    "lime",
    "bisque",
    "chocolate",
    "coral",
    "salmon",
    "ivory",
    "azure",
    "khaki",
    "orchid",
    "sienna",
    "tomato",
    "violet",
    "lavender",
    "beige",
    "aqua",
    "cyan",
    "magenta",
    "fuchsia",
    "silver",
    "maroon",
    "olive",
    "indigo",
    "crimson",
    "turquoise",
}
_BASIC = {
    "silver",
    "maroon",
    "olive",
    "indigo",
    "crimson",
    "turquoise",
    "lavender",
    "beige",
    "magenta",
    "violet",
    "coral",
    "ivory",
}

COLOR_TABLE: dict[str, str] = {**CSS_COLORS, **CURATED}
DETECTABLE: set[str] = (set(CURATED) | set(CSS_COLORS)) - (_AMBIGUOUS - _BASIC)

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalize_hex(value: str) -> str | None:
    """``"#abc"`` / ``"1F2A44"`` -> ``"#AABBCC"`` / ``"#1F2A44"``; None if invalid."""
    m = _HEX_RE.match(value.strip())
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return "#" + h.upper()


def color_by_name(name: str) -> str | None:
    """Look up a color name (case/space/hyphen-insensitive), also accepts hex."""
    key = re.sub(r"[\s_-]+", " ", name.strip().lower())
    if key in COLOR_TABLE:
        return COLOR_TABLE[key]
    squashed = key.replace(" ", "")
    if squashed in COLOR_TABLE:
        return COLOR_TABLE[squashed]
    return normalize_hex(name)


def find_colors(text: str) -> list[tuple[str, str]]:
    """Colors mentioned in ``text`` in order of appearance: ``[(name, hex), ...]``.

    Colors that are negated ("no red", "avoid red", "without red") are skipped.
    Multi-word names ("royal blue", "dark green") are matched first.
    """
    names = sorted(
        DETECTABLE | {n for n in COLOR_TABLE if n.startswith(("dark", "light"))} | set(CURATED), key=len, reverse=True
    )
    alt = "|".join(re.escape(n) for n in names)
    pat = re.compile(
        rf"(#[0-9a-fA-F]{{6}}\b|#[0-9a-fA-F]{{3}}\b)|\b((?:dark|light|deep|pale)[ -](?:{alt})|{alt})\b", re.I
    )
    found: list[tuple[str, str]] = []
    for m in pat.finditer(text):
        before = text[max(0, m.start() - 24) : m.start()].lower()
        if re.search(r"\b(no|not|avoid|without|except|never|don'?t use)\s+(\w+\s+)?$", before):
            continue
        if m.group(1):
            found.append((m.group(1).upper(), normalize_hex(m.group(1)) or ""))
            continue
        name = m.group(2).lower()
        hexv = color_by_name(name)
        if hexv is None:
            base = re.sub(r"^(deep|pale)[ -]", "", name)
            hexv = color_by_name(base)
        if hexv and all(hexv != h for _, h in found):
            found.append((name, hexv))
    return found
