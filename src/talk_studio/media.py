"""Facts about media files that every tool relies on.

Fingerprints decide whether picks still apply, durations decide whether a
render is whole, and loudness is what the review page matches levels with.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import binaries

AUDIO_TOLERANCE = 0.05
CHUNK = 8 * 1024 * 1024
LUFS_RE = re.compile(r"I:\s+(-?\d+(?:\.\d+)?) LUFS")
PEAK_RE = re.compile(r"Peak:\s+(-?\d+(?:\.\d+)?|-inf) dBFS")

# What YouTube and Spotify normalise to: publishing louder only gets turned down,
# publishing quieter plays quieter than everything around it.
PUBLISH_LUFS = -14.0
PUBLISH_TRUE_PEAK = -1.0
# The limiter aims below the ceiling: resampling back from 192 kHz can add a
# fraction of a dB of inter-sample peak.
LIMITER_MARGIN_DB = 0.5


class RenderError(Exception):
    """A render finished but its output is not what was asked for."""


def stream_durations(path: Path) -> dict[str, float]:
    """Duration of the first video and first audio stream, keyed `video` / `audio`."""
    result = binaries.run([
        binaries.ffprobe(), "-v", "error",
        "-show_entries", "stream=codec_type,duration:format=duration",
        "-of", "json", str(path),
    ])
    payload = json.loads(result.stdout)
    fallback = float(payload.get("format", {}).get("duration") or 0.0)
    durations: dict[str, float] = {}
    for stream in payload.get("streams", []):
        kind = stream.get("codec_type")
        if kind in ("video", "audio") and kind not in durations:
            durations[kind] = float(stream.get("duration") or fallback)
    return durations


def duration(path: Path) -> float:
    durations = stream_durations(path)
    if not durations:
        raise RenderError(f"{path} has no audio or video stream")
    return max(durations.values())


def fingerprint(path: Path) -> str:
    """Size, duration and the first and last 8 MB — cheap on a 4 GB file, and sees re-exports."""
    size = path.stat().st_size
    digest = hashlib.sha256(f"{size}:{duration(path):.3f}".encode())
    with path.open("rb") as handle:
        digest.update(handle.read(CHUNK))
        if size > CHUNK:
            handle.seek(max(size - CHUNK, CHUNK))
            digest.update(handle.read(CHUNK))
    return digest.hexdigest()[:32]


def extract_wav(source: Path, out: Path, *, start: float = 0.0, length: float | None = None) -> Path:
    """Decode audio to 48 kHz 16-bit WAV, keeping the source's channel count."""
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [binaries.ffmpeg(), "-y", "-v", "error"]
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    if length is not None:
        args += ["-t", f"{length:.3f}"]
    args += ["-i", str(source), "-vn", "-ar", "48000", "-c:a", "pcm_s16le", str(out)]
    binaries.run(args)
    return out


def integrated_loudness(path: Path) -> float:
    result = binaries.run([
        binaries.ffmpeg(), "-hide_banner", "-nostats", "-i", str(path),
        "-af", "ebur128=framelog=quiet", "-f", "null", "-",
    ])
    matches = LUFS_RE.findall(result.stderr)
    if not matches:
        raise RenderError(f"could not measure the loudness of {path}")
    return float(matches[-1])


def validate(path: Path, *, expected: float, tolerance: float, streams: tuple[str, ...]) -> None:
    """Fail loudly on a render that is missing a stream, short, or out of sync."""
    if not path.exists():
        raise RenderError(f"{path} was not written")
    durations = stream_durations(path)
    for kind in streams:
        if kind not in durations:
            raise RenderError(f"{path.name} has no {kind} stream")
        if abs(durations[kind] - expected) > tolerance:
            raise RenderError(
                f"{path.name}: {kind} lasts {durations[kind]:.3f}s, "
                f"expected {expected:.3f}s (±{tolerance:.3f}s)"
            )
    if "video" in streams and "audio" in streams:
        if abs(durations["video"] - durations["audio"]) > tolerance:
            raise RenderError(
                f"{path.name}: video {durations['video']:.3f}s and audio "
                f"{durations['audio']:.3f}s differ by more than {tolerance:.3f}s"
            )


def measure(path: Path) -> tuple[float, float]:
    """Integrated loudness in LUFS and true peak in dBTP."""
    result = binaries.run([
        binaries.ffmpeg(), "-hide_banner", "-nostats", "-i", str(path),
        "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-",
    ])
    loudness, peaks = LUFS_RE.findall(result.stderr), PEAK_RE.findall(result.stderr)
    if not loudness or not peaks:
        raise RenderError(f"could not measure the loudness of {path}")
    peak = peaks[-1]
    return float(loudness[-1]), float("-inf") if peak == "-inf" else float(peak)


def _gain(src: Path, out: Path, filters: str) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    binaries.run([
        binaries.ffmpeg(), "-y", "-v", "error", "-i", str(src),
        "-af", filters, "-ar", "48000", "-c:a", "pcm_s16le", str(out),
    ])
    return out


def set_loudness(src: Path, out: Path, *, target: float) -> Path:
    """A static gain towards `target` LUFS, held back where it would push a peak past -0.5 dBTP."""
    loudness, peak = measure(src)
    gain = min(target - loudness, -0.5 - peak)
    return _gain(src, out, f"volume={gain:.2f}dB")


def publish_loudness(src: Path, out: Path, *, target: float = PUBLISH_LUFS, true_peak: float = PUBLISH_TRUE_PEAK, passes: int = 4) -> Path:
    """Gain up to the publishing target with a true-peak limiter, correcting for what the limiter takes.

    Limiting lowers the integrated loudness, so the gain is re-measured and nudged
    until the result lands within 0.3 LU of the target.
    """
    limit = 10 ** ((true_peak - LIMITER_MARGIN_DB) / 20)
    gain = target - measure(src)[0]
    for _ in range(passes):
        _gain(src, out, (
            f"volume={gain:.2f}dB,aresample=192000,"
            f"alimiter=limit={limit:.4f}:attack=5:release=50:level=false,aresample=48000"
        ))
        loudness, peak = measure(out)
        if abs(loudness - target) <= 0.3:
            break
        gain += target - loudness
    if abs(loudness - target) > 0.5 or peak > true_peak:
        raise RenderError(
            f"{out.name}: reached {loudness:.1f} LUFS / {peak:.1f} dBTP, "
            f"wanted {target:g} LUFS under {true_peak:g} dBTP"
        )
    return out
