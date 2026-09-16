"""Vertical clips: one passage of the finished talk, reframed to 9:16 with karaoke captions.

Clips are cut from the final render, so they inherit the picked audio, mastering
and grade. Each tool is a different way of fitting a wide stage shot into a tall
frame; the human picks per clip.

Finding the speaker needs no vision model: the camera does not move, so the
median of the clip's frames is the empty stage, and the speaker is what is much
darker than it. Where that signal is too weak (off the edge, in front of the
dark board) the last good position holds.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import tempfile
from pathlib import Path

import numpy as np

from .. import binaries, media, probe
from ..captions import ass, group, measure, style
from ..captions.models import Word
from ..captions.render import escape_filter_path
from ..timecode import Excerpt
from . import Param, Tool

OUT_WIDTH, OUT_HEIGHT = 1080, 1920
TRACK_WIDTH, TRACK_HEIGHT, TRACK_FPS = 480, 270, 2
DARKER_BY = 35          # grey levels below the empty stage that count as the speaker
BODY_WINDOW = 60        # analysis columns (of 480) a body spans
MIN_MASS = 600          # below this the speaker is not visible enough to trust
HIGHLIGHTS = {"yellow": "#F2C14E", "white": "#FFFFFF", "green": "#7CFC8A"}
FONTS = Path(__file__).resolve().parents[3] / "assets" / "fonts"


# finding the speaker and the screen


@dataclasses.dataclass(frozen=True)
class Stage:
    centres: tuple[float, ...]            # speaker centre per second, as a fraction of width
    screen: tuple[float, float, float, float]  # projected slide: left, top, right, bottom fractions


def smooth_track(raw: list[float | None], window: int = 5) -> list[float]:
    """Fill gaps with the last good position, then a running median and mean."""
    first = next((x for x in raw if x is not None), 0.5)
    filled, last = [], first
    for x in raw:
        last = x if x is not None else last
        filled.append(last)
    values = np.array(filled, dtype=float)
    half = window // 2
    padded = np.pad(values, half, mode="edge")
    median = np.array([np.median(padded[i:i + window]) for i in range(len(values))])
    padded = np.pad(median, half, mode="edge")
    return [float(np.mean(padded[i:i + window])) for i in range(len(values))]


def screen_box(background: np.ndarray) -> tuple[float, float, float, float]:
    """The projected slide: the rows and columns that are mostly bright."""
    bright = background > (np.percentile(background, 10) + np.percentile(background, 90)) / 2
    cols = np.where(bright.mean(axis=0) > 0.5)[0]
    rows = np.where(bright.mean(axis=1) > 0.5)[0]
    if len(cols) < 10 or len(rows) < 10:
        return (0.0, 0.0, 1.0, 1.0)
    height, width = background.shape
    return (cols[0] / width, rows[0] / height, (cols[-1] + 1) / width, (rows[-1] + 1) / height)


@functools.lru_cache(maxsize=32)
def _analyse(source: str, size: int, start: float, duration: float) -> Stage:
    raw = binaries.run_bytes([
        binaries.ffmpeg(), "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", source,
        "-vf", f"fps={TRACK_FPS},scale={TRACK_WIDTH}:{TRACK_HEIGHT}", "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ])
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, TRACK_HEIGHT, TRACK_WIDTH).astype(np.float32)
    background = np.median(frames, axis=0)
    per_frame: list[float | None] = []
    for frame in frames:
        dark = (background - frame) > DARKER_BY
        dark[: int(TRACK_HEIGHT * 0.2)] = False  # the projector casing and ceiling
        mass = np.convolve(dark.sum(axis=0).astype(float), np.ones(BODY_WINDOW), mode="same")
        per_frame.append(float(mass.argmax()) / TRACK_WIDTH if mass.max() >= MIN_MASS else None)
    smoothed = smooth_track(per_frame)
    per_second = tuple(smoothed[::TRACK_FPS]) or (0.5,)
    return Stage(centres=per_second, screen=screen_box(background))


def analyse(source: Path, excerpt: Excerpt) -> Stage:
    return _analyse(str(source), source.stat().st_size, excerpt.start, excerpt.duration)


def crop_x(centres: tuple[float, ...], frame_width: int, crop_width: int) -> str:
    """An ffmpeg expression in `t`: the crop's left edge, linear between per-second positions."""
    lefts = [c * frame_width - crop_width / 2 for c in centres]
    expression = f"{lefts[0]:.1f}"
    for second, (a, b) in enumerate(zip(lefts, lefts[1:])):
        if abs(b - a) >= 0.5:
            expression += f"+({b - a:.1f})*clip(t-{second},0,1)"
    return f"clip({expression},0,{frame_width - crop_width})"


