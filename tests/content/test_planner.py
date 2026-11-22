import json

import pytest

from rooted.content import check_coverage, plan_content
from rooted.content import planner as planner_mod
from rooted.content.budget import estimate_capacity
from rooted.content.planner import build_prompt
from rooted.errors import LLMRefusalError, LLMTruncatedError, StageError, TraceabilityError
from rooted.models import (
    Brief,
    ContentDoc,
    DroppedBlock,
    EditPolicy,
    PageType,
    ProvenanceKind,
    PullQuote,
    Rewrite,
    TextExcerpt,
)
from tests.factories import excerpt, sample_brief

CASES = {}


def case(fn):
    CASES[fn.__name__] = fn
    return fn


@case
def clean(plan, doc):
    return plan


@case
def wrong_offsets(plan, doc):
    q = plan.sections[0].pull_quotes[0]
    q.excerpt = TextExcerpt(block_id="b0005", start=2, end=9, text=q.excerpt.text)
    plan.sections[0].key_stats[0].source = TextExcerpt(
        block_id="b0004", start=0, end=3, text=plan.sections[0].key_stats[0].source.text
    )
    return plan


@case
def missing_blocks(plan, doc):
    plan.sections[0].block_ids = ["b0003", "b0004"]
    plan.sections[1].block_ids = ["b0008", "b0010"]
    return plan


@case
def duplicates(plan, doc):
    plan.sections[1].block_ids += ["b0004", "b0003"]
    plan.cover.consumed_block_ids += ["b0008"]
    return plan


@case
def unpermitted_drops_and_rewrites(plan, doc):
    plan.sections[0].block_ids.remove("b0007")
    plan.dropped_blocks.append(DroppedBlock(block_id="b0007", reason="minor"))
    plan.rewrites.append(Rewrite(block_id="b0005", new_text="short", reason="space"))
    return plan


@case
def invented(plan, doc):
    plan.sections[0].block_ids.append("b4242")
    plan.sections[0].pull_quotes.append(
        PullQuote(
            id="q09",
            excerpt=TextExcerpt(block_id="b0005", start=0, end=1, text="Zzz invented"),
            anchor_block_id="b0005",
        )
    )
    return plan


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("policy", [EditPolicy.VERBATIM, EditPolicy.LIGHT_EDIT])
def test_broken_plans_are_repaired(name, policy, fake_llm, content_doc, content_plan, design_spec, brief):
    broken = CASES[name](content_plan.model_copy(deep=True), content_doc)
    brief = brief.model_copy(update={"edit_policy": policy})
    plan = plan_content(content_doc, brief, design_spec, fake_llm({"content_plan": broken}))
    assert check_coverage(plan, content_doc, brief) == []
    assert plan.dropped_blocks == []
    assert plan.rewrites == [] or policy == EditPolicy.LIGHT_EDIT
    for s in plan.sections:
        for q in s.pull_quotes:
            assert content_doc.block(q.excerpt.block_id).text[q.excerpt.start : q.excerpt.end] == q.excerpt.text
    placed = [i for s in plan.sections for i in s.block_ids + [j for c in s.callouts for j in c.block_ids]]
    placed += plan.cover.consumed_block_ids
    assert sorted(placed) == sorted(content_doc.block_ids)


def test_fixture_plan_roundtrips_and_sets_budgets(fake_llm, content_doc, design_spec, brief):
    llm = fake_llm()
    plan = plan_content(content_doc, brief, design_spec, llm)
    assert len(llm.calls) == 1
    c = llm.calls[0]
    assert c.task == "content_plan" and c.effort == "high" and c.output_model == "ContentPlan"
    assert plan.target_pages == 8
    assert sum(s.page_budget for s in plan.sections) == pytest.approx(8 - 2)  # cover + back cover
    assert check_coverage(plan, content_doc, brief) == []


def test_repairs_are_recorded_in_notes(fake_llm, content_doc, content_plan, design_spec, brief):
    broken = missing_blocks(content_plan.model_copy(deep=True), content_doc)
    plan = plan_content(content_doc, brief, design_spec, fake_llm({"content_plan": broken}))
    assert any(n.startswith("repaired:") and "not placed" in n for n in plan.notes)


def test_prompt_has_every_block_and_context(content_doc, design_spec, brief):
    cap = estimate_capacity(design_spec, brief)
    prompt = build_prompt(content_doc, brief, design_spec, cap)
    for bid in content_doc.block_ids:
        assert f"[{bid}|" in prompt
    assert "[b0007|list_item|2w] Housing support" in prompt
    assert "[b0003|heading1|1w] Introduction" in prompt
    assert '"target_pages": 8' in prompt
    assert "edit policy" in prompt.lower() and "verbatim" in prompt
    assert "section_opener" in prompt  # page types available
    assert "words_per_body_page" in prompt
    json.loads(prompt.split("Brief (JSON):\n")[1].split("\n\nContext")[0])  # embedded JSON is valid


def test_prompt_abbreviates_tables_and_is_deterministic(content_doc, design_spec, brief):
    cap = estimate_capacity(design_spec, brief)
    p1 = build_prompt(content_doc, brief, design_spec, cap)
    assert (
        "[b0010|table|8w] 4 rows x 2 cols: Region | Families // North | 5,200 // Central | 4,100 ... (+1 more rows)"
        in p1
    )
    assert p1 == build_prompt(content_doc, brief, design_spec, cap)


