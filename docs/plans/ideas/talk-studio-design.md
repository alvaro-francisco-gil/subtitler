# talk-studio — design

**Priority:** medium

**Goal:** Turn `subtitler` into `talk-studio`, a local tool in which agents propose
edits to a recorded talk using several different tools, and a human picks what
feels right from rendered samples.

## Context

`subtitler` does one job: time a known transcript against the audio and burn in
karaoke subtitles. Editing a phone recording of a talk needs much more — audio
clean-up, colour grading, captions, thumbnails, vertical clips, publishing — and
most of those choices are matters of taste. An agent can generate options quickly
but cannot tell which one *feels* right. A human can, in seconds, if the options are
put side by side on the same material.

That split is the product: **agents explore, the human judges.** Every design choice
below serves it.

The caption pipeline already works: cached stages, a style file, forced alignment,
drift reporting, and tests with `gpu`/`slow` markers. It is kept and becomes one tool
among several.

## Concepts

- **Decision** — one choice that depends on feel: `audio`, `grade`, `captions`,
  `thumbnail`, `clip:<name>`. Status is `open` or `picked`, or `stale` when its pick no
  longer matches the source.
- **Candidate** — one attempt at a decision: a tool plus settings. Candidates are
  grouped into **rounds**; a round is what the human compares at once.
- **Excerpt** — a time range (or, for stills, a timestamp) that every candidate of a
  decision is rendered on, so candidates are compared on identical material.
- **Sample** — a candidate rendered on the decision's excerpts.
- **Pick** — the human's choice. Only picks feed the final render.
- **Feedback** — a pick or a rejection, each with an optional free-text note, recorded
  against a round. Agents read it to plan the next round.

## The loop

1. The agent chooses excerpts for a decision, or accepts the suggested ones.
2. The agent proposes a round of candidates. It should vary the *tool* as well as the
   settings: three strengths of one denoiser is a worse round than three denoisers.
3. The agent renders samples.
4. The human opens the review page, compares, and either picks one or rejects the
   round with a note.
5. On a rejection the agent reads the note and proposes the next round. On a pick
   the decision closes.

### Fair comparison

- Candidates are shown **blind**, labelled A, B, C. Tool names and settings are
  revealed after the human submits feedback for the round.
- Audio samples are **loudness-matched** at playback. The page applies a gain per
  sample from its measured integrated loudness, so a louder candidate does not win
  by being louder. Nothing is re-rendered for this.
- The **original** is always available as a reference.

## Tools

A tool is a Python adapter with a fixed contract:

| Member | Meaning |
|---|---|
| `kind` | `audio`, `grade`, `captions`, `thumbnail` or `clip` |
| `name` | unique, e.g. `ffmpeg-chain`, `deepfilternet` |
| `params` | typed settings: name, type, range or choices, default, one-line description |
| `requires` | binaries, models, GPU; checked by `doctor` |
| `sample(source, excerpts, settings, out_dir)` | renders the samples |
| `apply(source, settings, out_path)` | renders the full length |

Adapters stay thin. **Heavy models run in isolated environments** (invoked with `uvx`
or a per-tool `uv` project) because their torch and onnxruntime pins conflict. The
adapter shells out and validates the result.

Initial tools, in build order:

| Kind | Tools |
|---|---|
| audio | `ffmpeg-chain` (high-pass, `afftdn`/`arnndn` denoise, compressor, `loudnorm`), `deepfilternet` |
| grade | `ffmpeg-eq` (exposure, contrast, saturation, gamma, temperature, curves), `lut` |
| captions | `aligned-srt` (existing alignment → cues → `.srt`/`.vtt`), `burn-in` (existing ASS render) |
| thumbnail | `html-template` (template + cutout via rembg, rendered with headless Chromium) |
| clip | `vertical-karaoke` (9:16 crop + existing karaoke render) |

`resemble-enhance` is a later audio tool: it rebuilds voice and can change timbre or
smear words over long material, so it is only ever offered as a candidate, never used
by default.

## Project files

Text and renders are kept apart, so a project's text can live in another repository
and be committed there.

```text
<project>/
  project.toml                      source path and fingerprint, excerpts, decisions, picks
  candidates/<decision>/<id>.toml   tool, settings, round, proposer, created
  feedback/<decision>.jsonl         one line per verdict: round, candidate, pick|reject, note, at
  picks/                            text outputs of picks: captions.srt, thumbnail.html, …

~/.cache/talk-studio/<project-id>/
  samples/<decision>/<id>/          rendered samples plus loudness measurements
  renders/                          full-length outputs
```

- `project.toml` is the source of truth for picks. The UI and the CLI both write
  through one module that rewrites the file atomically.
- Render cache keys are a hash of source fingerprint, tool name, tool version,
  settings and excerpt. An identical request is a cache hit.
