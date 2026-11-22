# Rooted: design and implementation plan

`rooted build content.docx --model reference.pdf --brief "8 pages, navy and gold, executive tone" -o out.pdf`

Rooted takes the user's words from a .docx and the look of a reference PDF, and produces a new PDF that has the reference's design and the user's text. Where the brief conflicts with the reference, the brief wins. The user's text is never rewritten or dropped unless the brief allows it, and every printed word can be traced back to a source block.

Status: the shared skeleton is in place (`src/rooted/models.py`, `src/rooted/llm.py`, `src/rooted/errors.py`, stubs, `tests/conftest.py`, `tests/factories.py`, `tests/test_skeleton.py`). Everything else in this document is to be built.

---

## 1. Principles

1. **The LLM emits structured JSON, never markup.** Claude returns Pydantic-validated plans (brief, content plan, vision notes, visual plan). Tested Jinja2 templates turn those plans into HTML and CSS. Prior-art reports agree that LLM-written HTML or ReportLab code breaks tables, splits sentences, and ignores pagination.
2. **Numbers come from code; semantics come from Claude.** Font sizes, margins, page geometry, and colors are measured with PyMuPDF. Claude's vision pass only *labels* what was measured (which cluster is H1, which page is a cover) and describes layout regions. Claude never transcribes text out of an image.
3. **Verbatim by default, and traceable.** Every printed string is either a block's own text, a `TextExcerpt` with exact offsets, or a `PlannedText` marked `generated_label`/`rewritten`. Validators enforce this and repair offsets. `render.json` lists every block, the pages it landed on, and all generated text.
4. **Every stage is a pure-ish function with a JSON artifact.** Stages can be re-run, inspected, edited by hand, and tested offline with `FakeLLMClient`.
5. **Bounded loops only.** Page fitting and LLM retries have hard iteration caps and always end with a report.

## 2. Architecture and data flow

```
 content.docx         brief text / --brief-file         reference.pdf
      |                        |                              |
      v                        v                              v
 [1 INGEST]               [2a BRIEF]  (LLM)            [3 DESIGN]
 python-docx              regex + Claude               3a raw_extract (PyMuPDF)  -> design_raw.json
      |                        |                       3b thumbnails (PNG)       -> thumbs/
      | ingest.json            | brief.json            3c vision (Claude)        -> design_vision.json
      |                        |                       3d synthesize + apply_brief
      |                        +---------------------> 3e fonts.resolve_design_fonts
      |                        |                              | design.json
      |                        |                              |
      +-----------+------------+-----------+------------------+
                  |                        |
                  v                        v
           [2b CONTENT]  (LLM)   <-- PageCapacity from DesignSpec
           sections, cover, callouts, pull quotes, key stats, page budgets
           validate: excerpt repair + coverage invariant
                  | content.json
                  v
           [4 VISUALS]  (LLM plan, code render)
           numeric candidates -> VisualPlan -> provenance check -> SVGs
                  | visuals.json, visuals/*.svg
                  v
           [5 LAYOUT & RENDER]
           compose -> LayoutPlan -> Jinja2 HTML/CSS -> WeasyPrint -> PDF
                ^                                               |
                +------ fit loop: measure pages, adjust --------+
                  | layout.json, render.json (BuildReport), out.pdf
                  v
           [6 ORCHESTRATION] CLI, WorkDir + manifest cache, logging, --stage/--from/--dry-run
```

Execution order (`Stage` enum): `ingest -> brief -> design -> content -> visuals -> layout -> render`. Ingest, brief, and design are independent of each other. The content stage needs the design so it can estimate words per page.

## 3a. DECISION UPDATE (user, 2026-11-22): Chromium is the default renderer

The user chose **Playwright/Chromium as the default**. This Mac has no Homebrew, so Pango/WeasyPrint is impractical. WeasyPrint becomes the optional extra (`pip install rooted[weasyprint]`) behind the same `Renderer` protocol. Where Section 3 and the rest of this doc say otherwise, this section wins.
- Templates are written and tested against Chromium (Chrome 131+ supports `@page` margin boxes and named pages; it does not support `string-set`). Running section titles are injected per page by the layout code, or set as literal text per section's named page.
- Measurement: render with `page.pdf()`, count pages and build the page map with PyMuPDF on the output (find the `data-el` markers via hidden text or anchors), plus `page.evaluate` DOM checks for overflow of fixed regions. Reuse one browser instance across fitting iterations.
- `rooted doctor` checks for the Chromium install (`playwright install chromium`) instead of Pango.
- Page count policy (user): **exact target, never edit the user's text**. Use only layout knobs. If the target can't be reached, emit the closest result and a clear warning.

## 3. Renderer choice: HTML/CSS + Jinja2 → WeasyPrint (default), Chromium as an optional backend

