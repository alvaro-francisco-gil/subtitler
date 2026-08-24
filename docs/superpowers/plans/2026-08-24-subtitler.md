# Subtitler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A command-line tool that burns CapCut-style karaoke subtitles into a video by force-aligning a supplied transcript to the audio, with a fast sample-render loop for style iteration.

**Architecture:** Five stages — extract, clean, align, group, render — each a module that reads and writes a file artifact in a per-video cache directory, so any stage can be inspected, hand-edited and resumed from. Pure logic (cleaning, grouping, ASS generation, colour and rotation maths) is separated from the I/O-bound stages (ffmpeg, GPU alignment) so the bulk of the code is unit-testable without a GPU or a video file.

**Tech Stack:** Python 3.12, `uv`, `stable-ts` (GPU forced alignment), Pillow (text measurement), ffmpeg/ffprobe (probe, extract, burn), ASS/libass (subtitle rendering), pytest.

**Spec:** `docs/superpowers/specs/2026-08-24-subtitler-design.md`

## Global Constraints

- Python **3.12**. `tomllib` is stdlib; do not add a TOML dependency.
- Output is **always SDR BT.709**. Never emit BT.2020 or HLG.
- Tone-mapping filters MUST precede the `subtitles` filter in the chain.
- Never apply a `transpose` filter. ffmpeg auto-rotates on decode.
- Rotation is read from `stream_side_data` → `Display Matrix` → `rotation`, never from `stream_tags.rotate`.
- ASS canvas (`PlayResX`/`PlayResY`) uses **display** dimensions (1080x1920 for the reference video), not stored dimensions (1920x1080).
- No visual constant may be hardcoded in Python. All look-and-feel lives in `style.toml`.
- ffmpeg binaries are resolved at runtime: prefer `ffmpeg`/`ffprobe` on PATH, fall back to `static_ffmpeg`/`static_ffprobe`.
- Alignment language is `es`.
- Reference video for manual checks: `/mnt/c/Users/alvar/Downloads/pregon_matamala.mov` (1.27 GB — never commit it, never copy it into the repo).

## File Structure

```
subtitler/
  pyproject.toml
  style.toml                      # all look-and-feel; no visual constants in code
  README.md
  .gitignore
  assets/fonts/Montserrat-ExtraBold.ttf
  src/subtitler/
    __init__.py
    binaries.py    # locate ffmpeg/ffprobe; run subprocesses
    probe.py       # ffprobe JSON -> MediaInfo (rotation, display dims, colour)
    extract.py     # video -> 16 kHz mono wav
    clean.py       # raw transcript -> spoken script + source position map
    models.py      # Word, Cue, Flag dataclasses shared across stages
    group.py       # words -> cues
    style.py       # style.toml -> Style dataclass; colour conversion
    measure.py     # Pillow text width measurement
    ass.py         # cues + style -> .ass document (pure; measurement injected)
    align.py       # forced alignment via stable-ts
    drift.py       # low-confidence / gap / duration audit -> drift.md
    render.py      # filter chain assembly + ffmpeg burn-in
    workdir.py     # per-video cache directory and artifact paths
    cli.py         # sample / render / align subcommands
  tests/
    fixtures/
      ffprobe_rotated.json
      ffprobe_unrotated.json
      transcript_sample.txt
      clip.mov                    # ~15 s cut from the reference video
    test_probe.py
    test_clean.py
    test_group.py
    test_style.py
    test_ass.py
    test_drift.py
    test_render.py
    test_e2e.py
```

`models.py` holds the dataclasses that cross stage boundaries, so `group.py`, `drift.py` and `ass.py` share one vocabulary instead of each defining its own. `ass.py` takes a text-measurement callable as an argument rather than importing Pillow, which keeps it a pure function testable with a stub.

---

### Task 1: Project scaffold and binary discovery

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/subtitler/__init__.py`, `src/subtitler/binaries.py`
- Test: `tests/test_binaries.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `binaries.ffmpeg() -> str` — path/name of the ffmpeg executable
  - `binaries.ffprobe() -> str` — path/name of the ffprobe executable
  - `binaries.run(args: list[str]) -> subprocess.CompletedProcess` — runs and raises `BinaryError` on non-zero exit, with stderr in the message
  - `binaries.BinaryError(Exception)`

- [ ] **Step 1: Create the project scaffold**

`pyproject.toml`:

```toml
[project]
name = "subtitler"
version = "0.1.0"
description = "Burn karaoke-style subtitles into a video from a supplied transcript"
requires-python = ">=3.12"
dependencies = [
    "stable-ts>=2.17",
    "pillow>=10.0",
]

[project.scripts]
subtitler = "subtitler.cli:main"

[dependency-groups]
dev = ["pytest>=8.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/subtitler"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "gpu: requires a CUDA device and downloads a model",
    "slow: invokes ffmpeg on a real clip",
]
```

`.gitignore`:

```
__pycache__/
*.pyc
.venv/
.subtitler/
dist/
*.mov
*.mp4
!tests/fixtures/clip.mov
```

`src/subtitler/__init__.py`: empty file.

- [ ] **Step 2: Write the failing test**

`tests/test_binaries.py`:

```python
import pytest

from subtitler import binaries


def test_ffmpeg_prefers_path_binary(monkeypatch):
    monkeypatch.setattr(binaries.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
    assert binaries.ffmpeg() == "/usr/bin/ffmpeg"


def test_ffmpeg_falls_back_to_static_build(monkeypatch):
    def which(name):
        return "/home/u/.local/bin/static_ffmpeg" if name == "static_ffmpeg" else None

    monkeypatch.setattr(binaries.shutil, "which", which)
    assert binaries.ffmpeg() == "/home/u/.local/bin/static_ffmpeg"


def test_ffprobe_falls_back_to_static_build(monkeypatch):
    def which(name):
        return "/home/u/.local/bin/static_ffprobe" if name == "static_ffprobe" else None

    monkeypatch.setattr(binaries.shutil, "which", which)
    assert binaries.ffprobe() == "/home/u/.local/bin/static_ffprobe"


def test_missing_binary_raises(monkeypatch):
    monkeypatch.setattr(binaries.shutil, "which", lambda name: None)
    with pytest.raises(binaries.BinaryError, match="ffmpeg"):
        binaries.ffmpeg()


def test_run_raises_with_stderr_on_failure():
    with pytest.raises(binaries.BinaryError, match="no-such-flag"):
        binaries.run(["python3", "-c", "import sys; sys.stderr.write('no-such-flag'); sys.exit(2)"])


def test_run_returns_stdout_on_success():
    result = binaries.run(["python3", "-c", "print('ok')"])
    assert result.stdout.strip() == "ok"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_binaries.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'subtitler.binaries'`

- [ ] **Step 4: Write the implementation**

`src/subtitler/binaries.py`:

```python
"""Locating and invoking the ffmpeg toolchain.

A system ffmpeg is preferred. The `static-ffmpeg` package installs its
binaries under the names `static_ffmpeg` and `static_ffprobe`, which is how
this machine currently provides them, so those are the fallback.
"""

from __future__ import annotations

import shutil
import subprocess


class BinaryError(Exception):
    """A required external binary is missing, or a run of one failed."""


def _find(*names: str) -> str:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    raise BinaryError(
        f"could not find any of {', '.join(names)} on PATH. "
        f"Install ffmpeg (apt install ffmpeg) or run: uv tool install static-ffmpeg"
    )


def ffmpeg() -> str:
    return _find("ffmpeg", "static_ffmpeg")


def ffprobe() -> str:
    return _find("ffprobe", "static_ffprobe")


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing output. Raises BinaryError on non-zero exit."""
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise BinaryError(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stderr}"
        )
    return result
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_binaries.py -v`
Expected: PASS, 6 tests

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore src/subtitler/__init__.py src/subtitler/binaries.py tests/test_binaries.py
git commit -m "feat: project scaffold and ffmpeg binary discovery"
```

---

### Task 2: Probe — rotation, display dimensions, colour

**Files:**
- Create: `src/subtitler/probe.py`, `tests/fixtures/ffprobe_rotated.json`, `tests/fixtures/ffprobe_unrotated.json`
- Test: `tests/test_probe.py`

**Interfaces:**
- Consumes: `binaries.ffprobe()`, `binaries.run()`
- Produces:
  - `probe.MediaInfo` — frozen dataclass with fields `stored_width: int`, `stored_height: int`, `rotation: int`, `display_width: int`, `display_height: int`, `fps: float`, `duration: float`, `color_transfer: str`, `color_primaries: str`, `is_hdr: bool`
  - `probe.rotation_from_side_data(stream: dict) -> int`
  - `probe.display_dimensions(width: int, height: int, rotation: int) -> tuple[int, int]`
  - `probe.parse_probe_json(payload: dict) -> MediaInfo`
  - `probe.probe(path: Path) -> MediaInfo`

- [ ] **Step 1: Create the fixtures**

`tests/fixtures/ffprobe_rotated.json` — a trimmed capture of the reference video's real ffprobe output:

```json
{
  "streams": [
    {
      "codec_type": "video",
      "width": 1920,
      "height": 1080,
      "r_frame_rate": "24000/1001",
      "duration": "691.135000",
      "color_transfer": "arib-std-b67",
      "color_primaries": "bt2020",
      "color_space": "bt2020nc",
      "tags": {},
      "side_data_list": [
        {"side_data_type": "DOVI configuration record", "dv_profile": 8},
        {"side_data_type": "Display Matrix", "rotation": -90},
        {"side_data_type": "Ambient viewing environment"}
      ]
    },
    {"codec_type": "audio", "sample_rate": "48000", "channels": 2}
  ],
  "format": {"duration": "691.135000"}
}
```

`tests/fixtures/ffprobe_unrotated.json`:

```json
{
  "streams": [
    {
      "codec_type": "video",
      "width": 1920,
      "height": 1080,
      "r_frame_rate": "30000/1001",
      "duration": "12.000000",
      "color_transfer": "bt709",
      "color_primaries": "bt709",
      "color_space": "bt709",
      "tags": {}
    },
    {"codec_type": "audio", "sample_rate": "44100", "channels": 2}
  ],
  "format": {"duration": "12.000000"}
}
```

- [ ] **Step 2: Write the failing test**

`tests/test_probe.py`:

```python
import json
from pathlib import Path

import pytest

from subtitler import probe

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize(
    "width,height,rotation,expected",
    [
        (1920, 1080, 0, (1920, 1080)),
        (1920, 1080, 90, (1080, 1920)),
        (1920, 1080, -90, (1080, 1920)),
        (1920, 1080, 270, (1080, 1920)),
        (1920, 1080, 180, (1920, 1080)),
        (1920, 1080, -180, (1920, 1080)),
    ],
)
def test_display_dimensions(width, height, rotation, expected):
    assert probe.display_dimensions(width, height, rotation) == expected


def test_rotation_read_from_display_matrix_not_tags():
    stream = load("ffprobe_rotated.json")["streams"][0]
    assert probe.rotation_from_side_data(stream) == -90


def test_rotation_defaults_to_zero_without_display_matrix():
    stream = load("ffprobe_unrotated.json")["streams"][0]
    assert probe.rotation_from_side_data(stream) == 0


def test_parse_rotated_hdr_source():
    info = probe.parse_probe_json(load("ffprobe_rotated.json"))
    assert (info.stored_width, info.stored_height) == (1920, 1080)
    assert (info.display_width, info.display_height) == (1080, 1920)
    assert info.rotation == -90
    assert info.fps == pytest.approx(23.976, abs=0.001)
    assert info.duration == pytest.approx(691.135)
    assert info.is_hdr is True


