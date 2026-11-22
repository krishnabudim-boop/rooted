"""Free-text brief -> Brief (WP2)."""

from __future__ import annotations

import logging
import re

from rooted.content._llm import structured_with_retry
from rooted.content.colors import color_by_name, find_colors, normalize_hex
from rooted.content.prompts import BRIEF_SYSTEM
from rooted.errors import LLMError
from rooted.llm import TASK_BRIEF, LLMClient
from rooted.models import Brief, ColorOverride, ColorRole, EditPolicy, VisualsPolicy

log = logging.getLogger("rooted.content.brief")

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
}
_TENS = {"twenty", "thirty", "forty", "fifty"}
_NUM_WORD_RE = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_NUM = rf"(\d{{1,3}}|(?:(?:{_NUM_WORD_RE})(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?))"
_PAGE_UNIT = r"(?:-?\s*pages?|\s*pp\b\.?|\s*pgs?\b\.?|-?\s*pagers?)"
_PAGES_RE = re.compile(rf"\b{_NUM}{_PAGE_UNIT}", re.I)
_RANGE_RE = re.compile(rf"\b{_NUM}\s*(?:-|–|to)\s*{_NUM}\s*(?:pages?|pp\b\.?)", re.I)
_ONE_PAGE_RE = re.compile(r"\b(?:single|one)[- ]?page(?:r)?\b|\bone-pager\b", re.I)

_SIZES = {
    "a3": ("A3", 841.89, 1190.55),
    "a4": ("A4", 595.28, 841.89),
    "a5": ("A5", 419.53, 595.28),
    "a6": ("A6", 297.64, 419.53),
    "letter": ("Letter", 612.0, 792.0),
    "legal": ("Legal", 612.0, 1008.0),
    "tabloid": ("Tabloid", 792.0, 1224.0),
}
_SIZE_RE = re.compile(r"\b(?:us[- ])?(a[3-6]|letter|legal|tabloid)\b", re.I)
_CUSTOM_SIZE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:x|×|by)\s*(\d+(?:\.\d+)?)\s*(in|inch|inches|\"|mm|cm)\b", re.I)

_VERBATIM_RE = re.compile(
    r"keep (?:the |my |all )?(?:wording|text|words|copy)(?: exactly| as[- ]is| unchanged)?"
    r"|(?:do not|don'?t|never) (?:edit|change|alter|rewrite|cut|shorten|condense|trim|drop|remove)"
    r"|\bverbatim\b|\bas[- ]is\b|\bword[- ]for[- ]word\b|\bwithout (?:editing|changes|cuts)\b"
    r"|\bno (?:edits|cuts|changes to (?:the )?(?:text|wording))\b",
    re.I,
)
_CONDENSE_RE = re.compile(
    r"\b(?:may|can|could|feel free to|ok(?:ay)? to|allowed to|permission to|please)\s+(?:\w+\s+){0,2}?"
    r"(?:condense|shorten|trim|cut|abridge|summari[sz]e|tighten)\b|\bcondense (?:it|the text|as needed)\b",
    re.I,
)
_LIGHT_EDIT_RE = re.compile(
    r"\b(?:light(?:ly)? (?:edit|copy[- ]?edit)\w*|fix (?:any )?(?:typos|spelling|grammar)|proofread\w*"
    r"|copy[- ]?edit\w*|clean up (?:the )?(?:text|wording))\b",
    re.I,
)
# Words that must appear in the brief for an LLM-proposed non-verbatim policy to be accepted.
_EDIT_STEMS = re.compile(
    r"condens|shorten|trim|\bcut|edit|tighten|summar|proofread|typo|rewrit|reword|abridg|polish|clean up|fix", re.I
)

_NO_COVER = re.compile(r"\b(?:no|without|skip|omit)\s+(?:a\s+)?cover(?: page)?\b", re.I)
_COVER = re.compile(r"\b(?:with|include|add|needs?|and)\s+(?:a\s+)?cover(?: page)?\b", re.I)
_NO_BACK = re.compile(r"\b(?:no|without|skip|omit)\s+(?:a\s+)?back[- ]cover\b", re.I)
_BACK = re.compile(r"\b(?:with|include|add|needs?|and)\s+(?:a\s+)?back[- ]cover\b", re.I)
_NO_TOC = re.compile(r"\b(?:no|without|skip|omit)\s+(?:a\s+)?(?:toc|table of contents|contents page)\b", re.I)
_TOC = re.compile(r"\b(?:with|include|add|needs?|and)\s+(?:a\s+)?(?:toc|table of contents|contents page)\b", re.I)
_NO_VIS = re.compile(r"\bno (?:charts?|graphs?|graphics|visuals|images|illustrations)\b|\btext[- ]only\b", re.I)
_MIN_VIS = re.compile(r"\b(?:minimal|few|light|sparse) (?:visuals|graphics|charts|imagery)\b", re.I)
_RICH_VIS = re.compile(
    r"\b(?:rich|lots of|plenty of|many|heavy|visual[- ]heavy) (?:visuals|graphics|charts|imagery)\b|\bvisually rich\b",
    re.I,
)
_TONE_RE = re.compile(r"\b([a-z]+)\s+tone\b", re.I)