| Criterion | WeasyPrint | Playwright/Chromium | Typst | ReportLab |
|---|---|---|---|---|
| CSS paged media (`@page`, named pages, margin boxes, `string-set`, running elements, counters, `:first`/`:left`/`:right`) | Best available | Partial (margin boxes since Chrome 131; no `string-set` or running elements) | N/A (own model, very good) | Manual |
| Multi-column flow | `columns` supported; `column-span: all` supported | Excellent | `columns()` good | Frame-based, manual |
| Precise absolute positioning (cover) | `position:absolute` in page box: good | Good | Excellent | Excellent |
| Font embedding | `@font-face` with local files, subsets automatically | Good | Good (font paths) | TTF registration, manual |
| Page-count measurement | `HTML(...).render()` → `Document.pages` in-process, boxes inspectable, no file I/O | `page.pdf()` then count; DOM measurement via JS | Compile and count, ~100 ms | Build and count |
| Speed per render (8 to 20 pages) | ~0.5 to 2 s | ~0.5 s with a warm browser | Fastest | Fast |
| macOS install friction | **Needs Pango (`brew install pango`)** | pip + `playwright install chromium` (~150 MB), no system libs | `pip install typst`, zero system deps | pip only |
| Templating familiarity for implementers and LLM vision vocabulary | HTML/CSS | HTML/CSS | New language | Python API |

**Decision:** we generate HTML+CSS from Jinja2 templates and render with **WeasyPrint**. It has the strongest CSS paged-media support, which we need for running headers, footers, and section names (`string-set`), named page templates (`page: cover`), page counters, and `break-*` control. It runs no JavaScript, which is fine because visuals are pre-rendered SVGs. It can also be measured in-process, which suits the fitting loop. The `Renderer` protocol (`rooted/layout/render.py`) keeps the backend swappable. A Chromium backend is an optional extra (`pip install rooted[chromium]`) for machines without Pango. It supports a reduced feature set: no `string-set`, so it uses literal section names.

The runner-up is Typst: zero install friction and very fast, but it would need a second templating language and Typst-specific escaping of user text. If Pango is a blocker for the user, we revisit (see Open questions). ReportLab and LaTeX were rejected: the layout logic ends up hand-coded, and the prior art reports poor results.

Note: this machine currently has no Homebrew and no Python ≥3.11. The skeleton was validated on 3.9 in a scratch venv. See Open questions.

## 4. Stage designs

### 4.1 Ingest (WP1): `rooted.ingest.read_docx(path, assets_dir) -> ContentDoc`
- Walk `document.element.body` children in order (`w:p`, `w:tbl`) so tables stay in sequence. `document.paragraphs` loses table order.
- Headings come from the style name (`Heading 1..6`, `Title` → level 1, `Subtitle` → paragraph tagged `style_name`) and from outline level as a fallback. List items come from `w:numPr` (`ordered` from `numFmt`, `level` from `ilvl`). Quotes come from the `Quote`/`Intense Quote` styles. Captions come from the `Caption` style.
- Inline runs record bold, italic, underline, superscript, subscript, and hyperlinks (via relationship id). Adjacent runs with identical formatting are merged.
- Images: `a:blip r:embed` → bytes → `assets/img_<sha8>.<ext>`, with pixel size taken from Pillow.
- Tables: cell text as runs; gridSpan → colspan; vMerge → rowspan; the first row is the header unless the docx marks otherwise.
- Block ids are sequential `b0001…`, so they are deterministic for the same input.

### 4.2 Brief (WP2): `rooted.content.parse_brief(text, llm) -> Brief`
- A deterministic pass runs first: regex for page counts (digits and number words, plus "pp"), page sizes, and edit permissions ("may condense", "keep wording exactly"). A color-name table (CSS named colors plus a small curated set such as navy, gold, forest, teal, burgundy, charcoal) maps names to hex.
- LLM pass (`TASK_BRIEF`, effort `low`). **Prompt intent:** "Extract constraints from this brief into the schema. Only fill fields the brief states or clearly implies. Map color words to palette roles: the first-named color is primary, the second is accent. Never invent a page count." Deterministic values win over LLM values for the page count, and any conflict is logged.
- Without an LLM (dry run): regex-only Brief.

### 4.3 Design extraction (WP3)
**3a `extract_raw(pdf) -> RawDesignStats`** (PyMuPDF only):
- Spans from `page.get_text("dict")`: font name (strip `ABCDEF+`), size, flags (bold `16`, italic `2`, serif `4`, mono `8`), color int → hex, bbox.
- Clusters are keyed by (normalized name, size rounded to 0.5 pt, bold, italic, color) and carry char counts, pages, and sample text. The body cluster is the one with the most characters.
- Colors: text colors weighted by chars, fills and strokes from `get_drawings()` weighted by area, and image colors via k-means (numpy, k=5) on downsampled thumbnails. Near-duplicates are merged within ΔE < 3.
- Rules: thin rects and lines (height or width < 2 pt).
- Margins: the 5th and 95th percentiles of body-span bbox edges across body pages.
- Columns: a histogram of body-span x0 values. Two or more strong peaks separated by more than 30% of the text width mean multiple columns; the gutter is the gap between column extents.
- Repeated text (header/footer candidates): text in the top or bottom 12% of the page, with digits normalized to `#`, at a stable y (±3 pt), on at least 50% of pages.

**3b thumbnails:** `select_sample_pages` always takes the first and last pages, then fills up to `vision_max_pages` (default 8) by farthest-point selection over per-page feature vectors. Pages are rendered at 100 dpi to `thumbs/page-NN.png`.