def test_parse_plain_sdr_source():
    info = probe.parse_probe_json(load("ffprobe_unrotated.json"))
    assert (info.display_width, info.display_height) == (1920, 1080)
    assert info.rotation == 0
    assert info.is_hdr is False
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_probe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'subtitler.probe'`

- [ ] **Step 4: Write the implementation**

`src/subtitler/probe.py`:

```python
"""Reading the metadata the rest of the pipeline depends on.

Two details of the reference video drive this module:

* Rotation lives in stream side data as a Display Matrix entry, not in
  `stream_tags.rotate`. Reading the tag would silently report 0.
* ffmpeg auto-rotates on decode, so the burn-in sees an upright frame. What
  the pipeline needs from rotation is the *display* resolution, used for the
  ASS canvas and all layout maths. No transpose is ever applied.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from . import binaries

# Transfer functions that indicate an HDR source needing tone-mapping.
HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}


@dataclass(frozen=True)
class MediaInfo:
    stored_width: int
    stored_height: int
    rotation: int
    display_width: int
    display_height: int
    fps: float
    duration: float
    color_transfer: str
    color_primaries: str
    is_hdr: bool


def display_dimensions(width: int, height: int, rotation: int) -> tuple[int, int]:
    """Apply a rotation to stored dimensions to get displayed dimensions."""
    if abs(rotation) % 180 == 90:
        return height, width
    return width, height


def rotation_from_side_data(stream: dict) -> int:
    """Extract rotation from a Display Matrix side-data entry, or 0 if absent."""
    for entry in stream.get("side_data_list", []):
        if entry.get("side_data_type") == "Display Matrix":
            return int(entry.get("rotation", 0))
    return 0


def parse_probe_json(payload: dict) -> MediaInfo:
    video = next(s for s in payload["streams"] if s.get("codec_type") == "video")

    stored_width = int(video["width"])
    stored_height = int(video["height"])
    rotation = rotation_from_side_data(video)
    display_width, display_height = display_dimensions(stored_width, stored_height, rotation)

    fps = float(Fraction(video.get("r_frame_rate", "0/1")))
    duration = float(video.get("duration") or payload["format"]["duration"])

    transfer = video.get("color_transfer", "")
    primaries = video.get("color_primaries", "")

    return MediaInfo(
        stored_width=stored_width,
        stored_height=stored_height,
        rotation=rotation,
        display_width=display_width,
        display_height=display_height,
        fps=fps,
        duration=duration,
        color_transfer=transfer,
        color_primaries=primaries,
        is_hdr=transfer in HDR_TRANSFERS,
    )


def probe(path: Path) -> MediaInfo:
    result = binaries.run([
        binaries.ffprobe(),
        "-v", "error",
        "-show_streams",
        "-show_format",
        "-of", "json",
        str(path),
    ])
    return parse_probe_json(json.loads(result.stdout))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_probe.py -v`
Expected: PASS, 11 tests (6 parametrised + 5)

- [ ] **Step 6: Verify against the real file**

Run:

```bash
uv run python -c "
from pathlib import Path
from subtitler.probe import probe
print(probe(Path('/mnt/c/Users/alvar/Downloads/pregon_matamala.mov')))
"
```

Expected: `rotation=-90`, `display_width=1080`, `display_height=1920`, `is_hdr=True`.

- [ ] **Step 7: Commit**

```bash
git add src/subtitler/probe.py tests/test_probe.py tests/fixtures/ffprobe_rotated.json tests/fixtures/ffprobe_unrotated.json
git commit -m "feat: probe rotation, display dimensions and colour metadata"
```

---

### Task 3: Work directory and audio extraction

**Files:**
- Create: `src/subtitler/workdir.py`, `src/subtitler/extract.py`
- Test: `tests/test_workdir.py`, `tests/test_extract.py`

**Interfaces:**
- Consumes: `binaries.ffmpeg()`, `binaries.run()`
- Produces:
  - `workdir.WorkDir` — dataclass with `root: Path` and properties `meta`, `audio`, `script`, `script_map`, `words`, `drift`, `cues`, `ass`, each a `Path`
  - `workdir.for_video(video: Path, base: Path | None = None) -> WorkDir` — creates `.subtitler/<video-stem>/` next to the video
  - `workdir.is_fresh(artifact: Path, *sources: Path) -> bool` — True when the artifact exists and is newer than every source
  - `extract.extract_audio(video: Path, out: Path) -> Path` — 16 kHz mono 16-bit WAV

- [ ] **Step 1: Write the failing tests**

`tests/test_workdir.py`:

```python
from subtitler import workdir


def test_for_video_creates_named_directory(tmp_path):
    video = tmp_path / "pregon_matamala.mov"
    video.write_bytes(b"")

    wd = workdir.for_video(video)

    assert wd.root == tmp_path / ".subtitler" / "pregon_matamala"
    assert wd.root.is_dir()


def test_artifact_paths_live_in_the_root(tmp_path):
    video = tmp_path / "clip.mov"
    video.write_bytes(b"")

    wd = workdir.for_video(video)

    assert wd.audio == wd.root / "audio.wav"
    assert wd.words == wd.root / "words.json"
    assert wd.ass == wd.root / "subs.ass"
    assert wd.drift == wd.root / "drift.md"


def test_is_fresh_false_when_artifact_missing(tmp_path):
    source = tmp_path / "s.txt"
    source.write_text("x")
    assert workdir.is_fresh(tmp_path / "nope.json", source) is False


def test_is_fresh_false_when_source_is_newer(tmp_path):
    artifact = tmp_path / "a.json"
    artifact.write_text("x")
    source = tmp_path / "s.txt"
    source.write_text("y")
    import os
    os.utime(artifact, (1, 1))
    assert workdir.is_fresh(artifact, source) is False


def test_is_fresh_true_when_artifact_is_newer(tmp_path):
    source = tmp_path / "s.txt"
    source.write_text("y")
    artifact = tmp_path / "a.json"
    artifact.write_text("x")
    import os
    os.utime(source, (1, 1))
    assert workdir.is_fresh(artifact, source) is True
```

`tests/test_extract.py`:

```python
from pathlib import Path

import pytest

from subtitler import extract, probe

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_builds_16k_mono_command(monkeypatch):
    captured = {}

    def fake_run(args):
        captured["args"] = args

    monkeypatch.setattr(extract.binaries, "run", fake_run)
    monkeypatch.setattr(extract.binaries, "ffmpeg", lambda: "ffmpeg")

    extract.extract_audio(Path("in.mov"), Path("out.wav"))

    args = captured["args"]
    assert "-ar" in args and args[args.index("-ar") + 1] == "16000"
    assert "-ac" in args and args[args.index("-ac") + 1] == "1"
    assert "-vn" in args
    assert args[-1] == "out.wav"


@pytest.mark.slow
def test_extract_produces_real_audio(tmp_path):
    out = tmp_path / "audio.wav"
    extract.extract_audio(FIXTURES / "clip.mov", out)

    assert out.exists() and out.stat().st_size > 0
    info = extract.audio_info(out)
    assert info["sample_rate"] == 16000
    assert info["channels"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_workdir.py tests/test_extract.py -v -m "not slow"`
Expected: FAIL — `ModuleNotFoundError: No module named 'subtitler.workdir'`

- [ ] **Step 3: Write the implementation**

`src/subtitler/workdir.py`:

```python
"""Per-video cache directory.

Every stage writes one artifact here. Because each stage's output is a file
on disk, a run can be stopped, its intermediate output inspected or
hand-edited, and resumed. Freshness is decided by mtime, which is what makes
the sample loop fast: alignment is paid for once per video.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkDir:
    root: Path

    @property
    def meta(self) -> Path:
        return self.root / "meta.json"

    @property
    def audio(self) -> Path:
        return self.root / "audio.wav"

    @property
    def script(self) -> Path:
        return self.root / "script.txt"

    @property
    def script_map(self) -> Path:
        return self.root / "script_map.json"

    @property
    def words(self) -> Path:
        return self.root / "words.json"

    @property
    def drift(self) -> Path:
        return self.root / "drift.md"

    @property
    def cues(self) -> Path:
        return self.root / "cues.json"

    @property
    def ass(self) -> Path:
        return self.root / "subs.ass"


def for_video(video: Path, base: Path | None = None) -> WorkDir:
    base = base or video.parent
    root = base / ".subtitler" / video.stem
    root.mkdir(parents=True, exist_ok=True)
    return WorkDir(root=root)


def is_fresh(artifact: Path, *sources: Path) -> bool:
    """True when `artifact` exists and is at least as new as every source."""
    if not artifact.exists():
        return False
    artifact_mtime = artifact.stat().st_mtime
    return all(
        source.exists() and source.stat().st_mtime <= artifact_mtime
        for source in sources
    )
