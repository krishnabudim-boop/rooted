import logging

import pytest

from rooted.content import parse_brief
from rooted.content.brief import normalize_page_size, parse_page_count
from rooted.errors import LLMRefusalError, LLMValidationError
from rooted.models import Brief, ColorRole, EditPolicy, VisualsPolicy


@pytest.mark.parametrize(
    "text,expected",
    [
        ("8 pages", 8),
        ("eight-page report", 8),
        ("make it 8pp", 8),
        ("Eight pages please", 8),
        ("twenty-four pages", 24),
        ("12 pg", 12),
        ("a one-pager", 1),
        ("single page", 1),
        ("8-10 pages", 10),
        ("about 16 page report", 16),
        ("navy and gold", None),
        ("page size A4", None),
    ],
)
def test_page_counts(text, expected):
    assert parse_page_count(text)[0] == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("A4", "A4"),
        ("use a4 paper", "A4"),
        ("US Letter", "Letter"),
        ("letter size", "Letter"),
        ("legal", "Legal"),
        ("6x9in book", "6x9in"),
        ("6 x 9 inches", "6x9in"),
        ("210 x 297 mm", "210x297mm"),
        ("nothing here", None),
    ],
)
def test_page_sizes(text, expected):
    assert normalize_page_size(text) == expected


def test_regex_only_brief_without_llm():
    b = parse_brief("8 pages, A4, navy and gold, executive tone", None)
    assert b.target_pages == 8
    assert b.page_size == "A4"
    assert b.raw_text == "8 pages, A4, navy and gold, executive tone"
    roles = {o.role: o for o in b.palette_overrides}
    assert roles[ColorRole.PRIMARY].hex == "#1F2A44" and roles[ColorRole.PRIMARY].name == "navy"
    assert roles[ColorRole.ACCENT].hex == "#C9A227"
    assert b.tone == "executive"
    assert b.edit_policy == EditPolicy.VERBATIM


def test_blank_brief_makes_no_llm_call(fake_llm):
    llm = fake_llm()
    b = parse_brief("   ", llm)
    assert b.target_pages is None and llm.calls == []


def test_hex_and_negated_colors():
    b = parse_brief("use #ff8800 as the main color, no red", None)
    assert [o.hex for o in b.palette_overrides] == ["#FF8800"]


@pytest.mark.parametrize(
    "text,policy",
    [
        ("keep the wording exactly", EditPolicy.VERBATIM),
        ("you may condense the text", EditPolicy.CONDENSE),
        ("feel free to shorten it", EditPolicy.CONDENSE),
        ("please fix typos", EditPolicy.LIGHT_EDIT),
        ("don't edit anything, but you may condense", EditPolicy.VERBATIM),
        ("a plain brief", EditPolicy.VERBATIM),
    ],
)
def test_edit_policy_regex(text, policy):
    assert parse_brief(text, None).edit_policy == policy


def test_visuals_and_cover_flags():
    b = parse_brief("no charts, without a cover, include a table of contents", None)
    assert b.visuals_policy == VisualsPolicy.NONE
    assert b.include_cover is False and b.include_toc is True


def test_llm_brief_uses_fixture_and_sets_raw_text(fake_llm):
    llm = fake_llm()
    b = parse_brief("8 pages, navy and gold, executive tone", llm)
    assert llm.calls[0].task == "brief" and llm.calls[0].effort == "low"
    assert b.raw_text == "8 pages, navy and gold, executive tone"
    assert b.target_pages == 8 and b.tone == "executive"


def test_regex_page_count_wins_and_conflict_logged(fake_llm, caplog):
    llm = fake_llm({"brief": Brief(target_pages=12)})
    with caplog.at_level(logging.WARNING, logger="rooted.content.brief"):
        b = parse_brief("keep it to 6 pages", llm)
    assert b.target_pages == 6
    assert "conflicts" in caplog.text


def test_llm_cannot_invent_page_count(fake_llm):
    llm = fake_llm({"brief": Brief(target_pages=10)})
    assert parse_brief("something short and punchy", llm).target_pages is None


def test_llm_may_infer_from_number_words(fake_llm):
    llm = fake_llm({"brief": Brief(target_pages=2)})
    assert parse_brief("a quick two page summary", llm).target_pages == 2


def test_llm_color_names_normalized_to_hex(fake_llm):
    llm = fake_llm(
        {
            "brief": {
                "palette_overrides": [
                    {"role": "primary", "hex": "navy", "name": "navy"},
                    {"role": "accent", "hex": "#abc", "name": None},
                    {"role": "secondary", "hex": "not a color", "name": "zzz"},
                ]
            }
        }
    )
    b = parse_brief("brand colors navy and abc", llm)
    assert [(o.role, o.hex) for o in b.palette_overrides] == [
        (ColorRole.PRIMARY, "#1F2A44"),
        (ColorRole.ACCENT, "#AABBCC"),
    ]


def test_llm_edit_policy_needs_support_in_brief(fake_llm):
    llm = fake_llm({"brief": {"edit_policy": "condense"}})
    assert parse_brief("make it pretty", llm).edit_policy == EditPolicy.VERBATIM
    llm = fake_llm({"brief": {"edit_policy": "condense"}})
    assert parse_brief("tighten up the prose where needed", llm).edit_policy == EditPolicy.CONDENSE


def test_regex_edit_signal_beats_llm(fake_llm):
    llm = fake_llm({"brief": {"edit_policy": "condense"}})
    assert parse_brief("keep wording exactly", llm).edit_policy == EditPolicy.VERBATIM


def test_refusal_degrades_to_regex(fake_llm):
    llm = fake_llm({"brief": LLMRefusalError("no")})
    b = parse_brief("8 pages navy", llm)
    assert b.target_pages == 8 and b.palette_overrides[0].name == "navy"


def test_validation_error_retries_once_with_error_in_prompt(fake_llm):
    llm = fake_llm({"brief": [LLMValidationError("bad json"), {"target_pages": 3}]})
    b = parse_brief("3 pages", llm)
    assert b.target_pages == 3
    assert len(llm.calls) == 2 and "failed validation: " in llm.calls[1].prompt