# captions


def clip_cues(words: list[Word], excerpt: Excerpt, sty: style.Style) -> list:
    inside = [
        Word(w.text, max(w.start - excerpt.start, 0.0), min(w.end - excerpt.start, excerpt.duration), w.score)
        for w in words if w.end > excerpt.start and w.start < excerpt.end
    ]
    measurer = measure.text_measurer(sty.font_path, sty.font_size)

    def fits(texts: list[str]) -> bool:
        rendered = [t.upper() if sty.all_caps else t for t in texts]
        gap = measurer(" ") * sty.word_spacing
        return sum(measurer(t) for t in rendered) + gap * (len(rendered) - 1) <= OUT_WIDTH * sty.max_width

    return group.group_words(inside, max_words=sty.max_words, pause_break=sty.pause_break, fits=fits)


def write_ass(words_path: Path, excerpt: Excerpt, settings: dict, position: float, out: Path) -> Path:
    base = style.load()
    sty = dataclasses.replace(
        base,
        highlight=HIGHLIGHTS[settings["highlight"]],
        position=position,
        font_size=int(round(base.font_size * settings["caption_size"])),
        max_words=int(settings["words_per_cue"]),
    )
    words = [Word(**entry) for entry in json.loads(Path(words_path).read_text())]
    document = ass.build_ass(
        clip_cues(words, excerpt, sty), sty, OUT_WIDTH, OUT_HEIGHT,
        measure.text_measurer(sty.font_path, sty.font_size),
    )
    out.write_text(document)
    return out


# the tools

CAPTION_PARAMS = (
    Param("highlight", "choice", "yellow", "colour of the word being spoken", choices=tuple(HIGHLIGHTS)),
    Param("caption_size", "float", 1.0, "caption size relative to the style file", 0.6, 1.6),
    Param("words_per_cue", "int", 3, "most words on screen at once", 1, 6),
)

GPU_ENCODER = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0"]
CPU_ENCODER = ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]


class ClipTool(Tool):
    kind = "clip"
    suffix = ".mp4"
    captions = True

    def graph(self, source: Path, excerpt: Excerpt, settings: dict, width: int, height: int) -> tuple[str, float]:
        """A filter graph from [0:v] to [framed], and where the captions sit (fraction of height)."""
        raise NotImplementedError

    def sample(self, source: Path, excerpt: Excerpt, settings: dict, out: Path, *, words: Path | None = None) -> Path:
        info = probe.probe(source)
        framed, position = self.graph(source, excerpt, settings, info.display_width, info.display_height)
        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="talk-studio-clip-") as staging:
            if self.captions:
                if words is None:
                    raise media.RenderError("this project has no word timings for captions; pass --words to `clips add`")
                subtitles = write_ass(words, excerpt, settings, position, Path(staging) / "captions.ass")
                framed += f";[framed]subtitles={escape_filter_path(subtitles)}:fontsdir={escape_filter_path(FONTS)}[out]"
            else:
                framed += ";[framed]null[out]"
            head = [
                binaries.ffmpeg(), "-y", "-v", "error", "-ss", f"{excerpt.start:.3f}", "-t", f"{excerpt.duration:.3f}",
                "-i", str(source), "-filter_complex", framed, "-map", "[out]", "-map", "0:a:0",
            ]
            tail = ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
            try:
                binaries.run(head + GPU_ENCODER + tail)
            except binaries.BinaryError:
                binaries.run(head + CPU_ENCODER + tail)
        media.validate(out, expected=excerpt.duration, tolerance=0.1, streams=("video", "audio"))
        return out