def test_prompt_uses_brief_edit_policy(content_doc, design_spec):
    cap = estimate_capacity(design_spec, Brief())
    p = build_prompt(content_doc, Brief(edit_policy=EditPolicy.CONDENSE), design_spec, cap)
    assert "condense: you may drop" in p


def test_condense_keeps_permitted_drops(fake_llm, content_doc, content_plan, design_spec):
    plan = content_plan.model_copy(deep=True)
    plan.sections[0].block_ids.remove("b0007")
    plan.dropped_blocks.append(DroppedBlock(block_id="b0007", reason="minor"))
    brief = sample_brief().model_copy(update={"edit_policy": EditPolicy.CONDENSE})
    out = plan_content(content_doc, brief, design_spec, fake_llm({"content_plan": plan}))
    assert [d.block_id for d in out.dropped_blocks] == ["b0007"]


def test_no_cover_in_brief_removes_cover_and_returns_blocks(fake_llm, content_doc, design_spec, brief):
    brief = brief.model_copy(update={"include_cover": False, "include_back_cover": False})
    plan = plan_content(content_doc, brief, design_spec, fake_llm())
    assert plan.cover is None
    assert plan.sections[0].block_ids[:2] == ["b0001", "b0002"]
    assert sum(s.page_budget for s in plan.sections) == pytest.approx(8)


def test_plan_toc_counts_as_fixed_page(fake_llm, content_doc, content_plan, design_spec, brief):
    p = content_plan.model_copy(update={"include_toc": True})
    plan = plan_content(content_doc, brief, design_spec, fake_llm({"content_plan": p}))
    assert sum(s.page_budget for s in plan.sections) == pytest.approx(8 - 3)


def test_page_types_and_openers_conform_to_design(fake_llm, content_doc, content_plan, design_spec, brief):
    design = design_spec.model_copy(
        update={
            "templates": [
                t for t in design_spec.templates if t.page_type not in (PageType.DATA, PageType.SECTION_OPENER)
            ]
        }
    )
    plan = plan_content(content_doc, brief, design, fake_llm({"content_plan": content_plan}))
    assert all(s.page_type == PageType.BODY and not s.opener for s in plan.sections)
    assert any("not in design" in n for n in plan.notes)


def test_pull_quotes_capped_by_pages(fake_llm, content_doc, content_plan, design_spec, brief):
    p = content_plan.model_copy(deep=True)
    p.sections[1].pull_quotes = [
        PullQuote(id="q02", excerpt=excerpt(content_doc, "b0013", "open two new centers"), anchor_block_id="b0013"),
        PullQuote(id="q03", excerpt=excerpt(content_doc, "b0009", "Central 4,100"), anchor_block_id="b0009"),
    ]
    brief = brief.model_copy(update={"target_pages": 4})  # 2 body pages -> 1 quote
    plan = plan_content(content_doc, brief, design_spec, fake_llm({"content_plan": p}))
    assert sum(len(s.pull_quotes) for s in plan.sections) == 1
    assert any("pull quote dropped" in n for n in plan.notes)


def test_over_budget_note(fake_llm, content_doc, design_spec, brief):
    brief = brief.model_copy(update={"target_pages": 3})  # 1 body page, but only ~100 words: fine
    big = content_doc.model_copy(deep=True)
    big.blocks[3].runs[0].text = "word " * 3000
    plan = plan_content(big, brief, design_spec, fake_llm({"content_plan": _plan_for(big)}))
    assert any("over budget" in n for n in plan.notes)


def _plan_for(doc: ContentDoc):
    from tests.factories import sample_content_plan

    return sample_content_plan(doc)


def test_truncation_retries_with_double_max_tokens(fake_llm, content_doc, content_plan, design_spec, brief):
    seen = []

    def resp(call):
        seen.append(call)
        return content_plan

    llm = fake_llm({"content_plan": [LLMTruncatedError("cut"), resp]})
    plan = plan_content(content_doc, brief, design_spec, llm)
    assert len(llm.calls) == 2 and plan.sections


def test_llm_failure_is_a_stage_error(fake_llm, content_doc, design_spec, brief):
    with pytest.raises(StageError, match="content"):
        plan_content(content_doc, brief, design_spec, fake_llm({"content_plan": LLMRefusalError("no")}))


def test_empty_document_is_a_stage_error(fake_llm, content_doc, design_spec, brief):
    with pytest.raises(StageError):
        plan_content(content_doc.model_copy(update={"blocks": []}), brief, design_spec, fake_llm())


def test_unrepairable_plan_raises_traceability_error(monkeypatch, fake_llm, content_doc, design_spec, brief):
    monkeypatch.setattr(
        planner_mod, "repair_coverage", lambda plan, doc, brief: plan.model_copy(update={"sections": []})
    )
    with pytest.raises(TraceabilityError):
        plan_content(content_doc, brief, design_spec, fake_llm())


def test_verbatim_title_provenance_preserved(fake_llm, content_doc, design_spec, brief):
    plan = plan_content(content_doc, brief, design_spec, fake_llm())
    assert plan.cover.title.provenance == ProvenanceKind.VERBATIM
    assert plan.cover.title.source.text == "Annual Impact Report 2025"