```

`src/subtitler/extract.py`:

```python
"""Decoding the source audio for the aligner.

16 kHz mono is what wav2vec2-based forced alignment expects; feeding it
48 kHz stereo just makes the aligner resample internally.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import binaries


def extract_audio(video: Path, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    binaries.run([
        binaries.ffmpeg(),
        "-y",
        "-v", "error",
        "-i", str(video),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(out),
    ])
    return out


def audio_info(path: Path) -> dict:
    result = binaries.run([
        binaries.ffprobe(),
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels",
        "-of", "json",
        str(path),
    ])
    stream = json.loads(result.stdout)["streams"][0]
    return {"sample_rate": int(stream["sample_rate"]), "channels": int(stream["channels"])}
```

- [ ] **Step 4: Run the fast tests to verify they pass**

Run: `uv run pytest tests/test_workdir.py tests/test_extract.py -v -m "not slow"`
Expected: PASS, 6 tests

- [ ] **Step 5: Create the test clip fixture**

The `slow` tests need a small real clip. Cut 15 seconds of dense speech from the reference video:

```bash
mkdir -p tests/fixtures
$(uv run python -c "from subtitler.binaries import ffmpeg; print(ffmpeg())") \
  -y -v error -ss 200 -t 15 \
  -i /mnt/c/Users/alvar/Downloads/pregon_matamala.mov \
  -c copy tests/fixtures/clip.mov
ls -lh tests/fixtures/clip.mov
```

Expected: a file of roughly 25 MB. `-c copy` preserves the rotation side data and the HLG/DV tagging, which is exactly what the tests need to exercise.

- [ ] **Step 6: Run the slow test to verify it passes**

Run: `uv run pytest tests/test_extract.py -v -m slow`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/subtitler/workdir.py src/subtitler/extract.py tests/test_workdir.py tests/test_extract.py tests/fixtures/clip.mov
git commit -m "feat: per-video work directory and audio extraction"
```

---

### Task 4: Transcript cleaning

**Files:**
- Create: `src/subtitler/clean.py`, `tests/fixtures/transcript_sample.txt`
- Test: `tests/test_clean.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `clean.SourcePos` — frozen dataclass with `line: int` (1-indexed), `column: int` (1-indexed)
  - `clean.CleanResult` — frozen dataclass with `text: str` and `positions: list[SourcePos]`, one position per whitespace-separated word in `text`
  - `clean.is_heading(line: str) -> bool`
  - `clean.normalise(text: str) -> str`
  - `clean.clean_transcript(raw: str) -> CleanResult`

**Cleaning rules** (implement exactly these, no others):

1. A line is a heading, and is dropped, when it matches `^\s*[IVXLCDM]+\.\s+\S` — e.g. `II. El nombre`, `VII. Cierre: Matamala libre`.
2. The first non-empty line is also dropped when it does not end in `.`, `!`, `?` or `:` — this catches the document title, e.g. `Pregón de las fiestas de Matamala`.
3. Typographic characters are normalised: `«»""` → `"`, `''` → `'`, `—–` → `-`, `…` → `...`.
4. Blank lines are dropped and runs of whitespace collapse to a single space.
5. Everything else is kept verbatim, **including sentence-final punctuation**, which Task 5 uses to decide cue breaks.

- [ ] **Step 1: Create the fixture**

`tests/fixtures/transcript_sample.txt` — a short excerpt exercising every rule:

```
Pregón de las fiestas de Matamala

Vecinas y vecinos de Matamala.

I. Una confesión, para empezar

Yo no soy de Matamala. Mi familia ha nacido en Matabuena.

II. El nombre

Dijo que quería sumar su voz a la protesta contra —y cito— «la brutal
agresión a la Naturaleza».

¡Viva Matamala!
```

- [ ] **Step 2: Write the failing test**

`tests/test_clean.py`:

```python
from pathlib import Path

import pytest

from subtitler import clean

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "line,expected",
    [
        ("I. Una confesión, para empezar", True),
        ("II. El nombre", True),
        ("VII. Cierre: Matamala libre", True),
        ("Vecinas y vecinos de Matamala.", False),
        ("I am not a heading", False),
        ("", False),
    ],
)
def test_is_heading(line, expected):
    assert clean.is_heading(line) is expected


def test_normalise_typography():
    raw = "contra —y cito— «la brutal agresión»… “hola”"
    assert clean.normalise(raw) == 'contra -y cito- "la brutal agresión"... "hola"'


def test_headings_and_title_are_dropped():
    result = clean.clean_transcript((FIXTURES / "transcript_sample.txt").read_text())

    assert "Pregón de las fiestas" not in result.text
    assert "Una confesión" not in result.text
    assert "El nombre" not in result.text
    assert "Vecinas y vecinos de Matamala." in result.text
    assert "¡Viva Matamala!" in result.text


def test_sentence_punctuation_is_preserved():
    result = clean.clean_transcript("Hola mundo.\n\n¿Qué tal?\n")
    assert result.text == "Hola mundo. ¿Qué tal?"


def test_whitespace_collapses_and_lines_join():
    result = clean.clean_transcript("uno   dos\n\n\ntres\n")
    assert result.text == "uno dos tres"


def test_positions_point_back_at_the_original_file():
    raw = "Titulo sin punto\n\nHola mundo.\n\nI. Seccion\n\nAdios amigo.\n"
    result = clean.clean_transcript(raw)

    assert result.text == "Hola mundo. Adios amigo."
    assert len(result.positions) == len(result.text.split())

    # "Hola" is on line 3 at column 1; "Adios" is on line 7 at column 1.
    assert result.positions[0] == clean.SourcePos(line=3, column=1)
    assert result.positions[1] == clean.SourcePos(line=3, column=6)
    assert result.positions[2] == clean.SourcePos(line=7, column=1)


def test_a_title_that_ends_in_punctuation_is_kept():
    result = clean.clean_transcript("Buenas noches.\n\nHola.\n")
    assert result.text == "Buenas noches. Hola."
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_clean.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'subtitler.clean'`

- [ ] **Step 4: Write the implementation**

`src/subtitler/clean.py`:

```python
"""Turning an authored transcript into the words that were actually spoken.

The written document contains scaffolding the speaker never said aloud: a
title and Roman-numeral section headings. Feeding those to a forced aligner
would make it hunt for audio that does not exist and drag the surrounding
timings out of place.

Every surviving word keeps a pointer back to its line and column in the
original file, so the drift report can tell the user where to look in the
document they actually wrote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HEADING_RE = re.compile(r"^\s*[IVXLCDM]+\.\s+\S")

TRANSLATIONS = {
    "«": '"', "»": '"', "“": '"', "”": '"',
    "‘": "'", "’": "'",
    "—": "-", "–": "-",
}

TITLE_TERMINATORS = (".", "!", "?", ":")


@dataclass(frozen=True)
class SourcePos:
    line: int
    column: int


@dataclass(frozen=True)
class CleanResult:
    text: str
    positions: list[SourcePos]


def is_heading(line: str) -> bool:
    return bool(HEADING_RE.match(line))


def normalise(text: str) -> str:
    for source, target in TRANSLATIONS.items():
        text = text.replace(source, target)
    return text.replace("…", "...")


def clean_transcript(raw: str) -> CleanResult:
    words: list[str] = []
    positions: list[SourcePos] = []
    seen_first_content_line = False

    for line_number, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue

        if is_heading(line):
            continue

        if not seen_first_content_line:
            seen_first_content_line = True
            # Rule 2: a leading line without terminal punctuation is the title.
            if not stripped.endswith(TITLE_TERMINATORS):
                continue

        for match in re.finditer(r"\S+", line):
            words.append(normalise(match.group()))
            positions.append(SourcePos(line=line_number, column=match.start() + 1))

    return CleanResult(text=" ".join(words), positions=positions)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_clean.py -v`
Expected: PASS, 13 tests

- [ ] **Step 6: Verify against the real transcript**

Save the full pregón transcript to `~/githubs/subtitler/transcript.txt` (it is input data, not source — it is covered by no gitignore rule, so decide deliberately whether to commit it; committing it is fine and makes the reference run reproducible). Then:

```bash
uv run python -c "
from pathlib import Path
from subtitler.clean import clean_transcript
r = clean_transcript(Path('transcript.txt').read_text())
print('words:', len(r.positions))
print('head:', r.text[:120])
assert 'Cierre' not in r.text, 'a heading survived cleaning'
assert 'Pregón de las fiestas' not in r.text, 'the title survived cleaning'
print('OK')
"
```

Expected: roughly 2,000 words, and both assertions pass.

- [ ] **Step 7: Commit**

```bash
git add src/subtitler/clean.py tests/test_clean.py tests/fixtures/transcript_sample.txt
git commit -m "feat: transcript cleaning with source position mapping"
```

---

### Task 5: Shared models and cue grouping

**Files:**
- Create: `src/subtitler/models.py`, `src/subtitler/group.py`
- Test: `tests/test_group.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `models.Word` — frozen dataclass: `text: str`, `start: float`, `end: float`, `score: float = 1.0`
  - `models.Cue` — frozen dataclass: `words: tuple[Word, ...]`, with properties `start`, `end`, `text`
  - `group.ends_sentence(text: str) -> bool`
  - `group.group_words(words: list[Word], *, max_words: int = 3, pause_break: float = 0.35) -> list[Cue]`

**Grouping rules** (implement exactly these):

1. Accumulate words into the current cue; flush when it reaches `max_words`.
2. Flush after any word whose text ends a sentence (`.`, `!`, `?`, `:`, `;`), ignoring trailing quotes and brackets.
3. Flush when the silence before the next word exceeds `pause_break` seconds.
4. Rebalance afterwards: when a one-word cue follows a full cue, and the boundary between them is neither a sentence end nor a pause, move the previous cue's last word forward. This turns a stranded `3 + 1` into `2 + 2`.

- [ ] **Step 1: Write the failing test**

`tests/test_group.py`:

```python
import pytest

from subtitler.models import Cue, Word
from subtitler import group


def w(text, start, end):
    return Word(text=text, start=start, end=end, score=1.0)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Matamala.", True),
        ("¡Viva!", True),
        ("¿Qué?", True),
        ('dijo:', True),
        ('"cierto."', True),
        ("Matamala", False),
        ("libre,", False),
    ],
)
def test_ends_sentence(text, expected):
    assert group.ends_sentence(text) is expected


def test_cue_exposes_span_and_text():
    cue = Cue(words=(w("Vecinas", 1.0, 1.4), w("y", 1.4, 1.5)))
    assert cue.start == 1.0
    assert cue.end == 1.5
    assert cue.text == "Vecinas y"


def test_splits_at_max_words():
    words = [w(f"p{i}", i * 0.4, i * 0.4 + 0.3) for i in range(6)]
    cues = group.group_words(words, max_words=3, pause_break=10.0)
    assert [len(c.words) for c in cues] == [3, 3]


def test_breaks_at_sentence_end():
    words = [w("Hola.", 0.0, 0.4), w("Adios", 0.4, 0.8), w("amigo", 0.8, 1.2)]
    cues = group.group_words(words, max_words=3, pause_break=10.0)
    assert [c.text for c in cues] == ["Hola.", "Adios amigo"]


def test_breaks_on_long_pause():
    words = [w("uno", 0.0, 0.4), w("dos", 3.0, 3.4), w("tres", 3.4, 3.8)]
    cues = group.group_words(words, max_words=3, pause_break=0.35)
    assert [c.text for c in cues] == ["uno", "dos tres"]


def test_rebalances_a_stranded_single_word():
    words = [w(f"p{i}", i * 0.3, i * 0.3 + 0.25) for i in range(4)]
    cues = group.group_words(words, max_words=3, pause_break=10.0)
    assert [len(c.words) for c in cues] == [2, 2]


def test_does_not_rebalance_across_a_sentence_end():
    words = [w("uno", 0.0, 0.3), w("dos", 0.3, 0.6), w("tres.", 0.6, 0.9), w("cuatro", 0.9, 1.2)]
    cues = group.group_words(words, max_words=3, pause_break=10.0)
    assert [c.text for c in cues] == ["uno dos tres.", "cuatro"]


def test_does_not_rebalance_across_a_pause():
    words = [w("uno", 0.0, 0.3), w("dos", 0.3, 0.6), w("tres", 0.6, 0.9), w("cuatro", 5.0, 5.3)]
    cues = group.group_words(words, max_words=3, pause_break=0.35)
    assert [c.text for c in cues] == ["uno dos tres", "cuatro"]


def test_empty_input_gives_no_cues():
    assert group.group_words([], max_words=3, pause_break=0.35) == []


def test_every_word_survives_grouping():
    words = [w(f"p{i}", i * 0.3, i * 0.3 + 0.25) for i in range(17)]
    cues = group.group_words(words, max_words=3, pause_break=0.35)
    assert [x.text for c in cues for x in c.words] == [x.text for x in words]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_group.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'subtitler.models'`

- [ ] **Step 3: Write the implementation**

`src/subtitler/models.py`:

```python
"""Dataclasses that cross stage boundaries.

Alignment, grouping, drift auditing and ASS generation all talk about words
and cues. Defining them once here keeps those stages speaking the same
vocabulary, and keeps `cues.json` a stable, renderer-agnostic contract.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float
    score: float = 1.0

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class Cue:
    words: tuple[Word, ...]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)
```

`src/subtitler/group.py`:

