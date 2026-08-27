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

import re
import shutil
import tempfile
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

SAFE_PATH = re.compile(r"^[A-Za-z0-9/._-]+$")


# A render that stops early still exits 0, so the output length is checked
# rather than trusted. One frame of slack absorbs rounding at the tail.
TRUNCATION_TOLERANCE = 1.0


class TruncatedRenderError(Exception):
    """ffmpeg exited cleanly but wrote a shorter video than it was asked for."""


class UnsafePathError(Exception):
    """A path that must reach ffmpeg's filter string contains characters we cannot escape."""


def _assert_safe(path: Path) -> str:
    """Guarantee a path is free of every character ffmpeg's filter parser treats as special.

    ffmpeg parses the filter string at two levels, and a literal backslash cannot
    be escaped reliably at all. Rather than escape user paths, we stage files into
    a directory we name ourselves and assert the result is safe.
    """
    text = str(path)
    if not SAFE_PATH.match(text):
        raise UnsafePathError(
            f"refusing to pass {text!r} to an ffmpeg filter: it contains characters "
            f"the filter parser cannot round-trip. This is a bug — staged paths "
            f"should always be safe."
        )
    return text


def escape_filter_path(path: Path) -> str:
    """Escape a path for use inside an ffmpeg filter argument.

    Only paths we have staged ourselves reach this, and `_assert_safe` has
    already guaranteed they contain no quotes or backslashes. A colon is still
    escaped because it separates filter options.
    """
    return str(path).replace(":", "\\:")


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

    with tempfile.TemporaryDirectory(prefix="subtitler-") as staging:
        staged_ass = Path(staging) / "subs.ass"
        staged_fonts = Path(staging) / "fonts"
        shutil.copyfile(ass, staged_ass)
        shutil.copytree(fontsdir, staged_fonts)

        _assert_safe(staged_ass)
        _assert_safe(staged_fonts)

        def command_for(enc: str) -> list[str]:
            return build_command(
                video, staged_ass, out, info,
                fontsdir=staged_fonts, start=start, duration=duration, encoder=enc,
            )

        try:
            binaries.run(command_for(encoder))
        except binaries.BinaryError as nvenc_error:
            if not encoder.endswith("nvenc"):
                raise
            # NVENC may be unavailable or busy; fall back to software encoding.
            try:
                binaries.run(command_for("libx264"))
            except binaries.BinaryError as fallback_error:
                raise binaries.BinaryError(
                    f"both NVENC and libx264 failed.\n"
                    f"NVENC: {nvenc_error}\n"
                    f"libx264: {fallback_error}"
                ) from fallback_error

    expected = duration if duration is not None else info.duration - (start or 0.0)
    _assert_complete(out, expected)
    return out


def _assert_complete(out: Path, expected: float) -> None:
    """Fail loudly when ffmpeg wrote a short file.

    A read that stalls part-way through a large source can end the stream
    early, and ffmpeg reports that as success. Silently handing back a
    truncated video is the worst outcome available, so the length is checked
    against what was asked for.
    """
    from .probe import probe

    actual = probe(out).duration
    if actual < expected - TRUNCATION_TOLERANCE:
        raise TruncatedRenderError(
            f"{out} is {actual:.1f}s but {expected:.1f}s was expected. "
            f"ffmpeg exited cleanly, so the source read most likely ended early; "
            f"re-run the render."
        )


def shift_cues(cues: list[Cue], start: float, end: float) -> list[Cue]:
    """Keep cues overlapping [start, end) and rebase their times to the window.

    The sample render seeks into the video, so subtitle times have to be
    rebased to the clip. A cue straddling either boundary is clamped into the
    window rather than dropped, so a partly-visible cue still renders.
    """
    window = end - start
    shifted = []
    for cue in cues:
        if cue.end <= start or cue.start >= end:
            continue
        shifted.append(
            Cue(words=tuple(
                Word(
                    text=word.text,
                    start=min(max(word.start - start, 0.0), window),
                    end=min(max(word.end - start, 0.0), window),
                    score=word.score,
                )
                for word in cue.words
            ))
        )
    return shifted
