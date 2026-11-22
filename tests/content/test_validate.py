import pytest

from rooted.content import check_coverage, repair_excerpts
from rooted.content.validate import repair_coverage
from rooted.models import (
    Brief,
    Callout,
    DroppedBlock,
    EditPolicy,
    KeyStat,
    PlannedText,
    ProvenanceKind,
    PullQuote,
    Rewrite,
    Section,
    TextExcerpt,
)
from tests.factories import sample_content_doc, verbatim

VERBATIM = Brief(edit_policy=EditPolicy.VERBATIM)
CONDENSE = Brief(edit_policy=EditPolicy.CONDENSE)


def test_sample_plan_is_clean(content_doc, content_plan):
    assert check_coverage(content_plan, content_doc, VERBATIM) == []
    plan, warnings = repair_excerpts(content_plan, content_doc)
    assert warnings == [] and plan == content_plan


# ---- excerpts ----


def test_wrong_offsets_are_fixed(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    q = plan.sections[0].pull_quotes[0]
    q.excerpt = TextExcerpt(block_id="b0005", start=0, end=5, text=q.excerpt.text)
    fixed, warnings = repair_excerpts(plan, content_doc)
    ex = fixed.sections[0].pull_quotes[0].excerpt
    assert content_doc.block("b0005").text[ex.start : ex.end] == ex.text
    assert warnings and check_coverage(fixed, content_doc, VERBATIM) == []


def test_curly_quotes_and_whitespace_are_normalized(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    doc = content_doc.model_copy(deep=True)
    doc.blocks[4].runs[0].text = "Our mission is to “help every  family” put down roots."
    q = plan.sections[0].pull_quotes[0]
    q.excerpt = TextExcerpt(block_id="b0005", start=0, end=3, text='"help every family"')
    fixed, _ = repair_excerpts(plan, doc)
    ex = fixed.sections[0].pull_quotes[0].excerpt
    assert ex.text == "“help every  family”"  # verbatim from the source, not the LLM's version
    assert doc.block("b0005").text[ex.start : ex.end] == ex.text


def test_excerpt_found_in_other_block_is_repointed(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    q = plan.sections[0].pull_quotes[0]
    q.excerpt = TextExcerpt(block_id="b0004", start=0, end=1, text=q.excerpt.text)
    fixed, warnings = repair_excerpts(plan, content_doc)
    assert fixed.sections[0].pull_quotes[0].excerpt.block_id == "b0005"
    assert any("moved" in w for w in warnings)


def test_invented_pull_quote_and_stat_are_dropped(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    s = plan.sections[0]
    s.pull_quotes.append(
        PullQuote(
            id="q02",
            excerpt=TextExcerpt(block_id="b0005", start=0, end=9, text="We love bananas"),
            anchor_block_id="b0005",
        )
    )
    s.key_stats.append(
        KeyStat(
            id="k02",
            value="99%",
            label=PlannedText(text="invented", provenance=ProvenanceKind.GENERATED_LABEL),
            source=TextExcerpt(block_id="b0004", start=0, end=4, text="up 99% from"),
            anchor_block_id="b0004",
        )
    )
    fixed, warnings = repair_excerpts(plan, content_doc)
    assert [q.id for q in fixed.sections[0].pull_quotes] == ["q01"]
    assert [k.id for k in fixed.sections[0].key_stats] == ["k01"]
    assert len(warnings) == 2


def test_stat_value_must_be_in_source(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    plan.sections[0].key_stats[0].value = "77%"
    fixed, warnings = repair_excerpts(plan, content_doc)
    assert fixed.sections[0].key_stats == [] and any("not in its source" in w for w in warnings)


def test_unfindable_verbatim_title_downgrades_or_falls_back(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    plan.sections[0].title = PlannedText(
        text="Totally new words here",
        provenance=ProvenanceKind.VERBATIM,
        source=TextExcerpt(block_id="b0003", start=0, end=3, text="Totally new words here"),
    )
    plan.cover.title = PlannedText(text="x " * 30, provenance=ProvenanceKind.VERBATIM, source=None)
    fixed, _ = repair_excerpts(plan, content_doc)
    assert fixed.sections[0].title.provenance == ProvenanceKind.GENERATED_LABEL
    assert fixed.cover.title.text == "Annual Impact Report 2025"
    assert check_coverage(fixed, content_doc, VERBATIM) == []


def test_overlong_generated_label_dropped(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    plan.sections[1].title = PlannedText(
        text="a very long generated label that exceeds eight words", provenance=ProvenanceKind.GENERATED_LABEL
    )
    fixed, _ = repair_excerpts(plan, content_doc)
    assert fixed.sections[1].title is None


# ---- coverage ----


def _violations(plan, doc=None, brief=VERBATIM):
    return check_coverage(plan, doc or sample_content_doc(), brief)


def test_missing_block_detected_and_reinserted_in_order(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    plan.sections[0].block_ids.remove("b0005")
    assert any("b0005" in v for v in _violations(plan))
    fixed = repair_coverage(plan, content_doc, VERBATIM)
    assert fixed.sections[0].block_ids == ["b0003", "b0004", "b0005", "b0006", "b0007"]
    assert check_coverage(fixed, content_doc, VERBATIM) == []


def test_orphan_goes_after_nearest_preceding_placed_block(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    plan.sections[1].block_ids.remove("b0009")  # between b0008 (heading, s02) and b0010
    fixed = repair_coverage(plan, content_doc, VERBATIM)
    assert fixed.sections[1].block_ids == ["b0008", "b0009", "b0010", "b0012", "b0013"]


def test_orphan_after_callout_block_joins_callout_section(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    plan.sections[1].block_ids.remove("b0012")  # preceded by b0011 which sits in a callout of s02
    fixed = repair_coverage(plan, content_doc, VERBATIM)
    assert "b0012" in fixed.sections[1].block_ids


def test_orphan_with_no_predecessor_goes_to_first_section(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    plan.cover.consumed_block_ids = []  # b0001/b0002 orphaned; nothing precedes them
    fixed = repair_coverage(plan, content_doc, VERBATIM)
    assert fixed.sections[0].block_ids[:2] == ["b0001", "b0002"]
    assert check_coverage(fixed, content_doc, VERBATIM) == []


def test_duplicates_keep_first_occurrence(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    plan.sections[1].block_ids.append("b0004")
    plan.cover.consumed_block_ids.append("b0004")
    full = content_doc.block("b0004").text
    plan.cover.date = PlannedText(
        text=full, provenance=ProvenanceKind.VERBATIM,
        source=TextExcerpt(block_id="b0004", start=0, end=len(full), text=full),
    )  # the cover shows all of b0004, so it may leave the body
    assert any("twice" in v for v in _violations(plan))
    fixed = repair_coverage(plan, content_doc, VERBATIM)
    assert fixed.cover.consumed_block_ids == ["b0001", "b0002", "b0004"]
    assert "b0004" not in fixed.sections[0].block_ids and "b0004" not in fixed.sections[1].block_ids
    assert check_coverage(fixed, content_doc, VERBATIM) == []


def test_callout_beats_section_list_inside_same_section(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    plan.sections[1].block_ids.insert(3, "b0011")  # also in callout c01
    fixed = repair_coverage(plan, content_doc, VERBATIM)
    assert "b0011" not in fixed.sections[1].block_ids and fixed.sections[1].callouts[0].block_ids == ["b0011"]


def test_invented_ids_removed(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    plan.sections[0].block_ids.append("b9999")
    plan.sections[1].callouts.append(Callout(id="c02", kind="note", block_ids=["b8888"]))
    plan.sections[0].heading_block_id = "b7777"
    plan.cover.hero_image_block_id = "b0004"  # exists but is not an image
    assert len(_violations(plan)) >= 3
    fixed = repair_coverage(plan, content_doc, VERBATIM)
    assert check_coverage(fixed, content_doc, VERBATIM) == []
    assert [c.id for c in fixed.sections[1].callouts] == ["c01"]


def test_unpermitted_drops_are_reverted_under_verbatim(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    plan.sections[0].block_ids.remove("b0005")
    plan.dropped_blocks.append(DroppedBlock(block_id="b0005", reason="redundant"))
    assert any("dropped" in v for v in _violations(plan))
    fixed = repair_coverage(plan, content_doc, VERBATIM)
    assert fixed.dropped_blocks == [] and "b0005" in fixed.sections[0].block_ids
    assert check_coverage(fixed, content_doc, VERBATIM) == []


def test_drops_are_reverted_under_light_edit_but_kept_under_condense(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    plan.sections[0].block_ids.remove("b0005")
    plan.dropped_blocks.append(DroppedBlock(block_id="b0005", reason="redundant"))
    light = Brief(edit_policy=EditPolicy.LIGHT_EDIT)
    assert repair_coverage(plan, content_doc, light).dropped_blocks == []
    assert check_coverage(plan, content_doc, CONDENSE) == []
    kept = repair_coverage(plan, content_doc, CONDENSE)
    assert [d.block_id for d in kept.dropped_blocks] == ["b0005"]
    assert "b0005" not in kept.sections[0].block_ids


def test_block_both_dropped_and_placed_is_a_duplicate(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    plan.dropped_blocks.append(DroppedBlock(block_id="b0005", reason="x"))
    assert any("twice" in v for v in _violations(plan, brief=CONDENSE))
    assert check_coverage(repair_coverage(plan, content_doc, CONDENSE), content_doc, CONDENSE) == []


def test_rewrites_rejected_under_verbatim_only(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    plan.rewrites.append(Rewrite(block_id="b0005", new_text="Our mission: stable homes.", reason="shorter"))
    assert any("rewrite" in v for v in _violations(plan))
    assert repair_coverage(plan, content_doc, VERBATIM).rewrites == []
    assert _violations(plan, brief=CONDENSE) == []
    assert len(repair_coverage(plan, content_doc, CONDENSE).rewrites) == 1


def test_rewritten_planned_text_reverted_under_verbatim(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    plan.sections[0].title = PlannedText(
        text="Intro", provenance=ProvenanceKind.REWRITTEN, source=verbatim(content_doc, "b0003", "Introduction").source
    )
    assert any("rewritten" in v for v in _violations(plan))
    fixed = repair_coverage(plan, content_doc, VERBATIM)
    assert fixed.sections[0].title.text == "Introduction"
    assert check_coverage(fixed, content_doc, VERBATIM) == []
    assert _violations(plan, brief=CONDENSE) == []


def test_out_of_order_blocks_sorted(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    plan.sections[0].block_ids = ["b0003", "b0005", "b0004", "b0006", "b0007"]
    assert any("order" in v for v in _violations(plan))
    fixed = repair_coverage(plan, content_doc, VERBATIM)
    assert fixed.sections[0].block_ids == ["b0003", "b0004", "b0005", "b0006", "b0007"]


def test_empty_paragraphs_may_be_dropped_even_under_verbatim(content_doc, content_plan):
    doc = content_doc.model_copy(deep=True)
    from rooted.models import Block, BlockType

    doc.blocks.insert(4, Block(id="b0004a", type=BlockType.PARAGRAPH, runs=[]))
    assert check_coverage(content_plan, doc, VERBATIM) == []  # unplaced empty paragraph is fine
    plan = content_plan.model_copy(deep=True)
    plan.dropped_blocks.append(DroppedBlock(block_id="b0004a", reason="empty"))
    assert check_coverage(plan, doc, VERBATIM) == []
    assert repair_coverage(plan, doc, VERBATIM).dropped_blocks


def test_section_left_empty_is_removed(content_doc, content_plan):
    plan = content_plan.model_copy(deep=True)
    plan.sections.append(Section(id="s03", block_ids=["b0003"]))  # all duplicates of s01
    fixed = repair_coverage(plan, content_doc, VERBATIM)
    assert [s.id for s in fixed.sections] == ["s01", "s02"]


def test_plan_with_no_sections_gets_one(content_doc, content_plan):
    plan = content_plan.model_copy(update={"sections": [], "cover": None})
    fixed = repair_coverage(plan, content_doc, VERBATIM)
    assert len(fixed.sections) == 1 and fixed.sections[0].block_ids == content_doc.block_ids


@pytest.mark.parametrize("seed", range(20))
def test_random_corruption_always_repairs(content_doc, content_plan, seed):
    import random

    rnd = random.Random(seed)
    plan = content_plan.model_copy(deep=True)
    ids = content_doc.block_ids
    for s in plan.sections:
        rnd.shuffle(s.block_ids)
        s.block_ids = [i for i in s.block_ids if rnd.random() > 0.3] + [
            rnd.choice(ids),
            "b" + str(rnd.randint(5000, 9000)),
        ]
    plan.cover.consumed_block_ids += [rnd.choice(ids)]
    plan.dropped_blocks.append(DroppedBlock(block_id=rnd.choice(ids), reason="x"))
    fixed, _ = repair_excerpts(plan, content_doc)
    fixed = repair_coverage(fixed, content_doc, VERBATIM)
    assert check_coverage(fixed, content_doc, VERBATIM) == []
    placed = [i for s in fixed.sections for i in s.block_ids] + [
        i for s in fixed.sections for c in s.callouts for i in c.block_ids
    ]
    placed += fixed.cover.consumed_block_ids if fixed.cover else []
    assert sorted(placed) == sorted(ids)


def test_cover_excerpt_of_longer_block_is_not_consumed(content_doc, content_plan):
    """A cover title that is only part of its block must not swallow the rest of that block."""
    plan = content_plan.model_copy(deep=True)
    b = content_doc.block("b0001")
    part = b.text.split()[0]
    plan.cover.title = PlannedText(
        text=part, provenance=ProvenanceKind.VERBATIM,
        source=TextExcerpt(block_id="b0001", start=0, end=len(part), text=part),
    )
    assert any("b0001" in v and "cover" in v for v in _violations(plan))
    fixed = repair_coverage(plan, content_doc, VERBATIM)
    assert "b0001" not in fixed.cover.consumed_block_ids
    assert "b0001" in [i for s in fixed.sections for i in s.block_ids]
    assert check_coverage(fixed, content_doc, VERBATIM) == []
