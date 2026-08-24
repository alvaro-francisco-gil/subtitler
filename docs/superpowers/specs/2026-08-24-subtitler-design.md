# subtitler — design

Date: 2026-08-24

## Purpose

Burn CapCut-style karaoke subtitles into a video, given the video and a full
written transcript, without using CapCut. The transcript already exists; the
job is to time it against the audio and render it.

Reusable: the video is an argument, not part of the tool. The reusable parts
are transcript cleaning, forced alignment, drift auditing, cue grouping and
the style config.

First target: `pregon_matamala.mov` — an 11:31 live speech in Spanish,
iPhone HEVC 10-bit, stored 1920x1080 with `rotation=-90`, i.e. displayed
1080x1920 vertical, 23.976 fps, AAC stereo 48 kHz.

## Non-goals

- No reframing or aspect-ratio conversion. The source is already vertical.
- No auto-transcription mode. A transcript is always supplied.
- No GUI.
- No multi-language configuration. Spanish is hardcoded as the alignment
  language for now; it becomes a flag only when a second language is needed.
- No per-frame renderer. See "Rejected alternatives".

## Look and feel

CapCut-style karaoke:

- 1-3 words on screen at a time, centered, large and bold.
- Each word pops in with a brief scale overshoot.
- The word currently being spoken is drawn in a highlight colour; the other
  words of the cue are in the base fill colour.
- Heavy outline plus drop shadow so text stays readable over any footage.

Default font is Montserrat ExtraBold, vendored into `assets/fonts/` from
Google Fonts (SIL Open Font License, redistributable). Arial Black and
Segoe UI Black are available from the host Windows font directory as
alternatives, selectable in the style config.

## Architecture

Five stages. Each is a separate module that reads and writes a file artifact,
so any stage can be inspected, hand-edited, and resumed from.

```
video.mov ------> [1] extract ------> audio.wav (16 kHz mono)
                        |
transcript.txt -> [2] clean --------> script.txt
                        |
audio + script -> [3] align --------> words.json + drift.md
                        |
words.json -----> [4] group --------> cues.json
                        |
cues + style ---> [5] render -------> subs.ass -> ffmpeg -> out.mp4
```

Artifacts live in a per-video work directory (`.subtitler/<video-stem>/`)
so reruns are cached and cheap.

### [1] extract

`ffmpeg` decodes the source to 16 kHz mono 16-bit WAV for the aligner.

Also probes the container and records, in `meta.json`:

- stored width/height
- the `rotation` side-data value
- the resulting **display** width/height
- frame rate, duration, pixel format, audio stream index

The rotation flag is the highest-risk detail in the whole pipeline: a
subtitle burn-in that ignores it produces text positioned for the wrong
canvas, or a video that silently loses its rotation metadata and plays
sideways. Display resolution is computed once here, from the metadata, and
every later stage consumes that value rather than re-deriving it.

### [2] clean

Turns the authored transcript into the sequence of words that were actually
spoken aloud.

- Drops section headings — lines matching a leading Roman numeral and period
  (`I. Una confesion, para empezar`), and the standalone title line.
- Drops blank lines and collapses whitespace.
- Normalises typographic quotes, guillemets and dashes to forms the aligner
  handles.
- Preserves sentence-final punctuation, because stage 4 uses it for cue
  breaks.

Emits `script.txt` plus `script_map.json`, mapping each output word index
back to its line and column in the original transcript, so the drift report
can cite locations in the file the user actually wrote.

### [3] align

Forced alignment of `script.txt` against `audio.wav` on the GPU
(RTX 4070 SUPER, 12 GB). Spanish wav2vec2 CTC alignment via `stable-ts`.

Output `words.json`: a flat list of `{word, start, end, score, script_index}`.

Output `drift.md`: the audit the user asked for. A live speech will not match
its script exactly — improvised asides, repeated words, skipped lines. The
report flags:

- runs of consecutive words whose alignment score falls below a threshold
- audio gaps inside what the script says is continuous speech
- words assigned implausible durations (far above or below the local median)

Each flagged span is reported with its timecode, the script text, and the
transcript line number, so the user can jump to that point in the video and
decide whether to hand-edit before rendering.

Drift is reported, never silently repaired. The subtitles say what the
transcript says; the report tells the user where that choice may be wrong.

### [4] group

Packs the word list into on-screen cues.