**3c vision (`TASK_DESIGN_VISION`, effort `high`):** one call with all thumbnails, each labeled "Page N", plus a compact, sorted JSON of clusters, colors, and repeated texts. **Prompt intent:** "You are a senior editorial designer. Classify each page into a page type. Assign each font cluster id to a typographic role. Assign palette roles to the measured hex colors (use only the hexes given). For each page type, describe its template as normalized regions. Describe callout, table, and figure styling and recurring motifs. For each font, give its classification and 1 to 3 similar Google Fonts. Do not transcribe text, and do not invent sizes or colors that were not given." Unknown cluster ids or hexes are dropped with a warning.

**3d `synthesize_design(raw, notes) -> DesignSpec`:** numbers come from `raw` and labels come from `notes`. Every `TypeRole` gets a style; missing roles are derived from body on a 1.25 modular scale (for example, caption = body × 0.8, label = caption with uppercase). Box, table, and figure styles come from fills and rules near cluster-labeled text, guided by the notes' descriptions. If vision fails, heuristics produce a valid spec and a warning is recorded.

**3d′ `apply_brief(spec, brief)`:** palette overrides replace roles. `ON_PRIMARY` is recomputed so its contrast with primary is at least 4.5. The secondary color derives from primary (a lighter tint) if the brief names only one color. Font overrides apply. A brief page size rescales margins (regions are normalized already). Every change is appended to `overrides_applied`.

**3e fonts (WP1 code, called by the pipeline):** see §7.

### 4.4 Content understanding (WP2): `plan_content(doc, brief, design, llm) -> ContentPlan`
- `estimate_capacity(design, brief)` gives words per body page, using text-area width and height, columns, body size and line height, average glyph width ≈ 0.5 em, and 15% heading overhead. It also counts fixed pages (cover, back cover, TOC).
- Prompt input is one line per block, `[b0007|paragraph|42w] text`, with tables abbreviated, plus the Brief, the page types available in the design, capacity, and target pages.
- **Prompt intent (`TASK_CONTENT_PLAN`, effort `high`):** "Determine purpose, audience, and report type. Group blocks into sections in original order. Choose cover fields from existing text. Short generated labels (≤8 words) are allowed only when no suitable text exists, and must be marked generated_label. Mark section openers. Choose at most one pull quote per ~2 pages; each must be an exact substring of a block. Pick key stats only from numbers present in the text. Move side material into callouts. Every block id must appear exactly once in cover.consumed_block_ids, a section's block_ids, or a callout. Under edit_policy=verbatim you may not drop or rewrite anything." The brief's edit policy is interpolated verbatim.
- Post-processing (pure functions, unit-tested): `repair_excerpts` fixes offsets, or drops an excerpt that is not found. `repair_coverage` re-inserts orphans after their nearest preceding placed block, removes duplicates, and reverts unpermitted drops and rewrites. `check_coverage` must then return no violations, or the stage raises `TraceabilityError`. `assign_page_budgets` splits `target_pages - fixed_pages` across sections in proportion to words plus visual weight.
- Over budget with `edit_policy=condense`: v1 records a note only. A condensing pass is future work, and all condensing would be traced as `rewrites`.

### 4.5 Visuals (WP4): `build_visuals(doc, plan, design, brief, llm, work_dir) -> VisualSet`
- `find_numeric_candidates(doc)` regex-scans for percentages, currency, counts with units, years, and numeric table columns, keeping sentence context and block ids.
- **Prompt intent (`TASK_VISUAL_PLAN`, effort `medium`):** "Given sections, numeric candidates, design motifs, and visuals_policy, propose visuals that clarify content. A chart is allowed only when there are ≥2 comparable numbers with the same unit in the candidates. Every DataPoint must quote `raw` exactly and cite its block. Diagram labels must be excerpts or generated_label. Use icons only from ICON_NAMES. Use decor only as content-free motifs that echo the reference. Mark each visual required or optional."
- `validate_visual_plan` is the never-invent-data gate. It checks that `raw` is a substring of its excerpt and that the excerpt is exact, that `parse(raw)` equals `value` (handling `,`, `%`, `k/M/B`, currency), that categories appear in the text, and that series lengths match the category count. Failing visuals go to `rejected`.
- Rendering: matplotlib (Agg) with `svg.fonttype='none'` keeps chart text live so WeasyPrint draws it with the embedded fonts. Spines, gridlines, sizes, and the series colors (`figure.series_roles`) come from the DesignSpec. Diagrams, decor, cover art, and icons are generated SVG strings (no extra deps), are deterministic from `seed`, and use palette colors only. User images are referenced from `assets/`.

### 4.6 Layout and render (WP5)
- `compose(...) -> LayoutPlan` is renderer-independent. It produces units (cover, toc, opener, flow, back cover) and ordered elements with `block_ids` for tracing, `optional` flags (pull quotes, key stats, optional visuals, decor), and `keep_with_next` on headings.
- `build_css(design, layout)` emits:
  - CSS variables from DesignSpec × FitParams;
  - `@font-face` rules from `design.fonts`;
  - one named `@page` per page type, with size, margins, and background, plus `@top-*`/`@bottom-*` margin boxes for running elements (`string(section)`, `counter(page)`);
  - `columns` on flow units, with `column-span: all` for full-width visuals;
  - `break-inside: avoid` on figures, callouts, tables (rows), and stat rows;
  - `orphans`/`widows: 2`.
