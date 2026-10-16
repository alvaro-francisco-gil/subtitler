# One table for what each decision kind is

**Priority:** medium — the review/server/sampling cluster is the hottest code (review.js churn 8, app.py 6, cli.py 7), and every new decision kind has touched five to seven of those files together
**Gate:** decision:Move what each decision kind is (tool kind, reference, media, upstream, inputs, excerpt suggester) into one `decisions.py` table, with no change in behaviour, before the captions and thumbnail steps add two more kinds?

**Goal:** Adding a decision kind becomes one entry in one table instead of name checks in seven modules, so the captions and thumbnail steps of the design cost one file each and cannot forget a branch.

## Context

As of `b1466fb` (2026-09-30), 90-day window, 68 commits. Adding mastering (`7377e56`),
grade (`61f103e`) and clips (`54b7604`) each edited `project.py`, `sampling.py`,
`server/app.py`, `cli.py`, `tools/__init__.py` and the review page together; those files
co-change 3–5 times with each other in the window.

The facts about a decision are spread as name checks:

- `project.py`: `DECISIONS = ("audio", "master", "grade")`, `UPSTREAM = {"master": "audio"}`,
  `CLIP_PREFIX`, `is_clip()`; `Project.load` filters names through both.
- `tools/__init__.py`: `kind_of()` and `reference_for()` re-test `"clip-"` as a literal
  (not `CLIP_PREFIX`) and `== "grade"`.
- `sampling.py`: the same clip / upstream / source branch in `source_for`, `sample_file`
  and `render_round`, plus an `is_clip` check in `ensure_sample` (clip tools get the
  words file); `ready()` and `ensure_sample` decide audio by `suffix == ".wav"`.
- `server/app.py`: `REFERENCE = {"master": …}`, the reference label for clips inline, the
  media kind as `"video" if is_clip(name) else "image" if name == "grade" else "audio"`,
  and the upstream/clip precondition copied in both `sample_path` and `decision_view`
  (it is also in `render_round`).
- `cli.py` `command_excerpts`: `if args.decision == "grade"` picks the suggester.
- `final.render`: the order `audio → master → grade` written out by name.
- `review.js`: `KINDS`, `TITLES` and the `clip-` prefix strip.

Fix commits in the cluster: `4c7cfb4` (polling across an excerpt switch; a failed
re-render in `rerender`), `8756a7c` (blind log leak in `view`), `b6f3eb9` (racing sample
writers). None is caused by the scatter directly, but each fix had to be found in the
branch that applied to one kind.

Dependency category: in-process.

## Design

**Chosen: a hybrid of a minimal interface and a trivial common caller.** A new
`src/talk_studio/decisions.py` holds one frozen record per kind and one lookup:

```python
# illustrative
@dataclass(frozen=True)
class Kind:
    name: str                    # "audio", "master", "grade", "clip"
    tool_kind: str               # what Tool.kind a candidate must have
    media: str                   # "audio" | "image" | "video" — what the page shows
    reference: Callable[[], Tool]
    reference_label: str         # "Original", "Before mastering", "Wide, no captions"
    upstream: str | None         # decision whose pick feeds this one
    suggest: Callable[[Project, int], list[Excerpt]]

def kind_for(decision: str) -> Kind      # "clip-promise-big" → the clip kind
def fixed() -> list[str]                 # the decisions every project has, in render order
def preconditions(project, decision)     # upstream picked, clip inputs present — raises ProjectError
```

`project.py`, `sampling.py`, `app.py`, `cli.py` and `tools/__init__.py` ask `kind_for()`
instead of testing names; `app.py` returns `media` and `reference_label` from the table;
the duplicated precondition becomes one `preconditions()` call. `review.js` keeps reading
`kind` from the API, which it already does; its `TITLES` stays for display.
`final.render` keeps its explicit pipeline (order is a product rule) but takes the names
from `fixed()`.

Builds before the design's step 3 (captions) and step 4 (thumbnail), which then add
their kinds as table entries.

**Alternatives considered:** a `Decision` class hierarchy with methods per kind — lost:
the per-kind behaviour is data plus two callables, and a hierarchy spreads it across
classes again. Putting the facts on `Tool` — lost: a decision is not a tool (several
tools serve one kind, and `master` vs `audio` differ in upstream, not tool shape). Leave
it — lost: two more kinds are planned, each touching every branch.

**Testing strategy:** one table test asserting each kind's tool kind, media, reference
and upstream, and `kind_for` on `clip-<slug>` and unknown names. Existing
`test_project.py`, `test_sampling.py`, `test_server.py` and `test_studio_cli.py` must pass
unchanged — that is the proof the refactor changes nothing observable; no new mocks.

## Paths

As of 2026-09-30:

- `src/talk_studio/decisions.py` (new)
- `src/talk_studio/project.py`
- `src/talk_studio/sampling.py`
- `src/talk_studio/tools/__init__.py`
- `src/talk_studio/server/app.py`
- `src/talk_studio/cli.py`
- `src/talk_studio/final.py`
- `tests/test_decisions.py` (new)

## Approval

**Needs your yes** — the `Gate` question; recommended answer: yes, because the repo has
no `## Approval` section, the change is behaviour-preserving, and it should land before
captions and thumbnails add two more kinds to the scatter.

## Out of scope

- Adding the captions or thumbnail decisions themselves (design steps 3 and 4).
- Any change to the review page's look, titles or keys.
- Changing the final render order or what `project.toml` stores.