class ClipOriginal(ClipTool):
    """The passage as it is in the talk, wide and without captions."""

    name = "original"
    version = "1"
    captions = False

    def graph(self, source, excerpt, settings, width, height):
        return "[0:v]null[framed]", 0.0


class FollowSpeaker(ClipTool):
    """A 9:16 window that follows the speaker across the stage."""

    name = "follow-speaker"
    version = "1"
    params = (
        Param("zoom", "float", 1.0, "1 uses the full height; higher crops tighter on the speaker", 1.0, 1.8),
        Param("caption_position", "float", 0.72, "centre of the captions, as a fraction of height", 0.15, 0.9),
    ) + CAPTION_PARAMS

    def graph(self, source, excerpt, settings, width, height):
        crop_h = int(height / settings["zoom"]) // 2 * 2
        crop_w = int(crop_h * OUT_WIDTH / OUT_HEIGHT) // 2 * 2
        top = int((height - crop_h) * 0.3)
        x = crop_x(analyse(source, excerpt).centres, width, crop_w)
        graph = f"[0:v]crop=w={crop_w}:h={crop_h}:x='{x}':y={top},scale={OUT_WIDTH}:{OUT_HEIGHT},setsar=1[framed]"
        return graph, settings["caption_position"]


class SlideAndSpeaker(ClipTool):
    """The slide above, the speaker below, captions on the seam."""

    name = "slide-and-speaker"
    version = "1"
    params = (
        Param("split", "float", 0.42, "how much of the height the slide takes", 0.3, 0.6),
    ) + CAPTION_PARAMS

    def graph(self, source, excerpt, settings, width, height):
        stage = analyse(source, excerpt)
        top_h = int(OUT_HEIGHT * settings["split"]) // 2 * 2
        bottom_h = OUT_HEIGHT - top_h
        left, top, right, bottom = stage.screen
        sx, sy = int(left * width), int(top * height)
        sw, sh = int((right - left) * width) // 2 * 2, int((bottom - top) * height) // 2 * 2
        crop_h = height
        crop_w = min(width, int(crop_h * OUT_WIDTH / bottom_h) // 2 * 2)
        x = crop_x(stage.centres, width, crop_w)
        graph = (
            f"[0:v]split[a][b];"
            f"[a]crop={sw}:{sh}:{sx}:{sy},scale={OUT_WIDTH}:{top_h}:force_original_aspect_ratio=increase,"
            f"crop={OUT_WIDTH}:{top_h}[slide];"
            f"[b]crop=w={crop_w}:h={crop_h}:x='{x}':y=0,scale={OUT_WIDTH}:{bottom_h},setsar=1[speaker];"
            f"[slide][speaker]vstack[framed]"
        )
        return graph, settings["split"]


class BlurLetterbox(ClipTool):
    """The whole wide shot in a band, over a blurred, darkened copy of itself."""

    name = "blur-letterbox"
    version = "1"
    params = (
        Param("band_centre", "float", 0.42, "centre of the wide shot, as a fraction of height", 0.2, 0.6),
        Param("blur", "int", 30, "background blur radius", 0, 60),
    ) + CAPTION_PARAMS

    def graph(self, source, excerpt, settings, width, height):
        band_h = int(OUT_WIDTH * height / width) // 2 * 2
        y = int(OUT_HEIGHT * settings["band_centre"] - band_h / 2)
        blur = f",boxblur={settings['blur']}:2" if settings["blur"] else ""
        graph = (
            f"[0:v]split[a][b];"
            f"[a]scale=-2:{OUT_HEIGHT},crop={OUT_WIDTH}:{OUT_HEIGHT}{blur},eq=brightness=-0.12[bg];"
            f"[b]scale={OUT_WIDTH}:{band_h}[band];"
            f"[bg][band]overlay=0:{y},setsar=1[framed]"
        )
        below = (y + band_h) / OUT_HEIGHT
        return graph, min(0.9, below + 0.1)