- `build_html(...)` renders `templates/document.html.j2` and its partials (`cover.html.j2`, `opener.html.j2`, `flow.html.j2`, `back.html.j2`, `elements/*.html.j2`) with autoescape on. Fixed pages use absolutely positioned region boxes from `PageTemplate.regions`. Each element carries `data-el` and `data-blocks` attributes.
- `WeasyPrintRenderer.render(...)` builds `HTML(string, base_url=work_dir).render()`, then walks `document.pages[i]._page_box` descendants to collect `data-el` ids per page (the page map). It detects overflow of fixed regions: content that spills past the region box, or a fixed unit spanning more than one page. It then calls `write_pdf`.
- `fit_to_pages(...)`: see §6.
- `build_trace(...)` and `collect_generated_text(...)` feed the `BuildReport`.

### 4.7 Orchestration (WP6)
- `rooted build` options are listed in `cli.py`'s docstring. `--brief` and `--brief-file` are mutually exclusive, and both are optional (no brief means the reference design is used as-is with natural length). `rooted inspect-design ref.pdf` prints the DesignSpec summary and font mapping. `rooted doctor` checks the Pango/WeasyPrint import, font cache, API credentials, and the Python version.
- `WorkDir` (default `.rooted/<sha8 of content+reference>/`, overridable) stores one JSON artifact per stage and `manifest.json`. A stage's `input_hash` is the sha256 of input file bytes, upstream artifact hashes, the relevant config subset, and `STAGE_VERSIONS[stage]`. A stage is skipped when its hash matches. `--from X` invalidates X and everything after it. `--stage X` stops after X. `--force` re-runs everything. Users may hand-edit an artifact (for example `content.json`); downstream stages then re-run because the upstream artifact hash changed.
- `--dry-run` runs ingest, regex brief, raw design extraction, and capacity estimation. It prints the planned LLM calls (task, images, approximate input tokens via `len/4`) and an estimated page count. It makes no LLM calls and writes no PDF.
- LLM: `build_client(config.llm, work_dir/"llm_cache")`. Logging uses a Rich console plus `<work_dir>/rooted.log`, and logs the request id of each LLM call.

## 5. Key data models (all in `src/rooted/models.py`)

| Artifact | Model | Producer | Consumers |
|---|---|---|---|
| ingest.json | `ContentDoc` (`Block`, `InlineRun`, `TableData`, `ImageRef`) | WP1 | WP2, WP4, WP5 |
| brief.json | `Brief` (LLM output) | WP2 | WP3 (`apply_brief`), WP2, WP4, WP5 |
| design_raw.json | `RawDesignStats` | WP3 | WP3 |
| design_vision.json | `VisionDesignNotes` (LLM output) | WP3 | WP3, WP1 fonts (hints) |
| design.json | `DesignSpec` (+ `FontResolution` from WP1) | WP3 → WP1 fonts | WP2 (capacity), WP4, WP5 |
| content.json | `ContentPlan` (LLM output, repaired) | WP2 | WP4, WP5 |
| visuals.json | `VisualSet` (`VisualPlan` is LLM output) | WP4 | WP5 |
| layout.json | `LayoutPlan` (+ `FitParams`) | WP5 | WP5 |
| render.json | `BuildReport` (`RenderResult`, `FitReport`, `TraceEntry`) | WP5 → WP6 | user |
| — | `BuildConfig`, `LLMConfig`, `FitBounds`, `Manifest` | WP6 | all |

Rules: models are never edited by package agents. LLM output models must not contain `dict`, `Any`, or recursive types; `tests/test_skeleton.py` checks every model in `LLM_OUTPUT_MODELS` with `anthropic.transform_schema`. Geometry is in points with a top-left origin; template regions are normalized (0..1).

## 6. Page-count fitting algorithm (WP5, `layout/fit.py`)

Inputs: target `N` (from the Brief; if `None`, a single render with status `no_target`), tolerance `tol` (default 0), `FitBounds`, `max_iterations` (default 10 renders), and optional element ids ordered by priority.

```
render(base) -> P0
if |P0-N| <= tol: done
Phase A, continuous (bisection on density t in [-1, 1]):
    density_to_params(t) fills knobs in priority order. For t<0 (tighten):
       spacing_scale -> visual_scale -> line_height_scale -> font_scale -> margin_scale,
    each moving to its bound before the next one starts (piecewise linear on |t|).
    t>0 loosens in the same order. Body size is clamped >= min_body_size_pt.
    lo, hi = (-1, 0) if P0 > N else (0, 1)
    probe the extreme first: if even t=-1 (or +1) misses, skip to Phase B.
    bisect until |P-N| <= tol or the interval < 0.05 or iterations are exhausted.
    Page count is monotone non-increasing in density (approximately; ties are fine).
Phase B, discrete, one step per render, re-running a short bisection if budget remains:
    over (P > N):  1) opener_full_page=False (openers become inline banners)
                   2) drop optional elements, lowest priority first:
                      decor -> extra pull quotes -> key-stat rows -> optional visuals
                   3) if edit_policy == condense: report "needs condensing" (v1, no LLM rewrite)
    under (P < N): 1) opener_full_page=True
                   2) forced page breaks before H1 sections that start in the bottom 40% of a page
                   3) visual_scale to max; full_page placement for the largest required visual
                   4) never pad with invented content: the result is reported as "under"
Choose the closest result seen (ties -> the one with params closest to 1.0). Write FitReport.
```

