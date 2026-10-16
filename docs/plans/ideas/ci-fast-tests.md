# Run the fast tests on every push

**Priority:** medium — 14 of 68 commits in 90 days were fixes, and the public repo has no CI: the suite runs only when someone remembers
**Gate:** decision:Add a GitHub Actions workflow that runs `uv run pytest -m "not gpu and not slow"` on every push and pull request, installing without torch and its CUDA wheels?

**Goal:** Every push and pull request runs the fast suite with ffmpeg on a clean machine, so a regression is caught by the push that causes it instead of the next session that runs tests.

## Context

As of `b1466fb` (2026-09-30), 90-day window: 68 commits, 14 of them fixes. The repo is
public on GitHub (`alvaro-francisco-gil/subtitler`) with no `.github/workflows/` and no
runs. `AGENTS.md` names the command: `uv run pytest -m "not gpu and not slow"`; 28 test
files exist, with 11 `gpu` or `slow` marks among them.

Install cost is the one obstacle: `stable-ts` brings `torch`, `torchaudio`, `triton` and 15
`nvidia-*` packages in `uv.lock`. The fast suite never needs them —
`captions/align.py` imports `stable_whisper` lazily, inside the aligning function, and
`tests/test_align.py` exercises only word I/O and result parsing.

Dependency category: true external (GitHub Actions), but nothing in the code changes.

## Design

**Chosen:** `.github/workflows/tests.yml` on `push` and `pull_request`: `ubuntu-latest`,
`apt-get install ffmpeg`, `astral-sh/setup-uv` with its cache, then
`uv sync --frozen --no-install-package torch --no-install-package torchaudio` plus the
`nvidia-*` and `triton` packages (listed once in the workflow), and the `AGENTS.md`
pytest command. If an import at collection needs torch, that test gets the `slow`
marker rather than CI installing torch. After `review-page-browser-tests` lands, the job
also runs `uv run playwright install --with-deps chromium`.

**Alternatives considered:** a plain `uv sync` — lost: several GB of CUDA wheels per
run for code the fast suite never imports. Moving `stable-ts` into a `captions` optional
extra — stronger long-term, but it changes how users install the tool, so it stays a
separate decision. A pre-commit hook — lost: local only, skippable, and no record on
pull requests.

**Testing strategy:** the first run on the PR that adds the workflow is the test; it must
pass on a clean runner, and its install time is reported in the PR.

## Paths

As of 2026-09-30:

- `.github/workflows/tests.yml` (new)
- `tests/` (a `slow` marker on any test that turns out to need torch at import)
- `AGENTS.md` (one line pointing at the workflow)

## Approval

**Needs your yes** — the `Gate` question; recommended answer: yes, because Actions are free
on a public repo, the install skips the heavy wheels, and the suite already exists.

## Out of scope

- GPU or `slow` tests in CI.
- Lint, type-checking or release workflows.
- Making `stable-ts` optional for users.
