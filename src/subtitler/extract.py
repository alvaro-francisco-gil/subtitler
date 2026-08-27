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


def cut_audio(audio: Path, out: Path, start: float, end: float) -> Path:
    """Copy the samples between two timestamps into their own file.

    `-ss` and `-t` go before `-i` so ffmpeg seeks rather than decoding from
    zero, and the stream is copied rather than re-encoded — this runs once per
    repaired span and should cost nothing.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    binaries.run([
        binaries.ffmpeg(),
        "-y",
        "-v", "error",
        "-ss", f"{start:.3f}",
        "-t", f"{end - start:.3f}",
        "-i", str(audio),
        "-c", "copy",
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