```python
"""Packing timed words into on-screen cues.

Cues are short by design — a few words at a time, in the karaoke style. The
interesting decisions are where *not* to break: mid-sentence breaks that
strand a single word read badly, so a trailing single word is pulled back
into balance when nothing meaningful separates it from its neighbours.
"""

from __future__ import annotations

from .models import Cue, Word

SENTENCE_ENDINGS = ".!?:;"
TRAILING_PUNCTUATION = '"\')]}»”’'


def ends_sentence(text: str) -> bool:
    stripped = text.rstrip(TRAILING_PUNCTUATION)
    return bool(stripped) and stripped[-1] in SENTENCE_ENDINGS


def group_words(
    words: list[Word],
    *,
    max_words: int = 3,
    pause_break: float = 0.35,
) -> list[Cue]:
    cues: list[Cue] = []
    current: list[Word] = []

    for index, word in enumerate(words):
        current.append(word)

        at_capacity = len(current) >= max_words
        at_sentence_end = ends_sentence(word.text)
        before_pause = (
            index + 1 < len(words)
            and words[index + 1].start - word.end > pause_break
        )

        if at_capacity or at_sentence_end or before_pause:
            cues.append(Cue(words=tuple(current)))
            current = []

    if current:
        cues.append(Cue(words=tuple(current)))

    return _rebalance(cues, max_words=max_words, pause_break=pause_break)


def _rebalance(cues: list[Cue], *, max_words: int, pause_break: float) -> list[Cue]:
    """Pull a stranded single word back into balance with the cue before it."""
    result = list(cues)

    for index in range(1, len(result)):
        previous, current = result[index - 1], result[index]

        if len(current.words) != 1 or len(previous.words) < max_words:
            continue

        boundary = previous.words[-1]
        if ends_sentence(boundary.text):
            continue
        if current.words[0].start - boundary.end > pause_break:
            continue

        result[index - 1] = Cue(words=previous.words[:-1])
        result[index] = Cue(words=(boundary,) + current.words)

    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_group.py -v`
Expected: PASS, 17 tests (7 parametrised + 10)

- [ ] **Step 5: Commit**

```bash
git add src/subtitler/models.py src/subtitler/group.py tests/test_group.py
git commit -m "feat: shared word/cue models and cue grouping"
```

---

### Task 6: Style configuration and font asset

**Files:**
- Create: `style.toml`, `src/subtitler/style.py`, `assets/fonts/Montserrat-ExtraBold.ttf`
- Test: `tests/test_style.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `style.Style` — frozen dataclass, fields listed below
  - `style.ass_colour(hex_colour: str) -> str` — `"#RRGGBB"` → `"&H00BBGGRR"`
  - `style.load(path: Path) -> Style`
  - `style.DEFAULT_STYLE_PATH: Path`

**Font note:** Google Fonts ships Montserrat only as a variable font. libass
cannot be relied on to select a non-default weight from it, so the build step
instantiates a **static** ExtraBold with `fonttools`. This was verified on
2026-08-24: the instantiated face reports family `Montserrat ExtraBold`,
subfamily `Regular`, so ASS names it as `Montserrat ExtraBold` with `Bold=0`.

- [ ] **Step 1: Build the font asset**

```bash
mkdir -p assets/fonts
curl -sL -o /tmp/Montserrat-var.ttf \
  "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf"
uv run --with fonttools python - <<'PY'
from fontTools import ttLib
from fontTools.varLib import instancer

font = ttLib.TTFont("/tmp/Montserrat-var.ttf")
static = instancer.instantiateVariableFont(font, {"wght": 800}, updateFontNames=True)
static.save("assets/fonts/Montserrat-ExtraBold.ttf")

check = ttLib.TTFont("assets/fonts/Montserrat-ExtraBold.ttf")
names = {n.nameID: str(n) for n in check["name"].names if n.platformID == 3}
assert names[1] == "Montserrat ExtraBold", names[1]
print("built", names[1], "/", names[2])
PY
```

Expected output: `built Montserrat ExtraBold / Regular`

Montserrat is SIL Open Font License, so vendoring it is fine. Record that in
`assets/fonts/LICENSE.md` with a link to
`https://github.com/google/fonts/blob/main/ofl/montserrat/OFL.txt`.

- [ ] **Step 2: Write `style.toml`**

```toml
# All look-and-feel lives here. No visual constant belongs in Python.

[font]
# Family name as libass will see it. The vendored file is a static
# instance of the Montserrat variable font at weight 800.
family = "Montserrat ExtraBold"
file = "assets/fonts/Montserrat-ExtraBold.ttf"
size = 96

[colour]
fill = "#FFFFFF"       # words in the cue that are not currently spoken
highlight = "#FFD400"  # the word being spoken right now
outline = "#000000"

[layout]
position = 0.72        # centre of the text line, as a fraction of frame height
outline_width = 6.0
shadow_depth = 3.0
all_caps = true

[animation]
pop_scale = 1.08       # peak scale of the pop-in overshoot
pop_ms = 140           # total duration of the pop-in

[cues]
max_words = 3
pause_break = 0.35     # silence longer than this forces a cue break, seconds
```

- [ ] **Step 3: Write the failing test**

`tests/test_style.py`:

```python
import pytest

from subtitler import style


@pytest.mark.parametrize(
    "hex_colour,expected",
    [
        ("#FFFFFF", "&H00FFFFFF"),
        ("#000000", "&H00000000"),
        ("#FFD400", "&H0000D4FF"),  # R=FF G=D4 B=00 -> BGR order
        ("#112233", "&H00332211"),
        ("FFD400", "&H0000D4FF"),   # leading hash optional
    ],
)
def test_ass_colour_converts_rgb_to_bgr(hex_colour, expected):
    assert style.ass_colour(hex_colour) == expected


def test_ass_colour_rejects_bad_input():
    with pytest.raises(ValueError):
        style.ass_colour("#12345")


def test_load_reads_every_section(tmp_path):
    path = tmp_path / "style.toml"
    path.write_text(
        """
[font]
family = "Montserrat ExtraBold"
file = "assets/fonts/Montserrat-ExtraBold.ttf"
size = 96

[colour]
fill = "#FFFFFF"
highlight = "#FFD400"
outline = "#000000"

[layout]
position = 0.72
outline_width = 6.0
shadow_depth = 3.0
all_caps = true

[animation]
pop_scale = 1.08
pop_ms = 140

[cues]
max_words = 3
pause_break = 0.35
"""
    )

    loaded = style.load(path)

    assert loaded.font_family == "Montserrat ExtraBold"
    assert loaded.font_size == 96
    assert loaded.fill == "#FFFFFF"
    assert loaded.highlight == "#FFD400"
    assert loaded.position == 0.72
    assert loaded.all_caps is True
    assert loaded.pop_scale == 1.08
    assert loaded.pop_ms == 140
    assert loaded.max_words == 3
    assert loaded.pause_break == 0.35


def test_font_path_resolves_relative_to_the_style_file(tmp_path):
    (tmp_path / "assets" / "fonts").mkdir(parents=True)
    font = tmp_path / "assets" / "fonts" / "Montserrat-ExtraBold.ttf"
    font.write_bytes(b"")
    path = tmp_path / "style.toml"
    path.write_text(
        """
[font]
family = "X"
file = "assets/fonts/Montserrat-ExtraBold.ttf"
size = 10
[colour]
fill = "#FFFFFF"
highlight = "#FFD400"
outline = "#000000"
[layout]
position = 0.5
outline_width = 1.0
shadow_depth = 1.0
all_caps = false
[animation]
pop_scale = 1.0
pop_ms = 0
[cues]
max_words = 2
pause_break = 0.5
"""
    )

    loaded = style.load(path)
    assert loaded.font_path == font


def test_repo_style_file_loads():
    loaded = style.load(style.DEFAULT_STYLE_PATH)
    assert loaded.font_path.exists(), "the vendored font asset is missing"
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_style.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'subtitler.style'`

- [ ] **Step 5: Write the implementation**

`src/subtitler/style.py`:

```python
"""Look-and-feel configuration.

Everything visual is here or in `style.toml`, so iterating on the look never
means editing Python. ASS stores colours as `&HAABBGGRR` — alpha first, then
blue, green, red — which is the reverse byte order of the `#RRGGBB` a person
would write, so the conversion is explicit and tested.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_STYLE_PATH = Path(__file__).resolve().parents[2] / "style.toml"

HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


@dataclass(frozen=True)
class Style:
    font_family: str
    font_path: Path
    font_size: int
    fill: str
    highlight: str
    outline: str
    position: float
    outline_width: float
    shadow_depth: float
    all_caps: bool
    pop_scale: float
    pop_ms: int
    max_words: int
    pause_break: float


def ass_colour(hex_colour: str) -> str:
    """Convert `#RRGGBB` to the ASS `&HAABBGGRR` form, fully opaque."""
    match = HEX_RE.match(hex_colour.strip())
    if not match:
        raise ValueError(f"expected a #RRGGBB colour, got {hex_colour!r}")
    red, green, blue = (match.group(1)[i : i + 2] for i in (0, 2, 4))
    return f"&H00{blue}{green}{red}".upper()


def load(path: Path = DEFAULT_STYLE_PATH) -> Style:
    data = tomllib.loads(Path(path).read_text())
    font_file = Path(data["font"]["file"])
    if not font_file.is_absolute():
        font_file = Path(path).resolve().parent / font_file

    return Style(
        font_family=data["font"]["family"],
        font_path=font_file,
        font_size=int(data["font"]["size"]),
        fill=data["colour"]["fill"],
        highlight=data["colour"]["highlight"],
        outline=data["colour"]["outline"],
        position=float(data["layout"]["position"]),
        outline_width=float(data["layout"]["outline_width"]),
        shadow_depth=float(data["layout"]["shadow_depth"]),
        all_caps=bool(data["layout"]["all_caps"]),
        pop_scale=float(data["animation"]["pop_scale"]),
        pop_ms=int(data["animation"]["pop_ms"]),
        max_words=int(data["cues"]["max_words"]),
        pause_break=float(data["cues"]["pause_break"]),
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_style.py -v`
Expected: PASS, 8 tests

- [ ] **Step 7: Commit**

```bash
git add style.toml src/subtitler/style.py tests/test_style.py assets/fonts/
git commit -m "feat: style configuration and vendored Montserrat ExtraBold"
```

---

### Task 7: Text measurement and ASS generation

**Files:**
- Create: `src/subtitler/measure.py`, `src/subtitler/ass.py`
- Test: `tests/test_ass.py`

**Interfaces:**
- Consumes: `models.Cue`, `models.Word`, `style.Style`, `style.ass_colour`
- Produces:
  - `measure.text_measurer(font_path: Path, font_size: int) -> Callable[[str], float]`
  - `ass.ass_time(seconds: float) -> str` — `"H:MM:SS.cc"`
  - `ass.escape(text: str) -> str`
  - `ass.build_ass(cues, style, width, height, measure) -> str`

**Design note:** `ass.py` never imports Pillow. It takes a `measure` callable,
so its layout maths is unit-testable with a stub that returns a fixed width
per character. That keeps the module pure and the tests fast and deterministic.

**Event model:** each word produces up to three non-overlapping events —
before it is spoken (fill colour), while it is spoken (highlight colour), and
after (fill colour). Non-overlapping phases avoid libass compositing two
copies of the same glyph. The pop-in transform is applied only to events that
begin at the cue's start, so the cue pops in as a unit and the highlight then
sweeps across it.

- [ ] **Step 1: Write the failing test**

`tests/test_ass.py`:

```python
import dataclasses
from pathlib import Path

import pytest

from subtitler import ass
from subtitler.models import Cue, Word
from subtitler.style import Style


def stub_measure(text: str) -> float:
    """Every glyph is 50 px wide, so expected positions are easy to compute."""
    return 50.0 * len(text)


@pytest.fixture
def sty():
    return Style(
        font_family="Montserrat ExtraBold",
        font_path=Path("assets/fonts/Montserrat-ExtraBold.ttf"),
        font_size=96,
        fill="#FFFFFF",
        highlight="#FFD400",
        outline="#000000",
        position=0.72,
        outline_width=6.0,
        shadow_depth=3.0,
        all_caps=True,
        pop_scale=1.08,
        pop_ms=140,
        max_words=3,
        pause_break=0.35,
    )


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0.0, "0:00:00.00"),
        (1.5, "0:00:01.50"),
        (61.234, "0:01:01.23"),
        (3661.0, "1:01:01.00"),
    ],
)
def test_ass_time(seconds, expected):
    assert ass.ass_time(seconds) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("hola", "hola"),
        ("a{b}c", r"a\{b\}c"),
        ("back\\slash", r"back\\slash"),
    ],
)
def test_escape(raw, expected):
    assert ass.escape(raw) == expected


