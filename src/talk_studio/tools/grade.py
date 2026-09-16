"""Colour grading: correcting a phone recording's cast, contrast and colour.

`auto-balance` measures the source and neutralises its cast; `ffmpeg-eq` is
manual. They are different kinds of fix, so a round should hold both.
"""

from __future__ import annotations

import functools
from pathlib import Path

import numpy as np

from .. import binaries, media
from . import GradeTool, Param

ANALYSIS_FRAMES = 8
ANALYSIS_WIDTH = 480


@functools.lru_cache(maxsize=8)
def _channel_stats(source: str, size: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Per-channel mean and 95th percentile over frames spread across the whole source."""
    length = media.duration(Path(source))
    pixels = []
    for index in range(ANALYSIS_FRAMES):
        at = length * (index + 0.5) / ANALYSIS_FRAMES
        raw = binaries.run_bytes([
            binaries.ffmpeg(), "-v", "error", "-ss", f"{at:.3f}", "-i", source, "-frames:v", "1",
            "-vf", f"scale={ANALYSIS_WIDTH}:-2", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ])
        pixels.append(np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.float64))
    everything = np.concatenate(pixels)
    return tuple(everything.mean(axis=0)), tuple(np.percentile(everything, 95, axis=0))


def channel_gains(source: Path, method: str) -> tuple[float, float, float]:
    """Gains that make the source neutral: its average grey (gray-world) or its highlights white (white-patch)."""
    means, highlights = (np.array(x) for x in _channel_stats(str(source), source.stat().st_size))
    gains = means.mean() / means if method == "gray-world" else highlights.max() / highlights
    return tuple(float(g) for g in gains)


def tone(settings: dict) -> list[str]:
    changes = []
    for name, neutral in (("contrast", 1.0), ("brightness", 0.0), ("saturation", 1.0), ("gamma", 1.0)):
        if settings[name] != neutral:
            changes.append(f"{name}={settings[name]:g}")
    return [f"eq={':'.join(changes)}"] if changes else []


TONE_PARAMS = (
    Param("contrast", "float", 1.0, "1 leaves it; above 1 deepens blacks and brightens whites", 0.5, 2.0),
    Param("brightness", "float", 0.0, "0 leaves it; added to every pixel", -0.3, 0.3),
    Param("saturation", "float", 1.0, "1 leaves it; 0 is black and white", 0.0, 3.0),
    Param("gamma", "float", 1.0, "1 leaves it; above 1 lifts the midtones and faces", 0.5, 2.0),
)


class AutoBalance(GradeTool):
    """Measures the colour cast across the whole talk and removes it."""

    name = "auto-balance"
    version = "1"
    params = (
        Param("method", "choice", "white-patch", "what should come out neutral", choices=("white-patch", "gray-world")),
        Param("strength", "float", 1.0, "how much of the correction to apply", 0.0, 1.0),
    ) + TONE_PARAMS

    def filters(self, source: Path, settings: dict) -> str:
        gains = channel_gains(source, settings["method"])
        red, green, blue = (1 + settings["strength"] * (g - 1) for g in gains)
        parts = [f"colorchannelmixer=rr={red:.4f}:gg={green:.4f}:bb={blue:.4f}"] + tone(settings)
        return ",".join(parts)


class FfmpegEq(GradeTool):
    """Manual: colour temperature, green-magenta tint, and tone."""

    name = "ffmpeg-eq"
    version = "1"
    params = (
        Param("temperature", "float", 6500.0, "white balance in kelvin; 6500 leaves it, lower is warmer", 3000.0, 10000.0),
        Param("tint", "float", 0.0, "0 leaves it; negative removes green, positive removes magenta", -0.3, 0.3),
    ) + TONE_PARAMS

    def filters(self, source: Path, settings: dict) -> str:
        parts = []
        if settings["temperature"] != 6500.0:
            parts.append(f"colortemperature=temperature={settings['temperature']:g}")
        if settings["tint"]:
            tint = settings["tint"]
            parts.append(f"colorbalance=gs={tint:g}:gm={tint:g}:gh={tint:g}")
        return ",".join(parts + tone(settings)) or "null"
