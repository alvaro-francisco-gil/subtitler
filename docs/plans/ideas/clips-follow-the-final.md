# Clips follow the final render, and a picked clip can always be exported

**Priority:** medium — Shorts are the newest part of the loop (`54b7604`, `faeff35`, `ad2351a`, all 2026-09-17) and rest on a final file nothing tracks; once that file is re-rendered or its cache is gone, picked clips cannot be exported without a new human pick for each one
**Gate:** decision:Record which picks produced the final video, refuse to cut clips from a final that no longer matches them, and let `clips export` re-render a picked clip from its saved settings instead of requiring the old sample file?

**Goal:** A clip pick means "these settings", not "that cached file": re-rendering the talk, or moving to a new machine, never forces the human to pick the same Shorts again, and a clip is never silently cut from a half-written or outdated final.

## Context

As of `f8f4240` (2026-11-22), re-checked after `decision-kinds` merged (`84423ab`,
PR #2): it changed none of the four facts below, only how names are tested
(`decisions.kind_for`). Clips are decisions cut from the final render
(`tools/clips.py` docstring: "so they inherit the picked audio, mastering and grade").
Four facts in the code undo that promise:

1. **The final is written in place.** `final.render` hands ffmpeg the destination itself
   (`-y … str(out)`) and runs `media.validate` only afterwards. A crash, or a render that
   fails validation, leaves the file at `sampling.final_video(project)`. Every other
   cached render (samples, picked audio, working audio) goes through `sampling._build` (writer-unique partial file, then
   `os.replace`), the pattern `b6f3eb9` (Fix: give each ensure_sample writer its own
   partial-render filename) made safe for samples.
2. **Existence is the only check.** `sampling.clip_inputs` accepts the final when
   `video.exists()`. Nothing records which audio, master and grade picks made it, so
   after `talk-studio reopen grade` and a new pick, clip samples are still cut from the
   old grade with no warning until someone runs `render` again. `master` goes `stale` when `audio` is reopened
   (`Project.reopen`); clips have no equivalent.
3. **`--out` skips the cache.** `final.render` writes either to `--out` or to the cache
   path, never both. A talk rendered with `--out` leaves no final for clips, and `clip_inputs` answers "run
   `talk-studio render` first" although it was run.
4. **A picked clip is a cache file, and losing it is a dead end.** `cli.py`
   `command_clips` (export) copies `sampling.sample_file(...)`, whose key includes
   `sampling.identity(video)` — `media.fingerprint` of the final file (size, duration,
   first and last 8 MB). When the final is re-rendered to different bytes, the sample
   is missing and the error says ``run `talk-studio sample clip-<slug>` ``. When the
   whole cache is gone (new machine, or the project folder moved: `cache.project_dir`
   is keyed by its path), export first stops in `clip_inputs` ("run `talk-studio render`
   first") and reaches the same error once the final is rendered again. That command cannot work:
   `sampling.render_round` raises "has no open round; propose candidates first" for a
   picked decision, and `Project.propose` raises "already picked … run `talk-studio
   reopen`". The only path is reopen, propose the same settings again, sample, and have
   the human pick it again in the review page — per clip.

`AGENTS.md` (workspace) treats the machine as disposable and the cache lives in
`~/.cache/talk-studio/`, so case 4 is the normal state after a machine change: the
current machine has no `~/.cache/talk-studio/` at all, so every clip picked before it
is in that state (project folders live outside this repo; their count was not checked).
`tests/test_clips.py` `test_clips_are_cut_from_the_final_render` covers only the happy
path (final present, sample present).

Dependency category: local-substitutable — ffmpeg on generated media, as the existing
tests do.

## Design

**Chosen: the final becomes a tracked artifact; a clip pick is settings.**

- **Atomic and described.** `final.render` renders to a partial file beside the cache
  path, validates the partial, moves it into place, then writes a small sidecar
  (`<stem>-final.json`) with a key of what produced it: source fingerprint plus tool,
  version and settings of the picked `audio`, `master` and `grade` candidates (the same
  ingredients `cache.sample_key` already hashes). The sidecar is removed before the
  render starts, so a final without a matching sidecar is never trusted.
- **`--out` is a copy.** The render always lands in the cache; `--out` copies it.
- **Clips check the key.** `sampling.clip_inputs` compares the sidecar's key with the
  key of the project's current picks and raises a `ProjectError` naming the difference
  ("the grade pick changed since the last render; run `talk-studio render`"). The review
  page already shows that text through `blocked` in `server/app.py` `decision_view`
  (`review.js` puts it in the decision status line).
- **Clip samples are keyed by that key**, not by the final's bytes, in
  `sampling.sample_file` — so rendering the same picks again does not orphan every clip
  sample.
- **Export renders what is missing.** `command_clips` export calls
  `sampling.ensure_sample` for each picked clip, so a missing file is rebuilt from the
  candidate's saved tool and settings. When the tool's current `version` differs from the
  candidate's recorded `tool_version`, export says so per clip instead of staying silent.

```text
# illustrative
$ talk-studio clips export --out shorts/
shorts/promise-big.mp4            (re-rendered from the pick: follow-speaker v2)
shorts/variable-names.mp4
```

**Alternatives considered:** a content-addressed final (`<stem>-final-<key>.mp4`), like
the picked audio in `sampling.picked_audio` — lost: every changed pick would leave another
multi-GB video in the cache, and the file name people upload would stop being stable.
Marking clip decisions `stale` when a pick upstream changes, like `master` — lost: it asks
the human to pick again for something that is a deterministic re-render of settings they
already chose. Fixing only the error message in export — lost: it leaves one re-pick per
clip as the answer.

**Testing strategy:** in `tests/test_final.py`: a render that fails validation leaves no
final and no sidecar; `--out` leaves both the copy and the cached final. In
`tests/test_clips.py`: after a pick, delete the clip's sample and export again (the file
comes back); change the grade pick without rendering and expect `clip_inputs` to name it.
`test_clips_are_cut_from_the_final_render` currently fakes a final by copying the source
into the cache; it switches to a helper that also writes the sidecar.

## Paths

As of 2026-11-22:

- `src/talk_studio/final.py`
- `src/talk_studio/sampling.py`
- `src/talk_studio/cli.py`
- `tests/test_final.py`
- `tests/test_clips.py`
- `.agents/skills/talk-studio/SKILL.md` (step 10: export rebuilds picked clips)

## Approval

**Needs your yes** — the `Gate` question; recommended answer: yes, because the repo has no
`## Approval` section and this changes what `render --out` and `clips export` do, and
because without it the Shorts of an already-published talk cannot be reproduced without
re-judging them.

## Out of scope

- Moving a project's video to a new path (`relink-project-source`).
- The decision-kind table itself (`src/talk_studio/decisions.py`, landed in `84423ab`).
  `decisions.preconditions()` already calls `sampling.clip_inputs`, so the key check
  added there reaches `render_round` and the review page with no further wiring; the
  export branch tests `decisions.kind_for(name) is decisions.CLIP` instead of the
  `clip-` prefix.
- Making encodes byte-reproducible.
- Any change to how clips look or to the review page.