def test_header_uses_display_resolution(sty):
    doc = ass.build_ass([], sty, 1080, 1920, stub_measure)
    assert "PlayResX: 1080" in doc
    assert "PlayResY: 1920" in doc


def test_style_line_names_the_family_without_bold_flag(sty):
    doc = ass.build_ass([], sty, 1080, 1920, stub_measure)
    style_line = next(l for l in doc.splitlines() if l.startswith("Style:"))
    fields = style_line.removeprefix("Style: ").split(",")
    assert fields[1] == "Montserrat ExtraBold"
    assert fields[2] == "96"
    assert fields[7] == "0", "Bold must be 0; the vendored face is already ExtraBold"


def test_words_are_centred_as_a_group(sty):
    cue = Cue(words=(Word("AB", 0.0, 1.0), Word("CD", 1.0, 2.0)))
    doc = ass.build_ass([cue], sty, 1000, 2000, stub_measure)

    # "AB" and "CD" are 100 px each; one space is 50 px. Total 250 px.
    # Left edge = (1000 - 250) / 2 = 375. Centres at 425 and 575.
    assert r"\pos(425,1440)" in doc
    assert r"\pos(575,1440)" in doc


def test_each_word_gets_fill_then_highlight_then_fill(sty):
    cue = Cue(words=(Word("AB", 0.0, 1.0), Word("CD", 1.0, 2.0)))
    doc = ass.build_ass([cue], sty, 1000, 2000, stub_measure)
    events = [l for l in doc.splitlines() if l.startswith("Dialogue:")]

    # AB: highlighted 0-1, fill 1-2. CD: fill 0-1, highlighted 1-2.
    assert len(events) == 4
    assert sum("&H0000D4FF" in e for e in events) == 2, "one highlight phase per word"


def test_pop_transform_only_on_events_starting_with_the_cue(sty):
    cue = Cue(words=(Word("AB", 0.0, 1.0), Word("CD", 1.0, 2.0)))
    doc = ass.build_ass([cue], sty, 1000, 2000, stub_measure)
    events = [l for l in doc.splitlines() if l.startswith("Dialogue:")]

    starting_at_cue_start = [e for e in events if ",0:00:00.00," in e]
    later = [e for e in events if ",0:00:00.00," not in e]
    assert all(r"\t(" in e for e in starting_at_cue_start)
    assert all(r"\t(" not in e for e in later)


def test_all_caps_is_applied(sty):
    cue = Cue(words=(Word("hola", 0.0, 1.0),))
    doc = ass.build_ass([cue], sty, 1000, 2000, stub_measure)
    assert "HOLA" in doc
    assert "hola" not in doc.split("[Events]")[1]


def test_all_caps_can_be_disabled(sty):
    lower = ass.build_ass(
        [Cue(words=(Word("hola", 0.0, 1.0),))],
        dataclasses.replace(sty, all_caps=False),
        1000, 2000, stub_measure,
    )
    assert "hola" in lower.split("[Events]")[1]


def test_events_are_ordered_by_start_time(sty):
    cues = [
        Cue(words=(Word("A", 5.0, 6.0),)),
        Cue(words=(Word("B", 0.0, 1.0),)),
    ]
    doc = ass.build_ass(cues, sty, 1000, 2000, stub_measure)
    events = [l for l in doc.splitlines() if l.startswith("Dialogue:")]
    starts = [e.split(",")[1] for e in events]
    assert starts == sorted(starts)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ass.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'subtitler.ass'`

- [ ] **Step 3: Write the implementation**

`src/subtitler/measure.py`:

```python
"""Measuring rendered text width.

ASS positions each word individually, so the generator needs to know how wide
each word will be in order to centre a cue as a group. Pillow measures with
the same font file libass will use, which is close enough for layout.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PIL import ImageFont


def text_measurer(font_path: Path, font_size: int) -> Callable[[str], float]:
    font = ImageFont.truetype(str(font_path), font_size)

    def measure(text: str) -> float:
        return float(font.getlength(text))

    return measure
```

`src/subtitler/ass.py`:

```python
"""Generating the ASS subtitle document.

Each word is positioned individually with `\\pos` so the highlight can change
colour on one word without relayouting the rest of the cue. That means this
module has to do its own centring, which is why it needs a text measurer.

The measurer is injected rather than imported so this module stays pure and
its layout maths can be tested with a predictable stub.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .models import Cue
from .style import Style, ass_colour

HEADER_TEMPLATE = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: K,{family},{size},{fill},{fill},{outline},&H00000000,0,0,0,0,100,100,0,0,1,{outline_width},{shadow},5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""


def ass_time(seconds: float) -> str:
    """Format seconds as the ASS `H:MM:SS.cc` timecode."""
    centiseconds = int(round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, cents = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cents:02d}"


def escape(text: str) -> str:
    """Escape the characters ASS treats as override-block syntax."""
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _pop_tags(style: Style) -> str:
    """Scale overshoot applied when a cue first appears."""
    if style.pop_ms <= 0 or style.pop_scale == 1.0:
        return ""
    start_scale = int(round(100 / style.pop_scale))
    peak_scale = int(round(100 * style.pop_scale))
    half = style.pop_ms // 2
    return (
        f"\\fscx{start_scale}\\fscy{start_scale}"
        f"\\t(0,{half},\\fscx{peak_scale}\\fscy{peak_scale})"
        f"\\t({half},{style.pop_ms},\\fscx100\\fscy100)"
    )


def _layout(cue: Cue, style: Style, width: int, measure) -> list[float]:
    """Horizontal centre for each word, centring the cue as a group."""
    texts = [_render_text(word.text, style) for word in cue.words]
    widths = [measure(text) for text in texts]
    space = measure(" ")

    total = sum(widths) + space * (len(widths) - 1)
    cursor = (width - total) / 2

    centres = []
    for word_width in widths:
        centres.append(cursor + word_width / 2)
        cursor += word_width + space
    return centres


def _render_text(text: str, style: Style) -> str:
    return text.upper() if style.all_caps else text


def build_ass(
    cues: Sequence[Cue],
    style: Style,
    width: int,
    height: int,
    measure: Callable[[str], float],
) -> str:
    header = HEADER_TEMPLATE.format(
        width=width,
        height=height,
        family=style.font_family,
        size=style.font_size,
        fill=ass_colour(style.fill),
        outline=ass_colour(style.outline),
        outline_width=_number(style.outline_width),
        shadow=_number(style.shadow_depth),
    )

    fill = ass_colour(style.fill)
    highlight = ass_colour(style.highlight)
    y = int(round(height * style.position))
    pop = _pop_tags(style)

    events: list[tuple[float, str]] = []

    for cue in sorted(cues, key=lambda c: c.start):
        centres = _layout(cue, style, width, measure)

        for word, centre_x in zip(cue.words, centres):
            text = escape(_render_text(word.text, style))
            x = int(round(centre_x))

            phases = [
                (cue.start, word.start, fill),
                (word.start, word.end, highlight),
                (word.end, cue.end, fill),
            ]

            for start, end, colour in phases:
                if end - start <= 0.001:
                    continue
                tags = f"\\an5\\pos({x},{y})\\c{colour}&"
                if abs(start - cue.start) < 0.001:
                    tags += pop
                events.append((
                    start,
                    f"Dialogue: 0,{ass_time(start)},{ass_time(end)},K,,0,0,0,,{{{tags}}}{text}",
                ))

    ordered = [line for _, line in sorted(events, key=lambda item: item[0])]
    return "\n".join([header, *ordered]) + "\n"


def _number(value: float) -> str:
    return f"{value:g}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ass.py -v`
Expected: PASS, 16 tests

- [ ] **Step 5: Commit**

```bash
git add src/subtitler/measure.py src/subtitler/ass.py tests/test_ass.py
git commit -m "feat: ASS generation with per-word layout and pop-in animation"
```

---

### Task 8: Forced alignment

**Files:**
- Create: `src/subtitler/align.py`
- Test: `tests/test_align.py`

**Interfaces:**
- Consumes: `models.Word`
- Produces:
  - `align.align(audio: Path, script: str, *, model_name: str = "large-v3", device: str = "cuda", language: str = "es") -> list[Word]`
  - `align.save_words(words: list[Word], path: Path) -> None`
  - `align.load_words(path: Path) -> list[Word]`

**Why forced alignment and not transcription:** the transcript is
authoritative. `stable-ts`'s `align()` takes the known text and finds where
each word falls in the audio, rather than guessing at the words. Where the
speaker departed from the script it will still place something — that is what
Task 9's drift report exists to surface.

- [ ] **Step 1: Write the failing test**

`tests/test_align.py`:

```python
import json
from pathlib import Path

import pytest

from subtitler import align
from subtitler.models import Word

FIXTURES = Path(__file__).parent / "fixtures"


def test_words_round_trip_through_json(tmp_path):
    words = [
        Word(text="Vecinas", start=0.0, end=0.52, score=0.98),
        Word(text="y", start=0.52, end=0.61, score=0.71),
    ]
    path = tmp_path / "words.json"

    align.save_words(words, path)
    loaded = align.load_words(path)

    assert loaded == words


def test_saved_json_is_a_readable_list_of_objects(tmp_path):
    path = tmp_path / "words.json"
    align.save_words([Word("hola", 1.0, 1.5, 0.9)], path)

    payload = json.loads(path.read_text())
    assert payload == [{"text": "hola", "start": 1.0, "end": 1.5, "score": 0.9}]


def test_result_conversion_drops_blank_words_and_strips_spacing():
    class FakeWord:
        def __init__(self, word, start, end, probability):
            self.word = word
            self.start = start
            self.end = end
            self.probability = probability

    fake = [
        FakeWord(" Vecinas", 0.0, 0.5, 0.9),
        FakeWord("  ", 0.5, 0.6, 0.1),
        FakeWord(" y", 0.6, 0.7, 0.8),
    ]

    words = align.words_from_result(fake)

    assert [w.text for w in words] == ["Vecinas", "y"]
    assert words[0].score == 0.9


def test_missing_probability_defaults_to_one():
    class Bare:
        word = "hola"
        start = 0.0
        end = 1.0

    words = align.words_from_result([Bare()])
    assert words[0].score == 1.0


@pytest.mark.gpu
def test_alignment_on_the_real_clip(tmp_path):
    """Aligns a known 15 s excerpt. Downloads a model on first run."""
    from subtitler import extract

    audio = tmp_path / "audio.wav"
    extract.extract_audio(FIXTURES / "clip.mov", audio)
    script = (FIXTURES / "clip_script.txt").read_text().strip()

    words = align.align(audio, script)

    assert len(words) > 10
    assert words[0].start >= 0.0
    assert all(w.end >= w.start for w in words)
    assert all(w.end <= 16.0 for w in words), "no word may fall outside the clip"
    # Timings must be monotonic, or grouping and rendering both break.
    assert all(b.start >= a.start for a, b in zip(words, words[1:]))
```

