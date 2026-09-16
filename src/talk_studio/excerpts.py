"""Choosing where to listen.

A good audio excerpt is one where treatments differ audibly: quiet speech
shows what a denoiser does to a voice, loud speech shows compression, and a
noisy pause shows what is left of the room.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from . import binaries
from .timecode import Excerpt

FRAME = 0.5
RATE = 16000
SPEECH_ABOVE_FLOOR_DB = 15.0


def frame_levels(source: Path) -> np.ndarray:
    """RMS level in dBFS of each half-second of the source's audio, mixed to mono."""
    with tempfile.TemporaryDirectory(prefix="talk-studio-levels-") as staging:
        raw = Path(staging) / "audio.raw"
        binaries.run([
            binaries.ffmpeg(), "-y", "-v", "error", "-i", str(source),
            "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", str(raw),
        ])
        samples = np.fromfile(raw, dtype="<i2").astype(np.float64) / 32768.0
    per_frame = int(RATE * FRAME)
    count = len(samples) // per_frame
    frames = samples[: count * per_frame].reshape(count, per_frame)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    return 20 * np.log10(np.maximum(rms, 1e-6))


def suggest(levels: np.ndarray, *, length: float = 12.0, frame: float = FRAME, margin: float = 2.0) -> list[Excerpt]:
    """Up to three non-overlapping excerpts: quietest speech, loudest speech, noisiest pause."""
    width = int(round(length / frame))
    if len(levels) < width:
        raise ValueError(f"source is shorter than one {length:g}s excerpt")
    floor = np.percentile(levels, 10)
    speech = levels > floor + SPEECH_ABOVE_FLOOR_DB

    last = len(levels) - width
    edge = int(round(margin / frame))
    low = min(edge, last)
    high = max(low, last - edge)
    step = max(1, int(round(1.0 / frame)))

    windows = []  # (start index, speech ratio, speech level, noise level)
    for index in range(low, high + 1, step):
        segment, spoken = levels[index:index + width], speech[index:index + width]
        speech_level = float(segment[spoken].mean()) if spoken.any() else -120.0
        windows.append((index, float(spoken.mean()), speech_level, float(np.percentile(segment, 10))))

    chosen: list[int] = []

    def take(candidates, key, reverse):
        for window in sorted(candidates, key=key, reverse=reverse):
            if all(abs(window[0] - other) >= width for other in chosen):
                chosen.append(window[0])
                return

    talking = [w for w in windows if w[1] >= 0.6]
    pauses = [w for w in windows if w[1] <= 0.4]
    take(talking, key=lambda w: w[2], reverse=False)
    take(talking, key=lambda w: w[2], reverse=True)
    take(pauses, key=lambda w: w[3], reverse=True)

    if not chosen and windows:
        # Fallback: if no targeted picks, select the window with the highest speech ratio.
        best = max(windows, key=lambda w: w[1])
        chosen.append(best[0])

    return [Excerpt(round(i * frame, 1), round(i * frame + length, 1)) for i in chosen]