Phase A also tracks fill quality. The final page should be at least 30% full; if not, a final ±0.05 density nudge is tried when budget remains. Every iteration records `FitIteration(params, page_count, action)`. `render_fn` is injected, so the algorithm is unit-tested with a fake renderer whose page count is a function of params.

## 7. Font fallback strategy (WP1, `rooted/fonts`)

1. `normalize_font_name`: strip the subset prefix and the `MT/PS/Std/Pro/LT` suffixes, and split style tokens (Thin 100 … Black 900, Italic/Oblique) into weight and italic.
2. **Embedded reuse** happens only when all of these hold: `--reuse-embedded-fonts` is set, `doc.extract_font(xref)` yields a TrueType/OpenType font, the OS/2 `fsType` permits installable or editable embedding (fontTools), and the cmap covers every character of the user's text. The default is off because of licensing; subsets rarely cover the user's text anyway.
3. **System**: look in `~/Library/Fonts`, `/Library/Fonts`, `/System/Library/Fonts`, and the `fc-list` output if available, matching on family name via fontTools `name` table (cached index).
4. **Google Fonts, exact family**: download TTFs from the `google/fonts` GitHub raw tree (`ofl/<family>/`) through the injected `FontFetcher` into the user cache dir (`platformdirs.user_cache_dir("rooted")/fonts`), recording license metadata. Disabled with `--offline`.
5. **Curated substitutes**: `substitutes.json` lists metric-compatible matches first (Arial/Helvetica→Arimo or Liberation Sans, Times→Tinos, Courier→Cousine, Calibri→Carlito, Cambria→Caladea, Georgia→Gelasio), then similar fonts (Helvetica Neue→Inter, Gotham→Montserrat, Proxima Nova→Figtree, Avenir→Nunito Sans, Futura→Jost, Gill Sans→Gillius ADF or Cabin, Frutiger→Hind, Myriad→Source Sans 3, Minion→Crimson Pro, Garamond→EB Garamond, Didot→Playfair Display, Bodoni→Bodoni Moda, Franklin Gothic→Libre Franklin, Univers→Roboto, DIN→Barlow).
6. **Vision hints** (`FontCharacter.similar_open_fonts`) are tried before step 7.
7. **Generic by classification**: serif→Source Serif 4, sans→Source Sans 3, mono→JetBrains Mono, display→Playfair Display, script→Dancing Script. If everything is offline and nothing is cached, fall back to a system font (Helvetica/Times/Menlo) and record `match="generic"` with a warning.

Each resolution records `match` (`exact | metric_compatible | similar | generic`). When the match is not exact, the CSS `fallback_stack` keeps the original name first, so an installed licensed font is used if present. Faces are copied or symlinked into `<work_dir>/fonts`, so the HTML is self-contained.

## 8. Error handling

- Exceptions live in `rooted/errors.py` and map to CLI exit codes: Input 2, Stage 3, LLM 4, Traceability 5, Render 6.
- LLM: the SDK retries 429/5xx itself (`max_retries=3`). Server-side refusal fallbacks are on (`fallbacks="default"` with the beta header). If the API rejects the fallback parameter, the wrapper retries once without it. A final refusal raises `LLMRefusalError`, and stages may degrade: design falls back to heuristics, visuals fall back to user images only, the brief falls back to regex only. Content planning cannot degrade, so it fails with a clear message. Truncation (`max_tokens`) leads to one retry with `max_tokens×2` capped at 64000, issued by the stage. A validation failure leads to one retry with the validation error appended to the prompt ("Your previous output failed validation: …"), issued by the stage.
- Degradations are recorded in the artifact `warnings` and surfaced in the CLI summary. The build never silently changes content.
- Renderer import failure raises `RenderError` with install hints (`brew install pango`, or `--renderer chromium`).
- Corrupt or encrypted PDF, or a docx that is not a zip, raises `InputError` before any LLM spend.

## 9. Test strategy

- **Offline by default.** `tests/conftest.py` provides `FakeLLMClient` (canned responses per task as a model, dict, JSON, Path, Exception, callable, or a list consumed in order, with call recording) and strips API keys from the environment. `tests/factories.py` provides mutually consistent samples (doc, brief, raw stats, design, plan, visuals, layout), so each package tests against its upstream contract without upstream code.
- Each package's tests live in `tests/<pkg>/` and its fixtures in `tests/fixtures/<pkg>/`. Test inputs are generated programmatically: `.docx` files with python-docx, reference PDFs with PyMuPDF (`page.insert_text`, `draw_rect` with known fonts, sizes, colors, and columns). Large binary fixtures are avoided.
- Markers: `live` (real API, `-m live`), `render` (auto-skipped without WeasyPrint/Pango), `network` (font downloads, `ROOTED_ALLOW_NETWORK=1`).
- Golden tests: extraction from a generated PDF must recover the known sizes and colors within tolerance. Fit tests use a fake renderer. Render tests check page count, that PyMuPDF text extraction contains every block's text (selectable text, verbatim check), that fonts are embedded (`page.get_fonts()`), and that no fixed region overflows.
- `tests/test_skeleton.py` is the contract test and must stay green.

