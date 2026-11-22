# Write the YouTube caption file from word timings

**Priority:** medium — `youtube apply` (`95fec84`) uploads a caption track, but nothing in the repo can make one; the 438-cue file of the published talk was built outside the tool, and nothing that built it was kept
**Gate:** decision:Add `talk-studio captions srt <words.json>`, a plain command that turns word timings into a readable `.srt` (two lines, about 42 characters each), rather than a blind-judged captions decision?

**Goal:** The caption track `youtube.toml` points at comes from one command over the same `words.json` the clips already use, so publishing a talk with subtitles stops needing a one-off script per talk.

## Context

As of `c9b2fec` (2026-11-21).

- `youtube.py` reads `[[captions]] path = "captions.en.srt"` and `apply` inserts or
  replaces the track. Outside the design doc, `git grep -i srt` finds the format only
  there and in `tests/test_youtube.py`: no code writes an `.srt` or `.vtt`.
- The design (`docs/plans/ideas/talk-studio-design.md`, Tools table) names an
  `aligned-srt` tool under step 3 (captions), and its header line says steps 3 and 4
  "have no plan yet".
- The repo already has every input: `captions/models.py` `Word` (text with punctuation,
  start, end, score), `captions/align.py` `load_words`, and `Project.words`, set by
  `clips add --words` for the karaoke captions of Shorts. `captions/group.py`
  `group_words` cannot be reused as is: it makes karaoke cues of at most three words
  (`style.toml` `cues.max_words = 3`), which is the wrong shape for a caption file.
- The published talk shows the target: its reviewed caption file has 438 cues from 4,069
  words, never more than two lines, median line 30 characters (longest 47), median cue
  3.8 s (longest 7.0 s). Its project notes describe it as "an `.srt` built from the
  reviewed transcript, with Whisper's timings". 17 of its 4,069 words have zero length.
- The README states the product stance this follows: loudness "is a standard, not a
  matter of taste, so it is never a candidate". Caption-file segmentation is the same
  kind of thing; the taste decision in the design's step 3 is the burnt-in caption
  *style*, which this plan leaves alone.

Dependency category: in-process (pure function from words to text).

## Design

**Chosen: a pure segmenter and a thin command.**

```python
# illustrative — src/talk_studio/captions/srt.py
def reading_cues(words: list[Word], *, max_chars: int = 42, max_lines: int = 2,
                 max_seconds: float = 7.0, pause_break: float = 0.7) -> list[list[list[Word]]]
def render_srt(cues) -> str
```

- Break, in order of preference: at a sentence end (`group.ends_sentence`), at a pause
  longer than `pause_break`, at a comma, and only then on length. A cue holds at most
  `max_lines` lines of `max_chars`; the line break inside a cue goes at the most balanced
  word boundary.
- A cue ends at its last word's end, stretched to a readable minimum but never past the
  next cue's start; zero-length words (the aligner's collapse signature, see
  `captions/repair.py`) take their neighbours' timings instead of producing a
  zero-length cue.
- `talk-studio captions srt <words.json> --out captions.en.srt [--max-chars N]
  [--range 9:37-10:35.5]`; `--range` shifts times to zero so a clip can get its own file,
  and is parsed with `timecode.parse_excerpt` (the `parse_timecode` in `captions/cli.py`
  rejects decimals). Unlike `sample`, `render` and `align`, it takes no video or transcript.
  It reads only `words.json`, so it works for forced-aligned words
  (`.subtitler/<stem>/words.json`) and for reviewed Whisper timings alike.
- `.agents/skills/publishing-talks-to-youtube/SKILL.md` gets the step: build the `.srt`,
  read it once against the transcript, then `youtube check`.

**Alternatives considered:** a `captions` decision with an `aligned-srt` tool, as the
design's table sketches — lost: there is nothing to judge blind between two segmentations
of the same words, and it would add a decision kind (and a review view) for a file a human
proofreads as text. Reusing `group_words` with a large `max_words` — lost: it has no
notion of lines or characters. Writing WebVTT as well — deferred: YouTube takes `.srt`,
and one renderer is enough until something needs the other.

**Testing strategy:** `tests/test_srt.py` on hand-built `Word` lists: sentence, pause and
length breaks; two-line balance; no cue over the limits; no overlap and no zero-length
cue with collapsed words; `--range` shifting; exact SRT text for a three-cue example. One
CLI test through `captions.cli.main`, in `tests/test_cli.py`. Fast tests only: no model, no ffmpeg.

## Paths

As of 2026-11-21:

- `src/talk_studio/captions/srt.py` (new)
- `src/talk_studio/captions/cli.py`
- `tests/test_srt.py` (new)
- `tests/test_cli.py`
- `.agents/skills/publishing-talks-to-youtube/SKILL.md`
- `README.md` (the Captions section)
- `docs/plans/ideas/talk-studio-design.md` (Tools table: `aligned-srt` is a command)

## Approval

**Needs your yes** — the `Gate` question; recommended answer: yes, as a plain command,
because the repo has no `## Approval` section, the publish step already depends on this
file, and segmentation has a readability standard rather than a look to choose.

## Out of scope

- Burnt-in caption style as a decision (the rest of design step 3).
- Translating captions, or editing their text; the words are taken as given.
- Making `captions align` work for a language other than Spanish (`align.align` defaults
  to `language="es"` and the CLI has no flag for it).
- Uploading: `youtube apply` already does it.
