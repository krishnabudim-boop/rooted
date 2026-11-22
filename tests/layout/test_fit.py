"""Fit loop tests with a fake renderer (no browser)."""

from __future__ import annotations

import pytest

from rooted.layout.fit import KNOBS, density_to_params, fit_to_pages
from rooted.models import FitBounds, FitParams, LayoutElement, LayoutPlan, LayoutUnit, PageType, RenderResult

B = FitBounds()


def _result(n: int) -> RenderResult:
    return RenderResult(pdf_path="x.pdf", html_path="x.html", page_count=n)


def _layout(params: FitParams) -> LayoutPlan:
    els = [
        LayoutElement(id="e1", kind="decor", optional=True),
        LayoutElement(id="e2", kind="pull_quote", optional=True),
        LayoutElement(id="e3", kind="heading"),
        LayoutElement(id="e4", kind="paragraph"),
        LayoutElement(id="e5", kind="heading"),
    ]
    return LayoutPlan(units=[LayoutUnit(id="u1", page_type=PageType.BODY, elements=els)], fit=params)


class FakeRenderer:
    """Page count is a function of the params; counts calls."""

    def __init__(self, pages_fn):
        self.pages_fn = pages_fn
        self.calls = 0
        self.last_params = None

    def __call__(self, params: FitParams):
        self.calls += 1
        self.last_params = params
        return _layout(params), _result(self.pages_fn(params))


def _density(p: FitParams) -> float:
    """Inverse-ish of density_to_params: a scalar in [-1, 1] from the knobs."""
    total = 0.0
    for knob in KNOBS:
        lo, hi = getattr(B, knob)
        v = getattr(p, knob)
        total += (v - 1.0) / ((hi - 1.0) if v >= 1 else (1.0 - lo)) * (1 if v >= 1 else 1)
    return total / len(KNOBS)


def smooth(base_pages: float, span: float):
    """Monotone page count: base + span * density, plus discrete effects."""

    def fn(p: FitParams) -> int:
        pages = base_pages + span * _density(p)
        if not p.opener_full_page:
            pages -= 2
        pages -= 0.5 * len(p.dropped_optional)
        pages += len(p.forced_breaks_before)
        return max(1, round(pages))

    return fn


def run(fake, target, tol=0, max_iterations=10, optional=("e1", "e2")):
    return fit_to_pages(
        fake, target_pages=target, tolerance=tol, bounds=B, max_iterations=max_iterations, optional_ids=list(optional)
    )


def test_density_endpoints_and_order():
    base = FitParams()
    tight = density_to_params(-1, B, base)
    assert tight.spacing_scale == B.spacing_scale[0] and tight.margin_scale == B.margin_scale[0]
    loose = density_to_params(1, B, base)
    assert loose.font_scale == B.font_scale[1]
    assert density_to_params(0, B, base) == base
    # priority order: at t = -0.2 only spacing has moved, and fully
    p = density_to_params(-0.2, B, base)
    assert p.spacing_scale == pytest.approx(B.spacing_scale[0])
    assert p.visual_scale == pytest.approx(1.0) and p.font_scale == pytest.approx(1.0)
    # at t = -0.5 spacing and visual are at their bounds, line height is mid-way
    p = density_to_params(-0.5, B, base)
    assert p.visual_scale == pytest.approx(B.visual_scale[0])
    assert 1.0 > p.line_height_scale > B.line_height_scale[0] and p.font_scale == pytest.approx(1.0)


def test_density_keeps_discrete_fields():
    base = FitParams(opener_full_page=False, dropped_optional=["e1"], forced_breaks_before=["e5"])
    p = density_to_params(0.7, B, base)
    assert p.opener_full_page is False and p.dropped_optional == ["e1"] and p.forced_breaks_before == ["e5"]


