import pytest

from rooted.content import estimate_capacity
from rooted.content.budget import assign_page_budgets
from rooted.models import Brief, Grid, Margins, PageCapacity, PageType, Section


def test_a4_two_column_10pt_capacity(design_spec):
    design = design_spec.model_copy(update={"grid": Grid(columns=2, gutter_pt=18)})
    cap = estimate_capacity(design, Brief())
    assert 600 <= cap.words_per_body_page <= 900
    assert cap.words_per_opener_page < cap.words_per_body_page
    assert cap.fixed_pages == 2  # cover + back cover templates exist


def test_one_column_has_fewer_words_per_line_but_similar_total_order(design_spec):
    two = estimate_capacity(design_spec, Brief()).words_per_body_page
    one_spec = design_spec.model_copy(update={"grid": Grid(columns=1)})
    one_spec = one_spec.model_copy(
        update={"templates": [t.model_copy(update={"columns": None}) for t in design_spec.templates]}
    )
    one = estimate_capacity(one_spec, Brief()).words_per_body_page
    assert one > two  # no gutter, no ragged half-columns


def test_larger_margins_reduce_capacity(design_spec):
    base = estimate_capacity(design_spec, Brief()).words_per_body_page
    tight = design_spec.model_copy(update={"margins": Margins(top=120, right=100, bottom=120, left=100)})
    assert estimate_capacity(tight, Brief()).words_per_body_page < base


def test_brief_page_size_changes_capacity(design_spec):
    a4 = estimate_capacity(design_spec, Brief()).words_per_body_page
    a5 = estimate_capacity(design_spec, Brief(page_size="A5")).words_per_body_page
    assert a5 < a4


def test_fixed_pages_follow_brief_flags(design_spec):
    cap = estimate_capacity(design_spec, Brief(include_cover=False, include_back_cover=False, include_toc=True))
    assert cap.fixed_pages == 1
    assert estimate_capacity(design_spec, Brief(include_cover=False, include_back_cover=False)).fixed_pages == 0


@pytest.mark.parametrize("target", [3, 8, 20])
def test_budgets_sum_to_target_minus_fixed(content_doc, content_plan, target):
    cap = PageCapacity(words_per_body_page=700, words_per_opener_page=350, fixed_pages=2)
    out = assign_page_budgets(content_plan, content_doc, cap, target)
    assert sum(s.page_budget for s in out.sections) == pytest.approx(target - 2, abs=1e-6)
    assert out.target_pages == target
    assert content_plan.sections[0].page_budget == 3.0  # input untouched


def test_budgets_are_proportional(content_doc, content_plan):
    cap = PageCapacity(words_per_body_page=700, words_per_opener_page=700, fixed_pages=0)
    plan = content_plan.model_copy(deep=True)
    plan.sections = [
        Section(id="s01", block_ids=["b0004"]),  # 15 words
        Section(id="s02", block_ids=["b0004", "b0005", "b0009", "b0013"]),  # more words
    ]
    plan.sections[0].page_type = PageType.BODY
    out = assign_page_budgets(plan, content_doc, cap, 10)
    assert out.sections[1].page_budget > out.sections[0].page_budget
    assert sum(s.page_budget for s in out.sections) == pytest.approx(10)


def test_no_target_gives_natural_length(content_doc, content_plan):
    cap = PageCapacity(words_per_body_page=700, words_per_opener_page=350, fixed_pages=2)
    out = assign_page_budgets(content_plan.model_copy(update={"target_pages": None}), content_doc, cap, None)
    assert all(s.page_budget > 0 for s in out.sections)


def test_target_smaller_than_fixed_pages_noted(content_doc, content_plan):
    cap = PageCapacity(words_per_body_page=700, words_per_opener_page=350, fixed_pages=4)
    out = assign_page_budgets(content_plan, content_doc, cap, 3)
    assert sum(s.page_budget for s in out.sections) == 0
    assert any("no body pages" in n for n in out.notes)