- The source fingerprint is size, duration and a hash of the first and last 8 MB.
  A mismatch marks every pick of the project `stale`.
- Final render order is fixed: `audio` and `grade` on the source, then `captions`,
  then `clip` and `thumbnail`, which inherit the picked grade.

Sketch of `project.toml`:

```toml
[source]
path = "/mnt/c/Users/…/talk.mp4"
fingerprint = "…"

[decisions.audio]
status = "picked"
excerpts = ["03:00-03:12", "09:36-09:50", "16:10-16:22", "00:05-00:17"]
pick = "c7"
```

## Excerpt suggestion

- **audio** — four excerpts of 10–15 s: the quietest stretch of speech, the loudest,
  the noisiest non-speech gap (highest noise floor from `astats`), and a change of
  speaker when a transcript with speaker turns exists.
- **grade** — six frames evenly spaced, plus the darkest and brightest by mean luma.
- The human can add an excerpt from the page with **Sample here**; it is appended to
  the decision and every candidate of the current round is re-rendered on it.

## Review page

A local FastAPI server (no front-end build step) serving
plain HTML and JavaScript. It runs in WSL and is opened from the Windows browser.

- **Audio:** all samples of an excerpt are decoded into Web Audio buffers and played
  in lock-step; keys `1`–`9` switch candidate without losing position, `0` is the
  original.
- **Grade:** one frame or short clip with a draggable before/after divider; the same
  keys switch candidate.
- **Thumbnail:** each candidate at full size and at the width of a phone feed tile.
- **Clip:** vertical players side by side, each with its transcript lines.
- **Every decision:** Pick, Reject with a note, Sample here, and the history of
  previous rounds with their notes.

The page reads and writes only through the server's API, which uses the same project
module as the CLI.

## Agent interface

```text
talk-studio init <video> --project <dir>
talk-studio tools [--kind audio]
talk-studio excerpts suggest <decision>
talk-studio propose <decision> --tool <name> --set key=value …
talk-studio sample <decision>
talk-studio review
talk-studio wait <decision>
talk-studio feedback <decision>
talk-studio render
talk-studio status
talk-studio doctor
```

- Every command prints a short human line and supports `--json` for agents.
- `wait` blocks until new feedback lands for the decision, so an agent loop does not
  poll.
- The repo ships an agent skill describing the loop and the "vary the tool" rule.
- Existing `subtitler align|sample|render` behaviour moves under the captions tools;
  the old command names are not kept.

## Error handling

- **A tool fails:** the candidate is marked `failed` with its log path; the page shows
  the log; other candidates are unaffected.
- **Missing requirement:** `doctor` names the binary, model or GPU capability and how
  to get it. `propose` refuses a tool whose requirements fail.
- **Invalid settings:** rejected at `propose` against the tool's `params`, before any
  render.
- **Broken output:** every render is validated — expected streams present, duration
  within one frame of the source or excerpt, audio and video lengths agree. A short
  render fails loudly, as `subtitler` already does.
- **Source changed:** picks become `stale` and are not used by `render` until
  re-confirmed.

## Testing

- **Tool contract tests** on generated media — a tone plus noise, colour bars, a
  three-second clip — run for every tool on every change.
- **Project module tests:** round-trip of `project.toml`, candidates and feedback;
  atomic writes; staleness.
- **Server API tests:** pick and reject write the right files; blind labels hide tool
  names until feedback is submitted.
- **GPU- and model-dependent tests** keep the existing `gpu` and `slow` markers.
- **One end-to-end test** on a five-second clip: init, two audio candidates, sample,
  pick through the API, render, validate.

## Repository

- `subtitler` is renamed `talk-studio`, locally and on GitHub. Its caption code moves
  into the `captions` and `clip` tools.
- Plans follow the `docs/plans/{ideas,ready,ongoing}/` lifecycle; the existing
  `docs/superpowers/` files move there when implementation starts.
- Nothing personal is committed to the tool repo: no source transcripts, recordings
  or project folders. Projects live beside the material they describe.

## Build order

Each step is its own plan and is usable on a real recording when it lands.

1. **Core and audio** — project module, tool contract, cache, CLI, review page with
   audio A/B, `ffmpeg-chain` and `deepfilternet`.
2. **Grade** — `ffmpeg-eq` and `lut`, with the divider view.
3. **Captions** — migrate alignment and burn-in into tools; caption style as a
   decision.
4. **Thumbnails** — `html-template` with cutout.
5. **Clips** — choose ranges from the transcript, 9:16 crop, karaoke.
6. **Publish** — YouTube captions, description and chapters.

## Out of scope

- Timeline editing: cuts, transitions, multi-track.
- More than one person reviewing at once.
- Cloud rendering or hosting.
- Automatic picking. The human always decides.