- [ ] **Step 2: Run the fast tests to verify they fail**

Run: `uv run pytest tests/test_align.py -v -m "not gpu"`
Expected: FAIL — `ModuleNotFoundError: No module named 'subtitler.align'`

- [ ] **Step 3: Write the implementation**

`src/subtitler/align.py`:

```python
"""Forced alignment of a known script against the audio.

This is alignment, not transcription. The words are given; the job is finding
when each one was said. Where the speaker improvised or skipped a line the
aligner will still place the scripted words somewhere — `drift.py` is what
turns that into something the user can see and act on.

`stable_whisper` is imported lazily so that importing this module, and
therefore running the fast unit tests, does not pull in torch.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import Word


def words_from_result(raw_words) -> list[Word]:
    """Convert stable-ts word timings into our Word model."""
    words = []
    for raw in raw_words:
        text = raw.word.strip()
        if not text:
            continue
        words.append(
            Word(
                text=text,
                start=float(raw.start),
                end=float(raw.end),
                score=float(getattr(raw, "probability", None) or 1.0),
            )
        )
    return words


def align(
    audio: Path,
    script: str,
    *,
    model_name: str = "large-v3",
    device: str = "cuda",
    language: str = "es",
) -> list[Word]:
    import stable_whisper

    model = stable_whisper.load_model(model_name, device=device)
    result = model.align(str(audio), script, language=language)
    return words_from_result(result.all_words())


def save_words(words: list[Word], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(word) for word in words], ensure_ascii=False, indent=1))


def load_words(path: Path) -> list[Word]:
    return [Word(**entry) for entry in json.loads(path.read_text())]
```

- [ ] **Step 4: Run the fast tests to verify they pass**

Run: `uv run pytest tests/test_align.py -v -m "not gpu"`
Expected: PASS, 4 tests

- [ ] **Step 5: Create the clip script fixture**

The GPU test needs the text spoken in `tests/fixtures/clip.mov`. Play the
15 seconds starting at 3:20 of the reference video and write down exactly what
is said, into `tests/fixtures/clip_script.txt`. Do not guess it from the
transcript — the point of this fixture is that it is known-correct.

- [ ] **Step 6: Run the GPU test**

Run: `uv run pytest tests/test_align.py -v -m gpu`
Expected: PASS. First run downloads the `large-v3` weights (~3 GB); later runs
use the cache. If CUDA is unavailable, rerun with `device="cpu"` to confirm
the code path works, then investigate the GPU separately.

- [ ] **Step 7: Commit**

```bash
git add src/subtitler/align.py tests/test_align.py tests/fixtures/clip_script.txt
git commit -m "feat: forced alignment via stable-ts with word-level timings"
```

---

### Task 9: Drift report

**Files:**
- Create: `src/subtitler/drift.py`
- Test: `tests/test_drift.py`

**Interfaces:**
- Consumes: `models.Word`, `clean.SourcePos`
- Produces:
  - `drift.Flag` — frozen dataclass: `kind: str`, `start: float`, `end: float`, `text: str`, `line: int`
  - `drift.find_drift(words, *, score_threshold=0.5, run_length=3, gap_threshold=2.0, duration_factor=3.0, positions=None) -> list[Flag]`
  - `drift.render_report(flags: list[Flag], total_words: int) -> str`

**The three signals** (a live speech departs from its script in three
detectable ways):

1. **`low-confidence`** — a run of `run_length` or more consecutive words each
   scoring below `score_threshold`. The aligner was guessing.
2. **`gap`** — silence longer than `gap_threshold` inside what the script says
   is continuous speech. Something was said that is not in the script, or a
   long pause was taken.
3. **`long-word`** — a word whose duration exceeds `duration_factor` times the
   median word duration. The classic signature of an aligner stretching one
   word across audio it could not match.

Drift is reported, never silently repaired.

- [ ] **Step 1: Write the failing test**

`tests/test_drift.py`:

```python
from subtitler import drift
from subtitler.clean import SourcePos
from subtitler.models import Word


def w(text, start, end, score=1.0):
    return Word(text=text, start=start, end=end, score=score)


def test_no_flags_on_clean_alignment():
    words = [w(f"p{i}", i * 0.4, i * 0.4 + 0.35) for i in range(20)]
    assert drift.find_drift(words) == []


def test_flags_a_run_of_low_confidence_words():
    words = [w(f"p{i}", i * 0.4, i * 0.4 + 0.35) for i in range(20)]
    words[5:9] = [w(f"p{i}", i * 0.4, i * 0.4 + 0.35, score=0.2) for i in range(5, 9)]

    flags = drift.find_drift(words, score_threshold=0.5, run_length=3)

    low = [f for f in flags if f.kind == "low-confidence"]
    assert len(low) == 1
    assert low[0].start == words[5].start
    assert low[0].end == words[8].end


def test_a_short_low_confidence_run_is_not_flagged():
    words = [w(f"p{i}", i * 0.4, i * 0.4 + 0.35) for i in range(20)]
    words[5] = w("p5", 2.0, 2.35, score=0.1)

    flags = drift.find_drift(words, score_threshold=0.5, run_length=3)
    assert [f for f in flags if f.kind == "low-confidence"] == []


def test_flags_a_long_silence():
    words = [w("uno", 0.0, 0.4), w("dos", 9.0, 9.4), w("tres", 9.4, 9.8)]

    flags = drift.find_drift(words, gap_threshold=2.0)

    gaps = [f for f in flags if f.kind == "gap"]
    assert len(gaps) == 1
    assert gaps[0].start == 0.4
    assert gaps[0].end == 9.0


def test_flags_an_implausibly_long_word():
    words = [w(f"p{i}", i * 0.4, i * 0.4 + 0.35) for i in range(20)]
    words[7] = w("estirada", 2.8, 7.0)

    flags = drift.find_drift(words, duration_factor=3.0, gap_threshold=100.0)

    long_words = [f for f in flags if f.kind == "long-word"]
    assert len(long_words) == 1
    assert long_words[0].text == "estirada"


def test_flags_carry_the_transcript_line_number():
    words = [w("uno", 0.0, 0.4), w("dos", 9.0, 9.4)]
    positions = [SourcePos(line=12, column=1), SourcePos(line=12, column=5)]

    flags = drift.find_drift(words, gap_threshold=2.0, positions=positions)

    assert flags[0].line == 12


def test_report_states_clean_when_there_are_no_flags():
    report = drift.render_report([], total_words=1500)
    assert "No drift detected" in report
    assert "1500" in report


def test_report_lists_each_flag_with_a_timecode():
    flags = [drift.Flag(kind="gap", start=65.0, end=70.0, text="uno dos", line=12)]
    report = drift.render_report(flags, total_words=1500)

    assert "1:05" in report
    assert "gap" in report
    assert "line 12" in report
    assert "uno dos" in report
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_drift.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'subtitler.drift'`

- [ ] **Step 3: Write the implementation**

`src/subtitler/drift.py`:

```python
"""Auditing where the audio and the script disagree.

The subtitles say what the transcript says. That is the user's choice, and
this module exists because it is sometimes the wrong one: a live speaker
improvises, repeats, and skips lines. Rather than silently repairing the
alignment, the pipeline reports the places where the scripted text and the
audio pulled apart, with timecodes to jump to and transcript line numbers to
edit.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from .models import Word


@dataclass(frozen=True)
class Flag:
    kind: str
    start: float
    end: float
    text: str
    line: int = 0


def _timecode(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


def _line_for(index: int, positions) -> int:
    if not positions or index >= len(positions):
        return 0
    return positions[index].line


def find_drift(
    words: list[Word],
    *,
    score_threshold: float = 0.5,
    run_length: int = 3,
    gap_threshold: float = 2.0,
    duration_factor: float = 3.0,
    positions=None,
) -> list[Flag]:
    flags: list[Flag] = []
    if not words:
        return flags

    # 1. Runs of low-confidence words.
    run_start = None
    for index, word in enumerate(words):
        if word.score < score_threshold:
            if run_start is None:
                run_start = index
            continue
        if run_start is not None and index - run_start >= run_length:
            flags.append(_run_flag(words, run_start, index - 1, positions))
        run_start = None
    if run_start is not None and len(words) - run_start >= run_length:
        flags.append(_run_flag(words, run_start, len(words) - 1, positions))

    # 2. Silences inside supposedly continuous speech.
    for index, (before, after) in enumerate(zip(words, words[1:])):
        if after.start - before.end > gap_threshold:
            flags.append(
                Flag(
                    kind="gap",
                    start=before.end,
                    end=after.start,
                    text=f"{before.text} | {after.text}",
                    line=_line_for(index, positions),
                )
            )

    # 3. Words stretched across audio the aligner could not match.
    median = statistics.median(word.duration for word in words)
    if median > 0:
        for index, word in enumerate(words):
            if word.duration > median * duration_factor:
                flags.append(
                    Flag(
                        kind="long-word",
                        start=word.start,
                        end=word.end,
                        text=word.text,
                        line=_line_for(index, positions),
                    )
                )

    return sorted(flags, key=lambda flag: flag.start)


def _run_flag(words: list[Word], first: int, last: int, positions) -> Flag:
    return Flag(
        kind="low-confidence",
        start=words[first].start,
        end=words[last].end,
        text=" ".join(word.text for word in words[first : last + 1]),
        line=_line_for(first, positions),
    )


def render_report(flags: list[Flag], total_words: int) -> str:
    lines = [
        "# Drift report",
        "",
        f"Aligned {total_words} words from the transcript.",
        "",
    ]

    if not flags:
        lines += [
            "No drift detected. The audio follows the transcript closely.",
            "",
        ]
        return "\n".join(lines)

    lines += [
        f"{len(flags)} span(s) where the audio and the transcript may disagree.",
        "Jump to each timecode in the video and check before rendering.",
        "",
        "| Time | Kind | Transcript | Text |",
        "| --- | --- | --- | --- |",
    ]

    for flag in flags:
        location = f"line {flag.line}" if flag.line else "-"
        text = flag.text.replace("|", "\\|")
        lines.append(
            f"| {_timecode(flag.start)}-{_timecode(flag.end)} | {flag.kind} | {location} | {text} |"
        )

    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_drift.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Add the integration test the spec calls for**

The unit tests above prove the detection logic. The spec also asks for a test
that the detector fires on *real* drift, not just synthetic timings. Append to
`tests/test_drift.py`:

```python
@pytest.mark.gpu
@pytest.mark.slow
def test_a_sentence_absent_from_the_audio_is_flagged(tmp_path):
    """Align a script containing a sentence the speaker never said."""
    from pathlib import Path

    from subtitler import align, extract

    fixtures = Path(__file__).parent / "fixtures"
    audio = tmp_path / "audio.wav"
    extract.extract_audio(fixtures / "clip.mov", audio)

    real = (fixtures / "clip_script.txt").read_text().strip()
    padded = real + " Esta frase no aparece en el audio de ninguna manera."

    words = align.align(audio, padded)
    flags = drift.find_drift(words)

    assert flags, "an interpolated sentence must produce at least one flag"
    # The invented sentence is at the end, so a flag must land in its span.
    assert any(f.start > words[len(real.split())].start - 1.0 for f in flags)