## 10. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Vision mislabels roles or invents layout | Labels constrained to measured cluster ids and hexes; heuristics fallback; `rooted inspect-design` lets the user check and hand-edit `design.json` |
| Complex references (multi-template magazines, heavy imagery) | v1 targets reports and brochures with ≤7 page types; full-bleed imagery is approximated with decor and cover art; documented limitation |
| WeasyPrint CSS gaps (no flexbox-grade column balancing, slower on long docs) | Templates are tested against WeasyPrint only; Chromium backend best-effort; docs are short (≤40 pages) |
| Pango install on macOS | `rooted doctor`; Chromium extra; Typst fallback if needed (open question) |
| Page count unreachable without editing text | Bounded knobs plus discrete steps; honest `over`/`under` status; condense only when the brief permits (future) |
| Font licensing | Embedded reuse off by default; only OFL/Apache Google Fonts downloaded; license recorded |
| LLM drops or paraphrases text | Coverage invariant plus excerpt validation plus a post-render text check |
| Fabricated chart data | `validate_visual_plan` gate; rejected visuals listed in the report |
| Cost and latency (Opus with vision on 8 pages) | Prompt caching on system prompts; LLM response disk cache; `--dry-run` estimate; effort per task (brief `low`, visuals `medium`) |
| SDK drift (fallback beta name, `transform_schema` location) | All SDK calls in `llm.py`; mocked call-shape test; one `live` smoke test per task |

Future, not in v1: an optional **fidelity pass**. Rasterize output pages, pair them with reference pages of the same type, and ask Claude for a bounded list of `DesignSpec` adjustments (≤2 rounds) based on side-by-side images, plus an SSIM-style score on layout masks. Also future: an LLM condensing pass under `edit_policy=condense`, and theme presets as config (cf. lovstudio/md2pdf).

## 11. LLM client (shared; `src/rooted/llm.py`)

All Claude calls go through `LLMClient.structured(task=, system=, prompt=, output_model=, images=(), effort=None, max_tokens=None) -> output_model`. Stages receive the client by injection and never import `anthropic`. Task constants: `TASK_BRIEF`, `TASK_CONTENT_PLAN`, `TASK_DESIGN_VISION`, `TASK_VISUAL_PLAN`. System prompts live in each package's `prompts.py` and must be byte-stable: no timestamps, and `json.dumps(..., sort_keys=True)` for embedded JSON.

The exact call pattern used by `AnthropicLLMClient` (verified against the bundled claude-api reference and the SDK source, anthropic ≥0.125):

```python
import anthropic

client = anthropic.Anthropic(timeout=600.0, max_retries=3)   # creds from env (ANTHROPIC_API_KEY)

params = dict(
    model="claude-opus-5-5",            # Opus 5.5: thinking ALWAYS on (adaptive); never send
    max_tokens=32000,                   #   thinking={"type":"disabled"} or budget_tokens (400)
    system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "Page 1"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
        {"type": "text", "text": prompt},
    ]}],
    output_config={
        "effort": "high",               # low | medium | high | xhigh | max (Opus 5.5 default: medium)
        "format": {"type": "json_schema", "schema": anthropic.transform_schema(OutputModel)},
    },
)

# Default path: server-side refusal fallbacks (beta), streamed.
with client.beta.messages.stream(**params,
                                 betas=["server-side-fallback-2026-07-01"],
                                 fallbacks="default") as stream:
    message = stream.get_final_message()

# Non-fallback path (config.server_fallbacks=False, or the API rejected the beta):
with client.messages.stream(**params) as stream:
    message = stream.get_final_message()

if message.stop_reason == "refusal":      # whole fallback chain refused
    raise LLMRefusalError(message.stop_details.explanation, message.stop_details.category)
if message.stop_reason == "max_tokens":
    raise LLMTruncatedError(...)
text = next(b.text for b in message.content if b.type == "text")   # guaranteed-valid JSON
result = OutputModel.model_validate_json(text)
```

Notes:
- `client.messages.parse(..., output_format=OutputModel)` and `response.parsed_output` are equivalent on the non-beta path. We use the explicit `output_config.format` form because the beta stream rejects `output_format` combined with `output_config.format`, so this keeps both paths identical.
- Streaming is always used. It avoids the SDK's long-request guard and HTTP idle drops.
- Log `message._request_id` for every call.
- `CachingLLMClient` wraps any client with a disk cache keyed by the task, model, system prompt, prompt, image hashes, and schema.
- `FakeLLMClient` (tests) implements the same protocol.

### 11a. Backends (added 2026-11-22)

`LLMConfig.backend` is `"claude-code"` (default) or `"api"`; the env var `ROOTED_LLM_BACKEND` sets the default and `--backend` overrides it per run. `build_client` picks the implementation and always wraps it in `CachingLLMClient`; the cache key includes `backend:model`, so results never mix.

**claude-code** (`src/rooted/llm_claude_code.py`, `ClaudeCodeLLMClient`) uses the user's Claude Code login, with no API key. Per call it runs:

```
claude -p --input-format stream-json --output-format stream-json --verbose \
  --model <id|alias> --effort <level> --system-prompt <system> --tools "" \
  --no-session-persistence --strict-mcp-config --json-schema '<anthropic.transform_schema(Model)>'
```