def _to_int(tok: str) -> int | None:
    tok = tok.lower().strip()
    if tok.isdigit():
        return int(tok)
    parts = re.split(r"[- ]", tok)
    total = 0
    for p in parts:
        if p not in _NUMBER_WORDS:
            return None
        total += _NUMBER_WORDS[p]
    if len(parts) == 2 and parts[0] not in _TENS:
        return None
    return total or None


def parse_page_count(text: str) -> tuple[int | None, str | None]:
    """``(pages, note)``. Ranges ("8-10 pages") take the upper bound and return a note."""
    m = _RANGE_RE.search(text)
    if m:
        lo, hi = _to_int(m.group(1)), _to_int(m.group(2))
        if lo and hi and hi >= lo:
            return hi, f"page range {lo}-{hi} requested; targeting {hi}"
    m = _PAGES_RE.search(text)
    if m:
        n = _to_int(m.group(1))
        if n and n > 0:
            return n, None
    if _ONE_PAGE_RE.search(text):
        return 1, None
    return None, None


def normalize_page_size(text: str) -> str | None:
    """ "A4", "US Letter" -> canonical name; "6 x 9 in" -> "6x9in"; None if absent."""
    m = _CUSTOM_SIZE_RE.search(text)
    if m:
        unit = m.group(3).lower()
        unit = {"in": "in", "inch": "in", "inches": "in", '"': "in"}.get(unit, unit)

        def fmt(v: str) -> str:
            f = float(v)
            return str(int(f)) if f == int(f) else str(f)

        return f"{fmt(m.group(1))}x{fmt(m.group(2))}{unit}"
    m = _SIZE_RE.search(text)
    if m:
        return _SIZES[m.group(1).lower()][0]
    return None


def _regex_brief(text: str) -> tuple[Brief, list[str]]:
    notes: list[str] = []
    pages, note = parse_page_count(text)
    if note:
        notes.append(note)
    brief = Brief(raw_text=text, target_pages=pages, page_size=normalize_page_size(text))
    colors = find_colors(text)
    roles = [ColorRole.PRIMARY, ColorRole.ACCENT]
    brief.palette_overrides = [
        ColorOverride(role=role, hex=hexv, name=None if name.startswith("#") else name)
        for role, (name, hexv) in zip(roles, colors, strict=False)
    ]
    if _VERBATIM_RE.search(text):
        brief.edit_policy = EditPolicy.VERBATIM
    elif _CONDENSE_RE.search(text):
        brief.edit_policy = EditPolicy.CONDENSE
    elif _LIGHT_EDIT_RE.search(text):
        brief.edit_policy = EditPolicy.LIGHT_EDIT
    if _NO_VIS.search(text):
        brief.visuals_policy = VisualsPolicy.NONE
    elif _MIN_VIS.search(text):
        brief.visuals_policy = VisualsPolicy.MINIMAL
    elif _RICH_VIS.search(text):
        brief.visuals_policy = VisualsPolicy.RICH
    brief.include_cover = _tri(text, _COVER, _NO_COVER)
    brief.include_back_cover = _tri(text, _BACK, _NO_BACK)
    brief.include_toc = _tri(text, _TOC, _NO_TOC)
    m = _TONE_RE.search(text)
    if m and m.group(1).lower() not in {"a", "the", "your", "this", "that", "its", "our", "same"}:
        brief.tone = m.group(1).lower()
    return brief, notes


def _tri(text: str, yes: re.Pattern, no: re.Pattern) -> bool | None:
    if no.search(text):
        return False
    if yes.search(text):
        return True
    return None


def _has_explicit_edit_signal(text: str) -> bool:
    return bool(_VERBATIM_RE.search(text) or _CONDENSE_RE.search(text) or _LIGHT_EDIT_RE.search(text))