```

Add `import pytest` at the top of the file.

- [ ] **Step 6: Run the integration test**

Run: `uv run pytest tests/test_drift.py -v -m "gpu and slow"`
Expected: PASS. If it fails, the thresholds in `find_drift` are too lax for
real alignment scores — tune `score_threshold` and `duration_factor` against
the actual values in `words.json` rather than guessing, and record the tuned
values as the defaults.

- [ ] **Step 7: Commit**

```bash
git add src/subtitler/drift.py tests/test_drift.py
git commit -m "feat: drift report flagging low confidence, gaps and stretched words"
```

---

### Task 10: Render — filter chain and burn-in

**Files:**
- Create: `src/subtitler/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `binaries`, `probe.MediaInfo`
- Produces:
  - `render.escape_filter_path(path: Path) -> str`
  - `render.build_filter_chain(ass_path: Path, fontsdir: Path, *, tone_map: bool) -> str`
  - `render.build_command(video, ass, out, info, *, fontsdir, start=None, duration=None, encoder="h264_nvenc") -> list[str]`
  - `render.burn(video, ass, out, info, *, fontsdir, start=None, duration=None, encoder="h264_nvenc") -> Path`
  - `render.shift_cues(cues: list[Cue], start: float, end: float) -> list[Cue]`

**Filter order matters.** Tone-mapping must come before `subtitles`, so
subtitle colours are composited in SDR BT.709 and mean exactly what
`style.toml` says. Compositing sRGB text onto an HLG timeline and tone-mapping
afterwards would distort the styled colours.

Verified working on 2026-08-24 against the reference video:

```
zscale=t=linear:npl=100,tonemap=hable:desat=0,zscale=p=bt709:t=bt709:m=bt709:r=tv,format=yuv420p,subtitles=...
```

- [ ] **Step 1: Write the failing test**

`tests/test_render.py`:

```python
from pathlib import Path

import pytest

from subtitler import probe, render
from subtitler.models import Cue, Word

FIXTURES = Path(__file__).parent / "fixtures"


def hdr_info():
    return probe.MediaInfo(
        stored_width=1920, stored_height=1080, rotation=-90,
        display_width=1080, display_height=1920,
        fps=23.976, duration=691.135,
        color_transfer="arib-std-b67", color_primaries="bt2020", is_hdr=True,
    )


def sdr_info():
    return probe.MediaInfo(
        stored_width=1920, stored_height=1080, rotation=0,
        display_width=1920, display_height=1080,
        fps=30.0, duration=12.0,
        color_transfer="bt709", color_primaries="bt709", is_hdr=False,
    )


def test_filter_chain_tone_maps_before_subtitles():
    chain = render.build_filter_chain(Path("/w/subs.ass"), Path("/w/fonts"), tone_map=True)

    assert chain.index("tonemap") < chain.index("subtitles")
    assert "bt709" in chain
    assert "format=yuv420p" in chain


def test_filter_chain_without_tone_mapping_is_just_subtitles():
    chain = render.build_filter_chain(Path("/w/subs.ass"), Path("/w/fonts"), tone_map=False)

    assert "tonemap" not in chain
    assert chain.startswith("subtitles=")


def test_filter_path_escaping():
    assert render.escape_filter_path(Path("/a b/subs.ass")) == "/a b/subs.ass"
    assert render.escape_filter_path(Path("/a:b/subs.ass")) == r"/a\:b/subs.ass"


def test_command_never_transposes():
    command = render.build_command(
        Path("in.mov"), Path("subs.ass"), Path("out.mp4"), hdr_info(),
        fontsdir=Path("fonts"),
    )
    assert not any("transpose" in arg for arg in command)


def test_command_tone_maps_an_hdr_source():
    command = render.build_command(
        Path("in.mov"), Path("subs.ass"), Path("out.mp4"), hdr_info(),
        fontsdir=Path("fonts"),
    )
    assert any("tonemap" in arg for arg in command)


def test_command_skips_tone_mapping_for_an_sdr_source():
    command = render.build_command(
        Path("in.mov"), Path("subs.ass"), Path("out.mp4"), sdr_info(),
        fontsdir=Path("fonts"),
    )
    assert not any("tonemap" in arg for arg in command)


def test_sample_window_seeks_before_the_input():
    command = render.build_command(
        Path("in.mov"), Path("subs.ass"), Path("out.mp4"), hdr_info(),
        fontsdir=Path("fonts"), start=135.0, duration=10.0,
    )
    assert command.index("-ss") < command.index("-i")
    assert command[command.index("-ss") + 1] == "135.0"
    assert command[command.index("-t") + 1] == "10.0"


def test_shift_cues_rebases_to_the_window_and_drops_the_rest():
    cues = [
        Cue(words=(Word("a", 1.0, 2.0),)),
        Cue(words=(Word("b", 11.0, 12.0),)),
        Cue(words=(Word("c", 30.0, 31.0),)),
    ]

    shifted = render.shift_cues(cues, start=10.0, end=20.0)

    assert len(shifted) == 1
    assert shifted[0].text == "b"
    assert shifted[0].start == pytest.approx(1.0)
    assert shifted[0].end == pytest.approx(2.0)


@pytest.mark.slow
def test_burn_produces_an_upright_sdr_file(tmp_path):
    ass_path = tmp_path / "subs.ass"
    ass_path.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: K,DejaVu Sans,96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,6,3,5,0,0,0,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:03.00,K,,0,0,0,,{\\an5\\pos(540,1382)}HOLA\n"
    )

    info = probe.probe(FIXTURES / "clip.mov")
    out = tmp_path / "out.mp4"
    render.burn(
        FIXTURES / "clip.mov", ass_path, out, info,
        fontsdir=Path("assets/fonts"), start=0.0, duration=3.0,
        encoder="libx264",
    )

    assert out.exists()
    result = probe.probe(out)
    assert (result.display_width, result.display_height) == (1080, 1920)
    assert result.is_hdr is False
    assert result.color_primaries == "bt709"
    assert result.duration == pytest.approx(3.0, abs=0.3)
```

- [ ] **Step 2: Run the fast tests to verify they fail**

Run: `uv run pytest tests/test_render.py -v -m "not slow"`
Expected: FAIL — `ModuleNotFoundError: No module named 'subtitler.render'`

- [ ] **Step 3: Write the implementation**

`src/subtitler/render.py`:

```python
"""Burning the subtitles into the video.

Two things about the reference source shape this module.

Rotation: ffmpeg auto-rotates on decode, so the filter graph already sees an
upright 1080x1920 frame. Adding a transpose would rotate it a second time.
There is deliberately no transpose here, and a test asserts its absence.

Colour: the source is Dolby Vision over an HLG BT.2020 base layer. Burning in
subtitles means decoding, filtering and re-encoding, which destroys the Dolby
Vision layer no matter what — that is a property of the operation, not a
choice. The base layer is tone-mapped to SDR BT.709 so the output looks the
same on every player, and so subtitle colours composited in SDR mean what
style.toml says. Tone-mapping therefore runs *before* the subtitles filter.
"""

from __future__ import annotations

from pathlib import Path

from . import binaries
from .models import Cue, Word
from .probe import MediaInfo

TONE_MAP_FILTERS = (
    "zscale=t=linear:npl=100",
    "tonemap=hable:desat=0",
    "zscale=p=bt709:t=bt709:m=bt709:r=tv",
    "format=yuv420p",
)


def escape_filter_path(path: Path) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    return str(path).replace("\\", "\\\\").replace(":", "\\:")


def build_filter_chain(ass_path: Path, fontsdir: Path, *, tone_map: bool) -> str:
    subtitles = (
        f"subtitles={escape_filter_path(ass_path)}"
        f":fontsdir={escape_filter_path(fontsdir)}"
    )
    if not tone_map:
        return subtitles
    return ",".join([*TONE_MAP_FILTERS, subtitles])


def build_command(
    video: Path,
    ass: Path,
    out: Path,
    info: MediaInfo,
    *,
    fontsdir: Path,
    start: float | None = None,
    duration: float | None = None,
    encoder: str = "h264_nvenc",
) -> list[str]:
    command = [binaries.ffmpeg(), "-y", "-v", "error"]

    # Seek before -i so ffmpeg skips decoding everything up to the window.
    if start is not None:
        command += ["-ss", str(start)]
    if duration is not None:
        command += ["-t", str(duration)]

    command += ["-i", str(video)]
    command += ["-vf", build_filter_chain(ass, fontsdir, tone_map=info.is_hdr)]
    command += ["-c:v", encoder]

    if encoder.endswith("nvenc"):
        command += ["-preset", "p5", "-rc", "vbr", "-cq", "20", "-b:v", "0"]
    else:
        command += ["-preset", "medium", "-crf", "20"]

    command += ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(out)]
    return command


def burn(
    video: Path,
    ass: Path,
    out: Path,
    info: MediaInfo,
    *,
    fontsdir: Path,
    start: float | None = None,
    duration: float | None = None,
    encoder: str = "h264_nvenc",
) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    command = build_command(
        video, ass, out, info,
        fontsdir=fontsdir, start=start, duration=duration, encoder=encoder,
    )
    try:
        binaries.run(command)
    except binaries.BinaryError:
        if not encoder.endswith("nvenc"):
            raise
        # NVENC is unavailable or busy; fall back to software encoding.
        binaries.run(build_command(
            video, ass, out, info,
            fontsdir=fontsdir, start=start, duration=duration, encoder="libx264",
        ))
    return out


def shift_cues(cues: list[Cue], start: float, end: float) -> list[Cue]:
    """Keep cues overlapping [start, end) and rebase their times to the window.

    The sample render seeks into the video, so subtitle times have to be
    rebased to the clip or every cue would appear at the wrong moment.
    """
    shifted = []
    for cue in cues:
        if cue.end <= start or cue.start >= end:
            continue
        shifted.append(
            Cue(words=tuple(
                Word(
                    text=word.text,
                    start=word.start - start,
                    end=word.end - start,
                    score=word.score,
                )
                for word in cue.words
            ))
        )
    return shifted
```

- [ ] **Step 4: Run the fast tests to verify they pass**

Run: `uv run pytest tests/test_render.py -v -m "not slow"`
Expected: PASS, 8 tests

- [ ] **Step 5: Run the slow test to verify the real burn works**

Run: `uv run pytest tests/test_render.py -v -m slow`
Expected: PASS. This is the test that proves rotation is neither missed nor
double-applied (output is 1080x1920) and that tone-mapping landed (output is
BT.709, not BT.2020).

- [ ] **Step 6: Commit**

```bash
git add src/subtitler/render.py tests/test_render.py
git commit -m "feat: tone-mapped subtitle burn-in with sample windowing"
```

---

### Task 11: CLI, caching and end-to-end test

**Files:**
- Create: `src/subtitler/cli.py`, `README.md`
- Test: `tests/test_cli.py`, `tests/test_e2e.py`

**Interfaces:**
- Consumes: every module above
- Produces:
  - `cli.main(argv: list[str] | None = None) -> int`
  - `cli.pipeline(video, transcript, style_path, *, force=False) -> PipelineResult` — runs stages 1-4 with caching, returns `PipelineResult(info, cues, words, workdir)`
  - `cli.first_dense_span(words, *, window=10.0) -> float` — start time of the densest `window` seconds of speech

**Caching:** each stage checks `workdir.is_fresh(artifact, *sources)` and skips
if the artifact is newer than its inputs. This is what makes the sample loop
fast: alignment runs once per video, and every later `sample` invocation
re-renders only the window.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:

```python
import pytest

from subtitler import cli
from subtitler.models import Word


def w(text, start, end):
    return Word(text=text, start=start, end=end, score=1.0)


def test_first_dense_span_prefers_the_busiest_window():
    # Sparse speech at the start, dense speech from 60 s.
    sparse = [w(f"s{i}", i * 5.0, i * 5.0 + 0.3) for i in range(10)]
    dense = [w(f"d{i}", 60.0 + i * 0.3, 60.0 + i * 0.3 + 0.25) for i in range(40)]

    assert cli.first_dense_span(sparse + dense, window=10.0) == pytest.approx(60.0, abs=1.0)


def test_first_dense_span_of_empty_input_is_zero():
    assert cli.first_dense_span([], window=10.0) == 0.0


def test_parses_sample_timecode():
    assert cli.parse_timecode("2:15") == 135.0
    assert cli.parse_timecode("0:05") == 5.0
    assert cli.parse_timecode("90") == 90.0
    assert cli.parse_timecode("1:02:03") == 3723.0


def test_rejects_a_bad_timecode():
    with pytest.raises(ValueError):
        cli.parse_timecode("banana")


def test_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0
    assert "sample" in capsys.readouterr().out
```

`tests/test_e2e.py`:

```python
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.gpu
@pytest.mark.slow
def test_sample_render_end_to_end(tmp_path):
    """The whole pipeline on a 15 s clip: align, group, render, burn."""
    from subtitler import cli, probe

    video = tmp_path / "clip.mov"
    video.write_bytes((FIXTURES / "clip.mov").read_bytes())
    transcript = tmp_path / "script.txt"
    transcript.write_text((FIXTURES / "clip_script.txt").read_text())

    out = tmp_path / "sample.mp4"
    exit_code = cli.main([
        "sample", str(video), str(transcript),
        "--at", "0", "--len", "5", "--out", str(out),
    ])

    assert exit_code == 0
    assert out.exists()

    info = probe.probe(out)
    assert (info.display_width, info.display_height) == (1080, 1920)
    assert info.is_hdr is False
    assert info.duration == pytest.approx(5.0, abs=0.4)

    work = video.parent / ".subtitler" / "clip"
    assert (work / "words.json").exists()
    assert (work / "drift.md").exists()
    assert (work / "subs.ass").exists()


@pytest.mark.gpu
@pytest.mark.slow
def test_second_sample_reuses_cached_alignment(tmp_path):
    from subtitler import cli

    video = tmp_path / "clip.mov"
    video.write_bytes((FIXTURES / "clip.mov").read_bytes())
    transcript = tmp_path / "script.txt"
    transcript.write_text((FIXTURES / "clip_script.txt").read_text())

    cli.main(["sample", str(video), str(transcript), "--at", "0", "--len", "3"])
    words = video.parent / ".subtitler" / "clip" / "words.json"
    first_mtime = words.stat().st_mtime

    cli.main(["sample", str(video), str(transcript), "--at", "0", "--len", "3"])

    assert words.stat().st_mtime == first_mtime, "alignment should not have re-run"
```

- [ ] **Step 2: Run the fast tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'subtitler.cli'`

- [ ] **Step 3: Write the implementation**

`src/subtitler/cli.py`:

```python
"""Command line entry point.

Three commands, but `sample` is the one that matters. Alignment is the
expensive stage and it is cached per video, so the first run on a new video
costs minutes and every subsequent sample costs seconds. That is what makes
iterating on style.toml bearable.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from . import align, ass, clean, drift, extract, group, measure, probe, render, style, workdir
from .models import Cue, Word
from .probe import MediaInfo


@dataclass
class PipelineResult:
    info: MediaInfo
    words: list[Word]
    cues: list[Cue]
    work: workdir.WorkDir


def parse_timecode(value: str) -> float:
    """Parse `90`, `1:30` or `1:02:03` into seconds."""
    parts = value.strip().split(":")
    if not all(part.strip().lstrip("-").isdigit() for part in parts) or len(parts) > 3:
        raise ValueError(f"could not parse timecode {value!r}")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def first_dense_span(words: list[Word], *, window: float = 10.0) -> float:
    """Start time of the densest `window` seconds of speech.

    The opening of a live recording is usually applause or silence, which
    makes a poor reference for judging subtitle style.
    """
    if not words:
        return 0.0

    best_start, best_count = words[0].start, 0
    for candidate in words:
        count = sum(1 for word in words if candidate.start <= word.start < candidate.start + window)
        if count > best_count:
            best_start, best_count = candidate.start, count
    return best_start


def pipeline(video: Path, transcript: Path, style_path: Path, *, force: bool = False) -> PipelineResult:
    work = workdir.for_video(video)
    sty = style.load(style_path)
    info = probe.probe(video)
    work.meta.write_text(json.dumps(asdict(info), indent=1))

    if force or not workdir.is_fresh(work.audio, video):
        print(f"extracting audio from {video.name}", file=sys.stderr)
        extract.extract_audio(video, work.audio)

    cleaned = clean.clean_transcript(transcript.read_text())
    work.script.write_text(cleaned.text)
    work.script_map.write_text(json.dumps(
        [{"line": p.line, "column": p.column} for p in cleaned.positions]
    ))

    if force or not workdir.is_fresh(work.words, work.audio, work.script):
        print("aligning transcript to audio (this is the slow part)", file=sys.stderr)
        words = align.align(work.audio, cleaned.text)
        align.save_words(words, work.words)
    else:
        words = align.load_words(work.words)

    flags = drift.find_drift(words, positions=cleaned.positions)
    work.drift.write_text(drift.render_report(flags, total_words=len(words)))
    if flags:
        print(f"{len(flags)} drift span(s) flagged — see {work.drift}", file=sys.stderr)

    cues = group.group_words(words, max_words=sty.max_words, pause_break=sty.pause_break)
    work.cues.write_text(json.dumps(
        [
            {"start": c.start, "end": c.end,
             "words": [{"text": w.text, "start": w.start, "end": w.end} for w in c.words]}
            for c in cues
        ],
        ensure_ascii=False,
    ))

    return PipelineResult(info=info, words=words, cues=cues, work=work)


def _write_ass(cues: list[Cue], sty: style.Style, info: MediaInfo, path: Path) -> Path:
    measurer = measure.text_measurer(sty.font_path, sty.font_size)
    document = ass.build_ass(cues, sty, info.display_width, info.display_height, measurer)
    path.write_text(document)
    return path


def command_align(args) -> int:
    result = pipeline(args.video, args.transcript, args.style, force=args.force)
    print(f"aligned {len(result.words)} words -> {result.work.words}")
    print(f"drift report -> {result.work.drift}")
    return 0


def command_sample(args) -> int:
    sty = style.load(args.style)
    result = pipeline(args.video, args.transcript, args.style, force=args.force)

    start = parse_timecode(args.at) if args.at is not None else first_dense_span(result.words)
    end = start + args.len

    windowed = render.shift_cues(result.cues, start=start, end=end)
    ass_path = _write_ass(windowed, sty, result.info, result.work.root / "sample.ass")

    out = args.out or args.video.with_name(f"{args.video.stem}_sample.mp4")
    print(f"rendering {args.len}s from {start:.1f}s -> {out}", file=sys.stderr)
    render.burn(
        args.video, ass_path, out, result.info,
        fontsdir=sty.font_path.parent, start=start, duration=float(args.len),
        encoder=args.encoder,
    )
    print(out)
    return 0


def command_render(args) -> int:
    sty = style.load(args.style)
    result = pipeline(args.video, args.transcript, args.style, force=args.force)

    ass_path = _write_ass(result.cues, sty, result.info, result.work.ass)
    out = args.out or args.video.with_name(f"{args.video.stem}_subtitled.mp4")

    print(f"rendering full video -> {out}", file=sys.stderr)
    render.burn(
        args.video, ass_path, out, result.info,
        fontsdir=sty.font_path.parent, encoder=args.encoder,
    )
    print(out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="subtitler",
        description="Burn karaoke-style subtitles into a video from a supplied transcript.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub):
        sub.add_argument("video", type=Path)
        sub.add_argument("transcript", type=Path)
        sub.add_argument("--style", type=Path, default=style.DEFAULT_STYLE_PATH)
        sub.add_argument("--force", action="store_true", help="ignore cached artifacts")
        sub.add_argument("--encoder", default="h264_nvenc")
        sub.add_argument("--out", type=Path, default=None)

    sample = subparsers.add_parser("sample", help="render a short window to check the style")
    add_common(sample)
    sample.add_argument("--at", default=None, help="start timecode, e.g. 2:15 (default: densest speech)")
    sample.add_argument("--len", type=float, default=10.0, help="sample length in seconds")
    sample.set_defaults(func=command_sample)

    full = subparsers.add_parser("render", help="render the whole video")
    add_common(full)
    full.set_defaults(func=command_render)

    align_cmd = subparsers.add_parser("align", help="align only; write words.json and drift.md")
    add_common(align_cmd)
    align_cmd.set_defaults(func=command_align)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the fast tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Write the README**

`README.md` must cover: what the tool does, the `uv sync` install, the three
commands with real examples, the meaning of every `style.toml` key, how to read
`drift.md`, and a note that the output is always SDR BT.709 and that Dolby
Vision cannot survive a burn-in.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -v`
Expected: every test passes, including the `gpu` and `slow` ones.

- [ ] **Step 7: Commit**

```bash
git add src/subtitler/cli.py tests/test_cli.py tests/test_e2e.py README.md
git commit -m "feat: CLI with cached pipeline, sample loop and end-to-end tests"
```

---

### Task 12: Render the real video

**Files:** none — this task produces the deliverable, not code.

- [ ] **Step 1: Save the transcript**

Write the full pregón transcript to `~/githubs/subtitler/transcript.txt`.

- [ ] **Step 2: Align and read the drift report**

```bash
uv run subtitler align /mnt/c/Users/alvar/Downloads/pregon_matamala.mov transcript.txt
cat /mnt/c/Users/alvar/Downloads/.subtitler/pregon_matamala/drift.md
```

Read every flagged span. For each, jump to the timecode in the video and
decide whether to edit `transcript.txt`. Re-run `align` after any edit.

Expect real flags here: this is a live speech, and the transcript is the
written version.

- [ ] **Step 3: Render a 10 second sample and check the style**

```bash
uv run subtitler sample /mnt/c/Users/alvar/Downloads/pregon_matamala.mov transcript.txt --len 10
```

Show the sample to the user. Iterate on `style.toml` — font size, position,
colours, pop scale, words per cue — re-running `sample` after each change,
until they approve the look.

**Do not proceed to the full render without the user's approval of the sample.**

- [ ] **Step 4: Render the full video**

```bash
uv run subtitler render /mnt/c/Users/alvar/Downloads/pregon_matamala.mov transcript.txt
```

- [ ] **Step 5: Verify the output**

```bash
uv run python -c "
from pathlib import Path
from subtitler.probe import probe
i = probe(Path('/mnt/c/Users/alvar/Downloads/pregon_matamala_subtitled.mp4'))
print(i)
assert (i.display_width, i.display_height) == (1080, 1920)
assert not i.is_hdr
assert abs(i.duration - 691.135) < 1.0
print('OK')
"
```

Then watch a few seconds of the result — at the start, in the middle, and at
the end — to confirm the subtitles stay in sync for the whole 11 minutes.
Drift accumulating toward the end is the failure mode to look for.

- [ ] **Step 6: Commit the style used**

```bash
git add style.toml transcript.txt
git commit -m "chore: style and transcript used for the Matamala pregón render"
```