def test_density_monotone():
    prev = None
    for t in [x / 20 for x in range(-20, 21)]:
        p = density_to_params(t, B, FitParams())
        score = sum(getattr(p, k) for k in KNOBS)
        # total "looseness" never decreases as t increases (all knobs move the same way)
        assert prev is None or score >= prev - 1e-9
        prev = score


def test_no_target_single_render():
    fake = FakeRenderer(smooth(6, 4))
    layout, result, report = run(fake, None)
    assert fake.calls == 1 and report.status == "no_target" and report.achieved_pages == result.page_count


def test_already_exact():
    fake = FakeRenderer(lambda p: 6)
    _, result, report = run(fake, 6)
    assert fake.calls == 1 and report.status == "exact" and result.page_count == 6


def test_within_tolerance_stops_early():
    fake = FakeRenderer(lambda p: 7)
    _, _, report = run(fake, 6, tol=1)
    assert fake.calls == 1 and report.status == "within_tolerance"


@pytest.mark.parametrize("target", [5, 6, 7, 8])
def test_continuous_bisection_reaches_target(target):
    fake = FakeRenderer(smooth(6.4, 6))  # density alone spans roughly 3..9 pages
    layout, result, report = run(fake, target)
    assert result.page_count == target and report.status == "exact"
    assert fake.calls <= 10 and len(report.iterations) == fake.calls


def test_monotone_every_probe_in_bounds():
    fake = FakeRenderer(smooth(8, 6))
    _, _, report = run(fake, 6)
    for it in report.iterations:
        for knob in KNOBS:
            lo, hi = getattr(B, knob)
            assert lo - 1e-9 <= getattr(it.params, knob) <= hi + 1e-9


def test_plateau_gets_discrete_help():
    # density has no effect (plateau) but inline openers and drops do
    fake = FakeRenderer(lambda p: 8 - (2 if not p.opener_full_page else 0) - len(p.dropped_optional))
    _, result, report = run(fake, 5)
    assert result.page_count == 5 and report.status == "exact"
    assert any("opener" in i.action for i in report.iterations)


def test_unreachable_over_returns_closest_with_warning():
    fake = FakeRenderer(lambda p: 9)
    _, result, report = run(fake, 5)
    assert report.status == "over" and report.achieved_pages == 9
    assert any("Could not reach 5" in w for w in result.warnings)
    assert fake.calls <= 10


def test_unreachable_under_returns_closest_with_warning():
    fake = FakeRenderer(lambda p: 3 + min(len(p.forced_breaks_before), 1))
    _, result, report = run(fake, 9)
    assert report.status == "under" and report.achieved_pages == 4
    assert any("never invents" in w for w in result.warnings)


def test_forced_breaks_close_the_gap():
    fake = FakeRenderer(lambda p: 4 + len(p.forced_breaks_before))
    _, result, report = run(fake, 6)
    assert result.page_count == 6 and len(report.final.forced_breaks_before) == 2


def test_iteration_cap_is_hard():
    fake = FakeRenderer(lambda p: 20)
    for cap in (1, 2, 3, 10):
        fake.calls = 0
        run(fake, 4, max_iterations=cap)
        assert fake.calls <= cap


def test_returned_result_matches_last_render():
    """The PDF on disk must be the one for the returned plan: the best candidate is re-rendered if needed."""
    seen = []
    pages_fn = smooth(6, 4)

    def fn(p: FitParams):
        seen.append(p)
        return _layout(p), _result(pages_fn(p))

    layout, result, report = fit_to_pages(
        fn, target_pages=7, tolerance=0, bounds=B, max_iterations=10, optional_ids=["e1"]
    )
    assert seen[-1] == report.final == layout.fit


def test_overshoot_down_recovers_with_breaks():
    # inline openers drops 2 pages, overshooting 5 -> 4; a forced break brings it back to 5
    fake = FakeRenderer(lambda p: 6 - (2 if not p.opener_full_page else 0) + len(p.forced_breaks_before))
    _, result, report = run(fake, 5)
    assert result.page_count == 5 and report.status == "exact"