The prompt (and any base64 image blocks) is one `{"type":"user",...}` JSON line on stdin. stream-json is used for every call because it is the only mode that accepts images. The last `result` event gives `structured_output`, which is validated into the pydantic model. The child runs in a fresh temp dir, with `CLAUDECODE`, `CLAUDE_PID`, `CLAUDE_EFFORT`, `CLAUDE_CODE_*`, `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` removed from its env. `--bare` is not used, because it would disable the OAuth login. The timeout is 900s.

Error mapping: `claude` missing gives `LLMError` (install it or use `--backend api`). Auth errors (401/403, "/login") give `LLMError` ("run `claude` and use /login") and are not retried. Rate limit, overloaded, 429/5xx and timeouts are retried with the `llm.py` backoff constants, then `LLMError`. `stop_reason == "refusal"` gives `LLMRefusalError`, and `max_tokens` gives `LLMTruncatedError`. A missing or invalid `structured_output`, or `error_max_structured_output_retries`, gives `LLMValidationError`. Any other `is_error` gives `LLMError`. Usage, cost (`total_cost_usd`) and duration are logged. `server_fallbacks` and the beta settings are ignored. `max_tokens` is passed through `CLAUDE_CODE_MAX_OUTPUT_TOKENS`.

**api** is the `AnthropicLLMClient` described above.

`rooted doctor` has a "claude cli" check (`claude --version` and `claude auth status`). The API-credentials check is informational only.

---

## 12. Implementation work packages

**Shared and frozen (architect-owned; no package edits these):** `pyproject.toml`, `src/rooted/__init__.py`, `src/rooted/models.py`, `src/rooted/errors.py`, `tests/conftest.py`, `tests/factories.py`, `tests/test_skeleton.py`, `docs/PLAN.md`. `src/rooted/llm.py` keeps a frozen interface; its internals belong to WP6.

**Change control:** if a package needs a model change or a new dependency, it stops and asks the coordinator. It does not work around a model with dicts or ad-hoc fields. Additive optional fields are cheap to approve.

Each package owns its directory's `__init__.py` and may replace its stub signatures' *bodies* only. Public signatures in the stubs are the contract. New private helpers and modules inside the owned directory are fine. Every package keeps `pytest tests/test_skeleton.py tests/<own>/` green and runs offline.

