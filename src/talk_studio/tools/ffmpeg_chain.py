"""Classic filters, no models: rumble cut, FFT denoise, compression, loudness."""

from __future__ import annotations

from pathlib import Path

from .. import binaries
from . import AudioTool, Param, Requirement

# -18 dBFS as the linear amplitude acompressor expects.
COMPRESSOR = "acompressor=threshold=0.125893:ratio=3:attack=5:release=100:makeup=2"


def filter_chain(settings: dict) -> str:
    parts = []
    if settings["highpass_hz"] > 0:
        parts.append(f"highpass=f={settings['highpass_hz']:g}")
    if settings["denoise_db"] > 0:
        parts.append(f"afftdn=nr={settings['denoise_db']:g}:nf={settings['noise_floor_db']:g}:tn=1")
    if settings["compress"]:
        parts.append(COMPRESSOR)
    if settings["loudness_lufs"] < 0:
        parts.append(f"loudnorm=I={settings['loudness_lufs']:g}:TP=-1.5:LRA=11")
    return ",".join(parts) or "anull"


class FfmpegChain(AudioTool):
    name = "ffmpeg-chain"
    version = "1"
    params = (
        Param("highpass_hz", "float", 80.0, "cut rumble below this frequency; 0 disables", 0.0, 300.0),
        Param("denoise_db", "float", 12.0, "afftdn noise reduction in dB; 0 disables", 0.0, 40.0),
        Param("noise_floor_db", "float", -50.0, "afftdn starting estimate of the noise floor", -80.0, -20.0),
        Param("compress", "bool", True, "gentle 3:1 compression above -18 dBFS"),
        Param("loudness_lufs", "float", -16.0, "loudnorm integrated target; 0 disables", -30.0, 0.0),
    )

    def requires(self) -> list[Requirement]:
        try:
            binaries.ffmpeg()
            return [Requirement("ffmpeg", True, "")]
        except binaries.BinaryError as error:
            return [Requirement("ffmpeg", False, str(error))]

    def process(self, src: Path, out: Path, settings: dict) -> None:
        binaries.run([
            binaries.ffmpeg(), "-y", "-v", "error", "-i", str(src),
            "-af", filter_chain(settings), "-ar", "48000", "-c:a", "pcm_s16le", str(out),
        ])
