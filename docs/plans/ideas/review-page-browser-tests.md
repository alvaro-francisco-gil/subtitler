# Test the review page in a headless browser

**Priority:** high — `review.js` is the hottest file in the repo (churn 8, 502 lines, two fix commits in 90 days) and has no test; its last fix shipped "reasoned from the code; not observed in a browser"
**Gate:** decision:Add Playwright (Python) as a dev dependency and a `browser` test marker so the review page's blind, level-matched comparison is tested in headless Chromium?

**Goal:** The fairness rules the product rests on — blind labels, loudness-matched playback, never playing an unmeasured sample, polling until every sample is ready — are checked by tests that drive the real page, so a change to `review.js` cannot silently break them.

## Context

As of `b1466fb` (2026-09-30), 90-day window. `src/talk_studio/server/static/review.js`
scores highest in the repo (churn 8, fixes 2, 502 lines, no named test) and co-changes
with `index.html` (5), `review.css` (4), `server/app.py` (4) and `tools/__init__.py` (4).

Its fix commits are exactly the rules the design calls "fair comparison":

- `45545f9` Fix: gate playable candidates on measured loudness, not just rendered —
  `playable()` let a rendered-but-unmeasured candidate through and `matchedGain()` fell
  back to unity gain, so a candidate could play louder and win by being louder.
- `4c7cfb4` Fix: keep polling across an excerpt switch, and surface a failed re-render —
  `schedulePoll()` looked at one excerpt only, so "Sample here" left candidates stuck on
  "rendering…". Its message says: "Reasoned from the code; not observed in a browser …
  Needs a human check at a browser before shipping."
- `faeff35` Review page redesign, and `61f103e`/`54b7604` added image and video views to
  the same file.

`tests/test_server.py` (11 tests) covers the API — blind JSON, samples by label, a pick
revealing — and only checks that `/static/review.js` is served. Nothing exercises
`playable`, `matchedGain`, `schedulePoll`, `select` or the 0–9 keys.

Dependency category: local-substitutable — the FastAPI app and generated media
(`tests/conftest.py` `make_media`, `tiny_video`) already stand in for a real project.

## Design

**Chosen: Playwright for Python against the real server.** A fixture builds the
`client_and_project` project that `test_server.py` already builds, serves
`create_app(project.root)` with uvicorn on a free port in a thread, and opens it in
headless Chromium. Tests read page state through the DOM and, where needed, a
read-only hook (illustrative: `window.__reviewState = state` only when the URL has
`?test`), so `review.js` needs no restructuring.

Tests at the page boundary:

- An open round shows A/B labels and no tool name or settings anywhere in the DOM.
- A candidate whose loudness is not yet measured has its button disabled and is counted
  in the "Rendering N of M versions…" status, and key `1`–`9` cannot select it; after
  measurement it becomes playable.
- `matchedGain` for each label equals `10^((min − own)/20)` from the API's loudness.
- "Sample here" adds an excerpt; the page keeps polling until the new excerpt's samples
  are ready, also after switching excerpts (the `4c7cfb4` scenario).
- A round whose background re-render fails (the server marks pending candidates
  `failed`, `4c7cfb4`) ends the "Rendering N of M versions…" status and leaves those
  candidates disabled, rather than showing "Rendering…" forever.

A new pytest marker `browser` is skipped when Chromium is not installed, so
`uv run pytest -m "not gpu and not slow"` stays green on a machine without it.

**Alternatives considered:** extract the pure functions into an ES module and test them
with `node --test` — lost: it adds a Node toolchain to a Python repo and leaves the
polling and DOM wiring, where `4c7cfb4` lived, untested. Selenium — lost: heavier
setup and no advantage for one local page.

**Testing strategy:** these are the tests. `test_index_is_served`'s static-file asserts
become redundant once a browser test loads the page, and fold into it.

## Paths

As of 2026-09-30:

- `tests/test_review_page.py` (new)
- `tests/conftest.py` (a live-server fixture)
- `pyproject.toml` (dev dependency, `browser` marker)
- `uv.lock`
- `src/talk_studio/server/static/review.js` (the optional test hook only)
- `AGENTS.md` (one line: `uv run playwright install chromium` to run browser tests)

## Approval

**Needs your yes** — the `Gate` question; recommended answer: yes, because the page holds
the product's fairness rules, it needed two fixes on the same day (2026-09-16), and the last fix was never
seen running.

## Out of scope

- Visual or screenshot comparison of the page's look.
- Changing review behaviour; a bug the tests find gets its own fix.
- Running these tests in CI (see `ci-fast-tests`).