def _normalize_overrides(overrides: list[ColorOverride], text: str) -> list[ColorOverride]:
    out: list[ColorOverride] = []
    lowered = text.lower()
    for o in overrides:
        literal = normalize_hex(o.hex)
        hexv = None
        if literal and o.hex.lstrip("#").lower() in lowered:
            hexv = literal  # the brief itself gave this exact hex
        if hexv is None and o.name:
            hexv = color_by_name(o.name)
        if hexv is None:
            hexv = literal or color_by_name(o.hex)
        if hexv is None:
            log.warning("dropping unusable color override %r/%r", o.name, o.hex)
            continue
        out.append(ColorOverride(role=o.role, hex=hexv, name=o.name))
    return out


def parse_brief(text: str, llm: LLMClient | None) -> Brief:
    """Interpret the brief with ``llm.structured(task=TASK_BRIEF, output_model=Brief)``.

    Deterministic pre/post-processing: regex for page counts ("8 pages",
    "8pp", "eight pages"), page sizes ("A4", "US Letter"), a CSS/X11 color-name
    table to normalize LLM color names to hex. If ``llm`` is None (dry run) or
    ``text`` is blank, return the regex-only Brief. Sets ``raw_text``.
    """
    text = text or ""
    if not text.strip():
        return Brief(raw_text=text)
    rx, notes = _regex_brief(text)
    for n in notes:
        log.info(n)
    if llm is None:
        rx.extra_constraints = list(notes)
        return rx

    try:
        got = structured_with_retry(
            llm,
            task=TASK_BRIEF,
            system=BRIEF_SYSTEM,
            prompt=_brief_prompt(text),
            output_model=Brief,
            effort="low",
        )
    except LLMError as e:
        log.warning("brief: LLM unavailable (%s); using regex-only brief", e)
        rx.extra_constraints = list(notes)
        return rx

    brief = got.model_copy(deep=True)
    brief.raw_text = text

    # Page count: deterministic value wins; the LLM may not invent one.
    if rx.target_pages is not None:
        if brief.target_pages not in (None, rx.target_pages):
            log.warning(
                "brief: LLM page count %s conflicts with regex %s; using %s",
                brief.target_pages,
                rx.target_pages,
                rx.target_pages,
            )
        brief.target_pages = rx.target_pages
    elif brief.target_pages is not None and not re.search(rf"\d|\b(?:{_NUM_WORD_RE})\b", text, re.I):
        log.warning("brief: dropping LLM page count %s (brief states no number)", brief.target_pages)
        brief.target_pages = None
    if brief.target_pages is not None and brief.target_pages < 1:
        brief.target_pages = None

    # Page size.
    if rx.page_size:
        if brief.page_size and brief.page_size != rx.page_size:
            log.warning("brief: LLM page size %r conflicts with regex %r", brief.page_size, rx.page_size)
        brief.page_size = rx.page_size
    elif brief.page_size:
        brief.page_size = normalize_page_size(brief.page_size) or brief.page_size

    # Palette.
    brief.palette_overrides = _normalize_overrides(brief.palette_overrides, text)
    if not brief.palette_overrides:
        brief.palette_overrides = rx.palette_overrides

    # Edit policy: never more permissive than the brief's own words.
    if _has_explicit_edit_signal(text):
        if brief.edit_policy != rx.edit_policy:
            log.warning(
                "brief: LLM edit_policy %s conflicts with regex %s", brief.edit_policy.value, rx.edit_policy.value
            )
        brief.edit_policy = rx.edit_policy
    elif brief.edit_policy != EditPolicy.VERBATIM and not _EDIT_STEMS.search(text):
        log.warning(
            "brief: LLM proposed edit_policy=%s without support in the brief; using verbatim", brief.edit_policy.value
        )
        brief.edit_policy = EditPolicy.VERBATIM

    # Explicit regex matches for booleans and visuals override the LLM.
    for name in ("include_cover", "include_back_cover", "include_toc"):
        if getattr(rx, name) is not None:
            setattr(brief, name, getattr(rx, name))
    if rx.visuals_policy != VisualsPolicy.NORMAL:
        brief.visuals_policy = rx.visuals_policy
    if brief.tone is None:
        brief.tone = rx.tone
    for n in notes:
        if n not in brief.extra_constraints:
            brief.extra_constraints.append(n)
    return brief


def _brief_prompt(text: str) -> str:
    return f"Brief:\n<brief>\n{text}\n</brief>\n\nExtract the constraints into the schema."
