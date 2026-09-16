"""Mastering: shaping a cleaned voice, after the audio decision and before publishing.

Both tools receive audio already set to WORKING_LUFS, so a compressor threshold
means the same thing on every recording. Neither sets the final loudness: the
render does that for every pick, so candidates differ in tone and dynamics only.
"""

from __future__ import annotations

from pathlib import Path

from .. import binaries
from . import AudioTool, Param, Requirement

WORKING_LUFS = -26.0


def db_to_linear(db: float) -> float:
    return 10 ** (db / 20)


class _FfmpegMaster(AudioTool):
    kind = "master"

    def requires(self) -> list[Requirement]:
        try:
            binaries.ffmpeg()
            return [Requirement("ffmpeg", True, "")]
        except binaries.BinaryError as error:
            return [Requirement("ffmpeg", False, str(error))]

    def filters(self, settings: dict) -> list[str]:
        raise NotImplementedError

    def process(self, src: Path, out: Path, settings: dict) -> None:
        binaries.run([
            binaries.ffmpeg(), "-y", "-v", "error", "-i", str(src),
            "-af", ",".join(self.filters(settings)) or "anull",
            "-ar", "48000", "-c:a", "pcm_s16le", str(out),
        ])


def tone(settings: dict) -> list[str]:
    parts = []
    if settings["highpass_hz"] > 0:
        parts.append(f"highpass=f={settings['highpass_hz']:g}")
    if settings["mud_db"]:
        parts.append(f"equalizer=f=300:t=q:w=1:g={settings['mud_db']:g}")
    if settings["presence_db"]:
        parts.append(f"equalizer=f=3500:t=q:w=1:g={settings['presence_db']:g}")
    return parts


TONE_PARAMS = (
    Param("highpass_hz", "float", 70.0, "cut rumble and handling noise below this frequency; 0 disables", 0.0, 200.0),
    Param("mud_db", "float", -2.0, "boxiness around 300 Hz; negative cuts it", -8.0, 4.0),
    Param("presence_db", "float", 2.0, "clarity around 3.5 kHz; positive brings the voice forward", -4.0, 8.0),
)


class VoiceMaster(_FfmpegMaster):
    """A vocal chain: EQ, de-essing and compression."""

    name = "voice-master"
    version = "1"
    params = TONE_PARAMS + (
        Param("warmth_db", "float", 0.0, "low shelf below 150 Hz; positive sounds fuller", -4.0, 6.0),
        Param("air_db", "float", 1.0, "high shelf above 10 kHz; positive sounds brighter", -4.0, 6.0),
        Param("deess", "float", 0.3, "de-esser intensity on sibilants; 0 disables", 0.0, 1.0),
        Param("threshold_db", "float", -30.0, "compression starts this far below full scale; speech sits near -26", -45.0, -10.0),
        Param("ratio", "float", 3.0, "compression ratio; 1 disables", 1.0, 10.0),
    )

    def filters(self, settings: dict) -> list[str]:
        parts = tone(settings)
        if settings["warmth_db"]:
            parts.append(f"lowshelf=f=150:g={settings['warmth_db']:g}")
        if settings["air_db"]:
            parts.append(f"highshelf=f=10000:g={settings['air_db']:g}")
        if settings["deess"] > 0:
            parts.append(f"deesser=i={settings['deess']:g}")
        if settings["ratio"] > 1:
            parts.append(
                f"acompressor=threshold={db_to_linear(settings['threshold_db']):.5f}"
                f":ratio={settings['ratio']:g}:attack=10:release=150"
            )
        return parts


class SpeechLeveler(_FfmpegMaster):
    """Rides the level phrase by phrase, so quiet sentences come up to the loud ones."""

    name = "speech-leveler"
    version = "1"
    params = TONE_PARAMS + (
        Param("max_boost", "float", 4.0, "most a quiet phrase is raised, as a factor", 1.0, 20.0),
        Param("max_cut", "float", 2.0, "most a loud phrase is lowered, as a factor", 1.0, 20.0),
    )

    def filters(self, settings: dict) -> list[str]:
        leveler = f"speechnorm=e={settings['max_boost']:g}:c={settings['max_cut']:g}:r=0.0001:l=1"
        return tone(settings) + [leveler]
