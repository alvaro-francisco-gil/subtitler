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
    """Escape a path for use inside an ffmpeg filter argument.

    The value is unquoted inside the filter string, so ffmpeg's tokenizer
    treats backslash, colon and single quote as special. The backslash must be
    escaped first, or it would double-escape the escapes added after it.
    """
    return (
        str(path)
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
    )


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
    except binaries.BinaryError as nvenc_error:
        if not encoder.endswith("nvenc"):
            raise
        # NVENC may be unavailable or busy; fall back to software encoding.
        # If that fails too, the original error is the more informative one.
        try:
            binaries.run(build_command(
                video, ass, out, info,
                fontsdir=fontsdir, start=start, duration=duration, encoder="libx264",
            ))
        except binaries.BinaryError as fallback_error:
            raise binaries.BinaryError(
                f"both NVENC and libx264 failed.\n"
                f"NVENC: {nvenc_error}\n"
                f"libx264: {fallback_error}"
            ) from fallback_error
    return out


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