- Target 1-3 words per cue (configurable).
- Force a break at sentence-final punctuation.
- Force a break when the silence between two words exceeds a pause
  threshold.
- Never split a cue such that a single word is left stranded when the
  grouping could be balanced instead.

Output `cues.json`: `{start, end, words: [{text, start, end}]}`.

`cues.json` is renderer-agnostic. It is the contract that would let a
different renderer be added later without redoing alignment.

### [5] render

Writes an ASS subtitle file, then burns it in with one `ffmpeg` pass.

Per cue, the renderer emits one Dialogue event per word:

- `\pos` for placement, computed from measured text extents so the cue is
  centered as a group
- `\fscx`/`\fscy` with `\t` transforms for the pop-in scale overshoot
- the highlight colour on the word whose time window is current, base fill
  on the others
- `\bord` and `\shad` for outline and drop shadow

Burn-in uses the `subtitles` filter with the vendored fontdir. Encoding
defaults to NVENC HEVC to preserve the 10-bit source, with a libx264 8-bit
fallback flag for maximum compatibility.

`subs.ass` is kept as a deliverable in its own right — it can be re-burned,
edited by hand, or loaded into another editor.

## Style configuration

A single `style.toml` at the repo root (overridable with `--style`) holds
everything visual:

- font family, font file, size, letter spacing
- base fill colour, highlight colour, outline colour, shadow
- outline width, shadow depth and offset
- vertical position as a fraction of frame height
- pop scale factor and pop duration in milliseconds
- words per cue, pause-break threshold
- all-caps on/off

No visual constant lives in code.

## CLI

```
subtitler sample <video> <transcript> [--at 2:15] [--len 10] [--style FILE]
subtitler render <video> <transcript> [--out FILE] [--style FILE]
subtitler align  <video> <transcript>          # stages 1-3 only
```

`sample` is the primary workflow, not an afterthought. It renders only the
requested window. Alignment always covers the whole audio and is cached, so
the *first* `sample` on a given video pays the full alignment cost — a few
minutes — and every subsequent `sample` returns in seconds. The loop is: run `sample`, look at it, edit
`style.toml`, run `sample` again. `render` is what you run once you are happy.

`--at` defaults to the start of the first dense run of speech found in
`words.json`, rather than 0:00, because the opening of a live recording is
usually applause or silence and makes a poor style reference.

## Testing

Test-driven, per the project's normal workflow.

Unit tests, on pure logic with no ffmpeg or GPU involved:

- transcript cleaning: heading removal, punctuation normalisation, the
  index map back to source lines
- cue grouping: word counts, punctuation breaks, pause breaks, balancing
- ASS generation: escaping of braces and backslashes in subtitle text,
  timecode formatting, event ordering
- rotation math: stored dimensions plus rotation to display dimensions,
  covering 0, 90, -90 and 180

Integration tests:

- one end-to-end smoke test over roughly 15 seconds of real audio, asserting
  that an output file is produced, has the expected display resolution, and
  has a duration matching the requested window
- a drift-report test using a fixture where the audio deliberately omits a
  scripted sentence, asserting the omission is flagged

Fixtures are short clips committed to the repo, not the 1.27 GB source.

## Dependencies

- Python 3.12, managed with `uv`
- `stable-ts` (and its torch dependency, CUDA build) for forced alignment
- `ffmpeg` / `ffprobe` — currently available as `static_ffmpeg` /
  `static_ffprobe` from the `static-ffmpeg` package installed via `uv tool`;
  a system ffmpeg on PATH is preferred and is used when present
- no system-level installs required

## Rejected alternatives

**Per-frame renderer (Pillow or Skia compositing each frame, piped to
ffmpeg).** Gives unlimited visual control — gradients, blur, spring easing,
emoji. Rejected because the CapCut look is fully expressible in ASS, and
16,582 frames per render would turn the style-iteration loop from seconds
into many minutes, defeating the sample-first workflow that motivated the
tool. `cues.json` is deliberately renderer-agnostic so this can be added
later without redoing alignment.

**HTML/CSS animated in a headless browser, screenshotted per frame.** Highest
visual ceiling, worst performance, and the most fragile dependency chain.

**ASR-driven subtitles with the transcript used only for spelling
correction.** More robust to improvisation, but the user wants the written
text to be authoritative. Chosen compromise: force the written text, and
report where it disagrees with the audio.
