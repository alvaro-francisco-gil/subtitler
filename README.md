# subtitler

Burn karaoke-style subtitles into a video from a transcript you already have.

You supply the video and the written text. The tool finds when each word was
spoken, packs the words into short on-screen cues, and burns them in — big,
bold, centred, with the current word highlighted. It exists to replace a
round-trip through CapCut for footage that already has an authored script.

## What it does

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

Each stage writes a file into a per-video cache directory
(`.subtitler/<video-stem>/` beside the video), so you can stop, inspect,
hand-edit and resume. Alignment is the slow stage and is cached: the first run
on a video costs a few minutes, every run after it costs seconds.

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- An NVIDIA GPU for alignment (CPU works but is slow)
- **A literal `ffmpeg` and `ffprobe` on your PATH.**

That last one is not optional and is easy to get wrong. `stable-ts` shells out
to a binary named exactly `ffmpeg` when it loads audio. The `static_ffmpeg`
fallback inside `binaries.py` only covers *this project's* subprocess calls,
not a third-party library's — so on a machine with only `static-ffmpeg`
installed, alignment fails with `FileNotFoundError: 'ffmpeg'`. Fix it with:

```bash
ln -s ~/.local/bin/static_ffmpeg  ~/.local/bin/ffmpeg
ln -s ~/.local/bin/static_ffprobe ~/.local/bin/ffprobe
```

or install a real ffmpeg (`sudo apt install ffmpeg`).

## Install

```bash
uv sync
```

The first sync pulls torch, which is large. The first alignment downloads the
Whisper `large-v3` weights (~2.9 GB) into `~/.cache/whisper/`.

## Use

The sample loop is the point. Render ten seconds, look at it, edit
`style.toml`, repeat — then render the whole thing once you are happy.

```bash
# align only: produces words.json and the drift report, renders nothing
uv run subtitler align video.mov transcript.txt

# a short sample to judge the style (defaults to the densest speech it finds)
uv run subtitler sample video.mov transcript.txt --len 10
uv run subtitler sample video.mov transcript.txt --at 2:15 --len 10

# the whole video
uv run subtitler render video.mov transcript.txt
```

Useful flags: `--style FILE` to use a different style file, `--out FILE` to
choose the output path, `--force` to ignore cached artifacts, `--encoder
libx264` if NVENC is unavailable.

`--at` accepts `90`, `1:30` or `1:02:03`. With no `--at`, the sample starts at
the densest run of speech, because the opening of a live recording is usually
applause and makes a poor style reference.

## Style

Everything visual lives in `style.toml`. No look-and-feel constant is
hardcoded in Python.

| Key | Meaning |
| --- | --- |
| `font.family` | Family name as libass sees it |
| `font.file` | Path to the font, relative to `style.toml` |
| `font.size` | Point size on a 1080x1920 canvas |
| `colour.fill` | Words in the cue that are not currently spoken |
| `colour.highlight` | The word being spoken right now |
| `colour.outline` | Outline colour |
| `layout.position` | Vertical centre of the text, as a fraction of frame height |
| `layout.outline_width` | Outline thickness |
| `layout.shadow_depth` | Drop shadow depth |
| `layout.all_caps` | Uppercase the subtitles |
| `animation.pop_scale` | Peak scale of the pop-in overshoot |
| `animation.pop_ms` | Duration of the pop-in |
| `cues.max_words` | Words on screen at once |
| `cues.pause_break` | Silence longer than this forces a cue break, in seconds |

The bundled font is Montserrat ExtraBold, vendored under the SIL Open Font
License. It is a *static* instance built from the Google Fonts variable font,
because libass cannot be relied on to select a non-default weight from a
variable font.

## Reading drift.md

The subtitles say what your transcript says — even where you improvised,
repeated yourself, or skipped a line. `drift.md` is the report that tells you
where that choice may be wrong. Drift is reported, never silently repaired.

It flags three things, each with a timecode and a transcript line number:

- **`low-confidence`** — a run of words the aligner was guessing at. Usually
  means the audio departs from the script there.
- **`gap`** — a long silence inside what the script says is continuous speech.
  Either a real pause, or something said that is not in the transcript.
- **`long-word`** — a word stretched implausibly long, the signature of the
  aligner failing to match audio. Flagged only above an absolute floor as well
  as a multiple of the median, because word durations are right-skewed and a
  relative threshold alone floods the report with ordinary speech.

Jump to each timecode, decide whether to edit `transcript.txt`, and re-run
`align`. A clean report means the audio follows the transcript closely.

## Colour, and what a burn-in costs

Output is **always SDR BT.709**.

If your source is HDR (an iPhone recording is typically Dolby Vision over an
HLG BT.2020 base layer), two things happen and only one of them is a choice:

- **The Dolby Vision layer is destroyed.** Burning in subtitles means decode,
  filter, re-encode, and no filter graph preserves the DV RPU. This is a
  property of the operation, not a setting.
- **The base layer is tone-mapped to SDR.** This *is* a choice. An HLG
  BT.2020-tagged file is handled inconsistently downstream — players that
  ignore the tags render it washed out — so the output is normalised to
  BT.709 and looks the same everywhere, at the cost of HDR range.

Tone-mapping runs *before* the subtitle filter, so subtitle colours are
composited in SDR and mean exactly what `style.toml` says.

Rotation is handled by ffmpeg's automatic rotation on decode. The pipeline
reads the display resolution from the Display Matrix side data so the subtitle
canvas matches, and never applies a `transpose` of its own.

## Tests

```bash
uv run pytest -m "not gpu and not slow"   # fast, no GPU, no ffmpeg work
uv run pytest -m slow                     # real ffmpeg on a short clip
uv run pytest -m gpu                      # real alignment; downloads weights
uv run pytest                             # everything
```
