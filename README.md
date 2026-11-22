# Rooted

`rooted` turns a `.docx`, a short brief and a reference PDF into a designed PDF. It copies the reference's design and fills it with your content.

## Install

```
uv venv && uv pip install -e ".[dev]"     # Python >= 3.11
.venv/bin/playwright install chromium     # default PDF renderer
rooted doctor                             # checks the environment
```

## Usage

```
rooted build content.docx --model reference.pdf --brief "8 pages, navy and gold" -o out.pdf
rooted inspect-design reference.pdf
rooted doctor
```

Useful options: `--dry-run` (no LLM calls), `--stage`, `--from`, `--force`, `--llm-model`, `--effort`, `--backend`.

## LLM backends

- **claude-code** (default). Rooted calls the [Claude Code](https://claude.com/claude-code) CLI (`claude -p`) and uses your existing Claude login. No API key is needed. Install Claude Code, run `claude` once and use `/login`. Calls count against your Claude plan usage.
- **api**. Uses the Anthropic SDK with `ANTHROPIC_API_KEY` (or `ant auth login`). Select it with `--backend api` or `ROOTED_LLM_BACKEND=api`.

Responses are cached per backend and model in the work dir, so re-runs are cheap. Details are in `docs/PLAN.md` section 11.

## Tests

```
.venv/bin/pytest -q
.venv/bin/pytest -m live tests/pipeline/test_llm_claude_code.py   # one real tiny `claude -p` call
.venv/bin/ruff check src tests
```