### WP1: Ingest and Fonts
- **Owns:** `src/rooted/ingest/**`, `src/rooted/fonts/**` (including new `fonts/substitutes.json`, `fonts/system_index.py`, `fonts/google.py`), `tests/ingest/**`, `tests/fonts/**`, `tests/fixtures/ingest/**`, `tests/fixtures/fonts/**`.
- **Provides:** `read_docx(path, assets_dir) -> ContentDoc`; `normalize_font_name`, `resolve_font`, `resolve_design_fonts(spec, required_chars=, config=, hints=, fetcher=) -> DesignSpec`.
- **Consumes:** models only.
- **Tests:** generate docx files with headings 1 to 3, nested ordered and bulleted lists, a table with merged cells, bold and italic runs, a hyperlink, an embedded PNG, and a page break; assert the block sequence and runs. Font tests cover name normalization (20+ cases), substitution table lookups, system index with a temp dir of fonts (use matplotlib's bundled DejaVu TTFs as fixtures), a fake fetcher for Google, the offline generic fallback, and the fsType and cmap coverage check.
- **Accept:** 100% of docx features above round-trip; block ids are deterministic; `resolve_design_fonts` never raises for unknown fonts and always yields at least one usable face file per role family when the system fallback exists.

### WP2: Brief and Content understanding
- **Owns:** `src/rooted/content/**`, `tests/content/**`, `tests/fixtures/content/**`, `tests/fixtures/llm/brief.json`, `tests/fixtures/llm/content_plan.json`.
- **Provides:** `parse_brief`, `estimate_capacity`, `assign_page_budgets`, `repair_excerpts`, `check_coverage`, `repair_coverage`, `plan_content`; prompts `BRIEF_SYSTEM` and `CONTENT_PLAN_SYSTEM`.
- **Consumes:** `ContentDoc`, `DesignSpec` (use the factories), `LLMClient`.
- **Tests:** regex brief cases ("8 pages", "eight-page", "8pp", "A4", "US Letter", color names); FakeLLM responses with deliberately broken plans (wrong offsets, missing blocks, duplicates, unpermitted drops) are repaired; capacity sanity (A4 two-column 10 pt ≈ 600 to 900 words); budgets sum to target minus fixed pages; the prompt contains every block id.
- **Accept:** `check_coverage` is empty after `plan_content` on all fixture cases, and verbatim policy is never violated.

### WP3: Design extraction
- **Owns:** `src/rooted/design/**`, `tests/design/**`, `tests/fixtures/design/**`, `tests/fixtures/llm/design_vision.json`.
- **Provides:** `extract_raw`, `render_thumbnails`, `select_sample_pages`, `analyze_pages`, `synthesize_design`, `apply_brief`, `extract_design`; `DESIGN_VISION_SYSTEM`.
- **Consumes:** `Brief`, `LLMClient`, `BuildConfig`. Does **not** call `rooted.fonts`; the pipeline does.
- **Tests:** a PyMuPDF-generated 4-page reference (cover with a full-bleed fill, a 2-column body with Helvetica 10 pt body and 18 pt bold H1 in a known color, a running footer with page numbers, a data page with a table drawn from rects). Assert clusters, margins (±2 pt), columns, gutter, repeated footer, and palette. Vision parsing with FakeLLM, including unknown ids that must be dropped. Synthesis without notes still yields every TypeRole. `apply_brief` with navy and gold yields a correct contrast for ON_PRIMARY.
- **Accept:** on the generated reference, the body and H1 sizes are exact, margins are within 2 pt, column count is correct, and the spec validates with every `TypeRole` present.

### WP4: Visuals
- **Owns:** `src/rooted/visuals/**`, `tests/visuals/**`, `tests/fixtures/visuals/**`, `tests/fixtures/llm/visual_plan.json`.
- **Provides:** `find_numeric_candidates`, `plan_visuals`, `validate_visual_plan`, `render_visuals`, `build_visuals`, `ICON_NAMES` (≥24 simple geometric icons), `VISUAL_PLAN_SYSTEM`.
- **Consumes:** `ContentDoc`, `ContentPlan`, `DesignSpec`, `Brief`, `LLMClient` (factories).
- **Tests:** a fabricated value is rejected; `raw` "3.1M" equals 3,100,000; mismatched series lengths are rejected; each ChartType renders a valid SVG (parse with `xml.etree`) using only palette hexes and the DesignSpec font family name; SVGs are deterministic for the same seed; policy NONE makes no LLM call.
- **Accept:** the sample visual set renders. No chart can pass validation with a number absent from its cited block.

### WP5: Layout, render and fit
- **Owns:** `src/rooted/layout/**` (including `templates/*.j2`, `static/*.css`), `tests/layout/**`, `tests/fixtures/layout/**`.
- **Provides:** `compose`, `build_css`, `build_html`, `get_renderer` / `WeasyPrintRenderer` / `ChromiumRenderer` (optional), `fit_to_pages`, `density_to_params`, `build_trace`, `collect_generated_text`.
- **Consumes:** `ContentDoc`, `ContentPlan`, `DesignSpec`, `VisualSet`, `Brief`, `FitBounds` (factories). For SVG paths in tests, write tiny placeholder SVGs into `work_dir/visuals/`.
- **Tests:** compose output order, grouping, and callout replacement; every block id is traced; HTML escapes `<script>` in user text; CSS contains named pages and font-faces; fit-loop unit tests with a fake `render_fn` (monotone, plateau, unreachable over and under, iteration cap); `@render` tests on the sample: PDF exists, every block's text is extractable, fonts are embedded, the cover is exactly one page, the page map is complete.
- **Accept:** the sample renders to an A4 PDF with a navy cover and two-column body; the fit loop lands on `target ± tol` in the fake-renderer tests within ≤10 renders.

### WP6: Orchestration, CLI and LLM client hardening
- **Owns:** `src/rooted/cli.py`, `src/rooted/pipeline.py`, `src/rooted/cache.py`, `src/rooted/logging_setup.py`, `src/rooted/llm.py` (internals only; the protocol, `ImageInput`, task constants, and class names are frozen), `tests/pipeline/**`, `tests/fixtures/pipeline/**`, `tests/fixtures/llm/` (only files not owned by WP2, WP3, or WP4; e.g. a README).
- **Provides:** `rooted build | inspect-design | doctor`, `run_pipeline`, `run_stage`, `WorkDir`, `hash_inputs`, `setup_logging`.
- **Consumes:** every package's public functions, by signature, via `monkeypatch` stubs in tests until those packages land.
- **Tests:** `typer.testing.CliRunner` option parsing; manifest freshness (re-run skips, input change invalidates downstream, `--from` and `--stage`); dry run makes zero LLM calls; the pipeline wires stage outputs correctly using monkeypatched stage functions returning factory objects; exit-code mapping; one `@live` smoke test per task.
- **Accept:** `rooted build --dry-run` works end-to-end once WP1 and WP3 land; with monkeypatched stages, the full pipeline writes all artifacts and a BuildReport.

### Ordering and dependencies
- All six packages can start **now** in parallel. Each depends only on the frozen skeleton and tests against `tests/factories.py`.
- Soft integration order: WP1 and WP3 first (their real artifacts make better fixtures), then WP2 and WP4, then WP5, then WP6 integration. After all packages land, one integration task (coordinator or WP6) runs a real end-to-end build on a sample docx and reference and fixes the wiring only.
- Cross-package calls exist only in `pipeline.py` (WP6) and in the `extract_design` / `build_visuals` convenience functions inside their own packages. No package imports another package's private modules.

## 13. Open questions for the user
1. **Renderer install:** WeasyPrint needs Pango, and this Mac has no Homebrew. Is installing Homebrew and Pango acceptable, or should Chromium (Playwright) or Typst be the default?
2. **Python:** only 3.9 is installed. Is it OK to install 3.11+ (python.org installer, or uv)?
3. **Font downloads:** may Rooted fetch Google Fonts at runtime? Should reusing fonts embedded in the reference ever be allowed (licensing)?
4. **Page-count strictness:** exact count by default, or ±1? Is condensing text ever acceptable by default?
5. **Generated labels:** are short generated labels (chart titles, "Key figures", a cover eyebrow) OK by default, or must all printed text be verbatim?
6. **Scope of references:** mostly reports and brochures, or magazine-style layouts with heavy imagery?
