"""Bounded render -> measure -> adjust loop (WP5). See PLAN.md "Page-count fitting".

The loop only turns layout knobs (spacing, visual size, line height, font
size, margins, opener mode, optional elements, forced breaks). It never edits
user text. When the target is unreachable it returns the closest result and a
warning in ``RenderResult.warnings``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from rooted.models import (
    Brief,
    BuildConfig,
    ContentDoc,
    ContentPlan,
    DesignSpec,
    FitBounds,
    FitIteration,
    FitParams,
    FitReport,
    LayoutPlan,
    RenderResult,
    VisualSet,
)

# Given FitParams, compose+build_html+render and return the result.
RenderFn = Callable[[FitParams], tuple[LayoutPlan, RenderResult]]

# Knob order for density_to_params (first = changed first).
KNOBS = ("spacing_scale", "visual_scale", "line_height_scale", "font_scale", "margin_scale")
# Order in which optional elements are dropped when over target (lowest priority first).
DROP_ORDER = ("decor", "pull_quote", "key_stats", "visual")


def density_to_params(t: float, bounds: FitBounds, base: FitParams) -> FitParams:
    """Map t in [-1, 1] (negative = tighter, positive = looser) to knob
    values, filling knobs in priority order: spacing, visual, line height,
    font, margins. Piecewise linear in |t|: each knob travels to its bound
    before the next starts. Discrete fields are copied from ``base``."""
    t = max(-1.0, min(1.0, t))
    out = base.model_copy(deep=True)
    n = len(KNOBS)
    for i, knob in enumerate(KNOBS):
        lo, hi = getattr(bounds, knob)
        target = lo if t < 0 else hi
        frac = max(0.0, min(1.0, n * abs(t) - i))
        setattr(out, knob, 1.0 + (target - 1.0) * frac)
    return out


def _distance(p: FitParams) -> float:
    return sum(abs(getattr(p, k) - 1.0) for k in KNOBS)


def optional_ids_by_priority(layout: LayoutPlan) -> list[str]:
    """Optional element ids in drop order (decor, pull quotes, key stats, optional visuals)."""
    rank = {k: i for i, k in enumerate(DROP_ORDER)}
    opt = [e for u in layout.units for e in u.elements if e.optional]
    opt.sort(key=lambda e: rank.get(e.kind, len(rank)))
    return [e.id for e in opt]


def fit_to_pages(
    render_fn: RenderFn,
    *,
    target_pages: int | None,
    tolerance: int,
    bounds: FitBounds,
    max_iterations: int,
    optional_ids: list[str],
) -> tuple[LayoutPlan, RenderResult, FitReport]:
    """Continuous phase: bisection on a density scalar mapped to knobs in
    priority order; discrete phase: opener mode, drop optional elements
    (``optional_ids`` is in drop order, first dropped first), forced breaks.
    Never exceeds ``max_iterations`` renders. Pure w.r.t. ``render_fn`` so it
    is testable with a fake.

    One render is reserved for re-rendering the best candidate when it is not
    the last one rendered, so the PDF on disk always matches the returned plan."""
    fitter = _Fitter(render_fn, target_pages, tolerance, bounds, max_iterations, optional_ids)
    return fitter.run()


class _Fitter:
    def __init__(self, render_fn, target, tol, bounds, max_iter, optional_ids):
        self.render_fn, self.N, self.tol, self.bounds = render_fn, target, tol, bounds
        self.max_iter = max(1, max_iter)
        self.optional_ids = list(optional_ids)
        self.iterations: list[FitIteration] = []
        self.best: Optional[tuple[LayoutPlan, RenderResult, FitParams]] = None
        self.last: Optional[tuple[LayoutPlan, RenderResult, FitParams]] = None
        self.layout: Optional[LayoutPlan] = None
        self.history: list[tuple[FitParams, LayoutPlan, RenderResult]] = []

    # ---- bookkeeping
    @property
    def used(self) -> int:
        return len(self.iterations)

    def can_search(self) -> bool:
        # keep one render in reserve for restoring the best candidate
        return self.used < self.max_iter - 1

    def miss(self, pages: int) -> int:
        return abs(pages - self.N)

    def ok(self, pages: int) -> bool:
        return self.miss(pages) <= self.tol

    def do(self, params: FitParams, action: str) -> int:
        layout, result = self.render_fn(params)
        self.layout = layout
        self.history.append((params, layout, result))
        self.iterations.append(FitIteration(params=params, page_count=result.page_count, action=action))
        cand = (layout, result, params)
        self.last = cand
        if self.best is None or self._key(cand) < self._key(self.best):
            self.best = cand
        return result.page_count

    def _key(self, cand):
        p = cand[2]
        discrete = len(p.forced_breaks_before) + len(p.dropped_optional) + (0 if p.opener_full_page else 1)
        return (self.miss(cand[1].page_count), discrete, _distance(p), self.used)

    def done(self) -> bool:
        return self.best is not None and self.ok(self.best[1].page_count)

    # ---- run
    def run(self):
        if self.N is None:
            p = self.do(FitParams(), "single render (no target)")
            layout, result, params = self.last
            return (
                layout,
                result,
                FitReport(
                    target_pages=None, achieved_pages=p, status="no_target", iterations=self.iterations, final=params
                ),
            )

        base = FitParams()
        self.do(base, "initial render")
        if not self.done():
            self.phase_a(base)
        if not self.done():
            self.phase_b()
        return self.finish()

    # ---- phase A: continuous bisection on density
    def phase_a(self, base: FitParams, anchor_pages: Optional[int] = None) -> None:
        N = self.N
        p0 = anchor_pages if anchor_pages is not None else self.last[1].page_count
        over = p0 > N
        extreme_t = -1.0 if over else 1.0
        if not self.can_search():
            return
        pe = self.do(density_to_params(extreme_t, self.bounds, base), f"probe t={extreme_t:+.1f}")
        if self.ok(pe) or self.done():
            return
        if (over and pe > N) or (not over and pe < N):
            return  # the extreme misses on the same side: continuous knobs cannot reach it
        # bracket: [a, b] with t_far overshooting and t_near the original side
        t_near, t_far = 0.0, extreme_t
        while self.can_search() and abs(t_far - t_near) >= 0.05:
            mid = (t_near + t_far) / 2
            pm = self.do(density_to_params(mid, self.bounds, base), f"bisect t={mid:+.2f}")
            if self.ok(pm):
                return
            same_side = (pm > N) if over else (pm < N)
            if same_side:
                t_near = mid
            else:
                t_far = mid

    # ---- phase B: discrete steps
    def phase_b(self) -> None:
        cur = self.best[2]
        pages = self.best[1].page_count
        if pages > self.N:
            self.over_steps(cur)
        else:
            self.under_steps(cur)

    def _groups(self) -> list[list[str]]:
        kinds: dict[str, str] = {}
        if self.layout is not None:
            for u in self.layout.units:
                for e in u.elements:
                    kinds[e.id] = e.kind
        groups: dict[str, list[str]] = {}
        for eid in self.optional_ids:
            groups.setdefault(kinds.get(eid, "other"), []).append(eid)
        order = [k for k in DROP_ORDER if k in groups] + [k for k in groups if k not in DROP_ORDER]
        return [groups[k] for k in order]

    def over_steps(self, cur: FitParams) -> None:
        N = self.N
        cur = cur.model_copy(deep=True)
        steps: list[tuple[str, FitParams]] = []
        if cur.opener_full_page:
            t = cur.model_copy(deep=True)
            t.opener_full_page = False
            steps.append(("inline section openers", t))
            cur = t
        for group in self._groups():
            t = cur.model_copy(deep=True)
            t.dropped_optional = [*t.dropped_optional, *[g for g in group if g not in t.dropped_optional]]
            steps.append((f"drop {len(group)} optional element(s)", t))
            cur = t
        for action, params in steps:
            if not self.can_search():
                return
            p = self.do(params, action)
            if self.ok(p):
                return
            if p < N:
                # overshot downwards: recover pages with forced breaks, keeping the tightened state
                self.under_steps(params, allow_opener=False)
                return
        # still over with everything tight: one last continuous pass from the most reduced state
        if self.can_search() and self.last[1].page_count > N:
            self.phase_a(cur, anchor_pages=self.last[1].page_count)

    def _ref_for(self, cur: FitParams) -> Optional[tuple[LayoutPlan, RenderResult]]:
        for params, layout, result in reversed(self.history):
            if params == cur:
                return layout, result
        return None

    def _break_candidates(self, cur: FitParams, rejected: set[str]) -> list[str]:
        """Elements a forced break may precede, one per flow unit first.

        Using the page map of the render for ``cur``: in each unit, the middle
        element of its last page that starts on that page (splitting the last
        page always adds exactly one page and never cascades). Falls back to
        ordering by element kind when no page map is available."""
        ref = self._ref_for(cur)
        if ref is None:
            ref = (self.layout, self.last[1]) if self.layout is not None else None
        if ref is None:
            return []
        layout, result = ref
        if not result.page_map:
            return self._blind_candidates(layout, cur, rejected)
        pages_of: dict[str, list[int]] = {}
        for entry in result.page_map:
            for eid in entry.element_ids:
                pages_of.setdefault(eid, []).append(entry.page_index)
        primary: list[str] = []
        secondary: list[str] = []
        for u in layout.units:
            if u.fixed:
                continue
            els = u.elements
            usable = [
                (i, e)
                for i, e in enumerate(els)
                if i > 0
                and e.id not in cur.forced_breaks_before
                and e.id not in rejected
                and not (els[i - 1].kind == "heading")
            ]
            by_page: dict[int, list[str]] = {}
            for _, e in usable:
                pg = pages_of.get(e.id)
                if pg:
                    by_page.setdefault(pg[0], []).append(e.id)  # page where the element starts
            for pg in sorted(by_page, reverse=True):
                ids = by_page[pg]
                # skip the element that is first on its page: a break there changes nothing
                first_on_page = {
                    eid
                    for eid in (
                        entry.element_ids[0]
                        for entry in result.page_map
                        if entry.page_index == pg and entry.element_ids
                    )
                }
                ids = [x for x in ids if x not in first_on_page]
                if ids:
                    primary.append(ids[len(ids) // 2])
                    secondary.extend(x for x in ids if x != ids[len(ids) // 2])
                    break
        return primary + secondary

    @staticmethod
    def _blind_candidates(layout: LayoutPlan, cur: FitParams, rejected: set[str]) -> list[str]:
        """No page map: headings first, then figures/tables/callouts, then the rest, latest first."""
        rank = {"heading": 0, "visual": 1, "table": 1, "callout": 1, "image": 1}
        found: list[tuple[int, int, str]] = []
        n = 0
        for u in layout.units:
            if u.fixed:
                continue
            for i, e in enumerate(u.elements):
                n += 1
                if i and e.id not in cur.forced_breaks_before and e.id not in rejected:
                    found.append((rank.get(e.kind, 2), -n, e.id))
        found.sort()
        return [eid for _, _, eid in found]

    def under_steps(self, cur: FitParams, allow_opener: bool = True) -> None:
        N = self.N
        cur = cur.model_copy(deep=True)
        pages = self.best[1].page_count if self.last[2] is not cur and allow_opener else self.last[1].page_count
        if allow_opener and not cur.opener_full_page and self.can_search():
            cur.opener_full_page = True
            p = self.do(cur.model_copy(deep=True), "full-page section openers")
            if self.ok(p):
                return
            if p <= N:
                pages = p
            else:
                cur.opener_full_page = False
        rejected: set[str] = set()
        limit: Optional[int] = None
        while self.can_search() and pages < N:
            cands = self._break_candidates(cur, rejected)
            if not cands:
                return
            k = min(N - pages, len(cands))
            if limit is not None:
                k = max(1, min(k, limit))
            chosen = cands[:k]
            trial = cur.model_copy(deep=True)
            trial.forced_breaks_before = [*trial.forced_breaks_before, *chosen]
            p = self.do(trial, f"forced break before {', '.join(chosen)}")
            if self.ok(p):
                return
            if pages <= p < N:  # no overshoot: keep the breaks (they redistribute content; later ones add pages)
                cur, pages = trial, p
            else:  # fewer pages or overshoot: discard and become more careful
                if len(chosen) == 1:
                    rejected.update(chosen)
                else:
                    limit = max(1, len(chosen) // 2)

    # ---- finish
    def finish(self):
        best_layout, best_result, best_params = self.best
        if self.last[2] is not best_params and self.used < self.max_iter:
            layout, result = self.render_fn(best_params)
            self.iterations.append(
                FitIteration(params=best_params, page_count=result.page_count, action="re-render best candidate")
            )
            best_layout, best_result = layout, result
        pages = best_result.page_count
        miss = pages - self.N
        if miss == 0:
            status = "exact"
        elif abs(miss) <= self.tol:
            status = "within_tolerance"
        elif miss > 0:
            status = "over"
            best_result.warnings.append(
                f"Could not reach {self.N} pages: closest layout has {pages}. Knobs are exhausted; "
                "reaching the target would require condensing or dropping content."
            )
        else:
            status = "under"
            best_result.warnings.append(
                f"Could not reach {self.N} pages: closest layout has {pages}. There is not enough content "
                "to fill the target without padding, and Rooted never invents content."
            )
        report = FitReport(
            target_pages=self.N, achieved_pages=pages, status=status, iterations=self.iterations, final=best_params
        )
        return best_layout, best_result, report


def make_render_fn(
    doc: ContentDoc,
    plan: ContentPlan,
    design: DesignSpec,
    visuals: VisualSet,
    brief: Brief,
    work_dir: Path,
    renderer,
    *,
    pdf_path: Path,
    html_path: Path,
    config: Optional[BuildConfig] = None,
    min_body_size_pt: float = 8.5,
) -> RenderFn:
    """Build a ``RenderFn``: compose -> build_html -> render, reusing one renderer."""
    from rooted.layout.compose import compose
    from rooted.layout.html import build_html

    def render_fn(params: FitParams):
        layout = compose(doc, plan, design, visuals, brief, params)
        html = build_html(doc, plan, design, visuals, layout, work_dir, min_body_size_pt=min_body_size_pt)
        result = renderer.render(html, base_url=work_dir, pdf_path=pdf_path, html_path=html_path)
        return layout, result

    return render_fn
