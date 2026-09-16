"""DeepFilterNet 3: a speech denoiser model, run in its own environment.

The package does not depend on torch, and torchaudio 2.1+ removed an API it
imports, so the environment is pinned here. CPU wheels are enough: the model
runs at roughly 35× real time on a desktop CPU.

deepFilter holds a whole file in memory, about 1.1 GB per minute of stereo
audio, so a full talk is cut into overlapping chunks that are denoised in one
call and crossfaded back together at the middle of each overlap.
"""

from __future__ import annotations

import shutil
import tempfile
import wave
from pathlib import Path

import numpy as np

from .. import binaries, media
from . import AudioTool, Param, Requirement, ToolError

UVX_ARGS = (
    "--python", "3.11",
    "--index", "https://download.pytorch.org/whl/cpu",
    "--index-strategy", "unsafe-best-match",
    "--from", "deepfilternet==0.5.6",
    "--with", "torch==2.0.1",
    "--with", "torchaudio==2.0.2",
    "--with", "numpy<2",
)

CHUNK_SECONDS = 60.0
OVERLAP_SECONDS = 2.0
FADE_SECONDS = 0.5


class DeepFilterNet(AudioTool):
    name = "deepfilternet"
    version = "0.5.6"
    params = (
        Param("atten_lim_db", "float", 100.0, "most the model may remove, in dB; lower keeps more room sound", 3.0, 100.0),
        Param("post_filter", "bool", False, "extra attenuation of very noisy stretches"),
    )

    def requires(self) -> list[Requirement]:
        return [Requirement("uvx", shutil.which("uvx") is not None, "install uv: https://docs.astral.sh/uv/")]

    def command(self, sources: list[Path], out_dir: Path, settings: dict) -> list[str]:
        command = [
            "uvx", *UVX_ARGS, "deepFilter", "--no-suffix",
            "--atten-lim", f"{settings['atten_lim_db']:g}", "-o", str(out_dir),
        ]
        if settings["post_filter"]:
            command.append("--pf")
        command.extend(str(source) for source in sources)
        return command

    def denoise(self, sources: list[Path], out_dir: Path, settings: dict) -> None:
        command = self.command(sources, out_dir, settings)
        # The pinned environment is cached after the first run. Offline, uvx skips
        # re-resolving against two package indexes, which is slower and fails
        # outright whenever the network drops; online is only for the first run.
        try:
            binaries.run([command[0], "--offline", *command[1:]])
        except binaries.BinaryError:
            binaries.run(command)

    def process(self, src: Path, out: Path, settings: dict) -> None:
        with tempfile.TemporaryDirectory(prefix="talk-studio-dfn-") as staging:
            staging = Path(staging)
            source = media.extract_wav(src, staging / "source.wav")
            (staging / "chunks").mkdir()
            (staging / "denoised").mkdir()
            with wave.open(str(source), "rb") as reader:
                params = reader.getparams()
                bounds = _bounds(params.nframes, params.framerate)
                pieces = []
                for index, (low, high) in enumerate(_spans(bounds, params.framerate)):
                    reader.setpos(low)
                    chunk = staging / "chunks" / f"{index:04d}.wav"
                    with wave.open(str(chunk), "wb") as writer:
                        writer.setparams(params)
                        writer.writeframes(reader.readframes(high - low))
                    pieces.append((low, high, chunk))
            self.denoise([chunk for _, _, chunk in pieces], staging / "denoised", settings)
            _join(pieces, bounds, staging / "denoised", params, out)


def _bounds(frames: int, rate: int) -> list[int]:
    """Where each chunk hands over to the next; the last chunk absorbs the remainder."""
    size = int(CHUNK_SECONDS * rate)
    count = max(1, frames // size)
    return [index * size for index in range(count)] + [frames]


def _spans(bounds: list[int], rate: int) -> list[tuple[int, int]]:
    overlap = int(OVERLAP_SECONDS * rate)
    return [
        (max(0, bounds[i] - overlap), min(bounds[-1], bounds[i + 1] + overlap))
        for i in range(len(bounds) - 1)
    ]


def _join(pieces, bounds: list[int], denoised: Path, params, out: Path) -> None:
    channels, rate = params.nchannels, params.framerate
    half = int(FADE_SECONDS * rate) // 2
    width = 2 * half
    ramp = ((np.arange(width) + 0.5) / width)[:, None] if width else None
    last = len(pieces) - 1
    tail = None
    with wave.open(str(out), "wb") as writer:
        writer.setparams(params)
        for index, (low, high, chunk) in enumerate(pieces):
            produced = denoised / chunk.name
            if not produced.exists():
                raise ToolError(f"deepFilter finished but wrote no {chunk.name}")
            with wave.open(str(produced), "rb") as reader:
                if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) != (channels, 2, rate):
                    raise ToolError(f"deepFilter changed the format of {chunk.name}")
                if reader.getnframes() != high - low:
                    raise ToolError(f"deepFilter returned {reader.getnframes()} frames for {chunk.name}, expected {high - low}")
                frames = np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2")
            frames = frames.reshape(-1, channels).astype(np.float64)
            begin = bounds[index] - half if index else 0
            end = bounds[index + 1] + half if index < last else bounds[-1]
            segment = frames[begin - low:end - low]
            if tail is not None and width:
                segment[:width] = tail * (1 - ramp) + segment[:width] * ramp
            if index < last and width:
                tail = segment[-width:].copy()
                segment = segment[:-width]
            writer.writeframes(np.clip(np.round(segment), -32768, 32767).astype("<i2").tobytes())
