"""The tool contract.

A tool is a thin adapter: it declares its settings and requirements, and knows
how to process. Audio tools only implement `process`, a WAV-in/WAV-out step;
`AudioTool` turns that into the `sample` and `apply` every tool offers.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .. import binaries, media
from ..timecode import Excerpt


class ToolError(Exception):
    """Invalid settings, a missing requirement, or a failed run."""


@dataclass(frozen=True)
class Param:
    name: str
    kind: str  # float | int | bool | choice
    default: object
    help: str
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()

    def coerce(self, raw: object) -> object:
        try:
            if self.kind == "float":
                value: object = float(raw)
            elif self.kind == "int":
                value = int(raw)
            elif self.kind == "bool":
                text = str(raw).lower()
                if isinstance(raw, bool):
                    value = raw
                elif text in ("1", "true", "yes", "on"):
                    value = True
                elif text in ("0", "false", "no", "off"):
                    value = False
                else:
                    raise ValueError
            elif self.kind == "choice":
                value = str(raw)
                if value not in self.choices:
                    raise ToolError(f"{self.name} must be one of {', '.join(self.choices)}, not {value!r}")
            else:
                raise ToolError(f"unknown param kind {self.kind!r}")
        except (TypeError, ValueError):
            raise ToolError(f"{self.name} expects {self.kind}, got {raw!r}") from None
        if self.kind in ("float", "int"):
            low = self.minimum if self.minimum is not None else float("-inf")
            high = self.maximum if self.maximum is not None else float("inf")
            if not low <= value <= high:
                raise ToolError(f"{self.name} must be between {self.minimum} and {self.maximum}, got {value}")
        return value


@dataclass(frozen=True)
class Requirement:
    name: str
    ok: bool
    hint: str


class Tool:
    kind = ""
    name = ""
    version = ""
    params: tuple[Param, ...] = ()
    # What a sample of this tool is saved as.
    suffix = ".wav"

    def requires(self) -> list[Requirement]:
        return []

    def settings(self, raw: dict) -> dict:
        known = {param.name: param for param in self.params}
        unknown = sorted(set(raw) - set(known))
        if unknown:
            raise ToolError(
                f"{self.name} has no setting {', '.join(unknown)}; it has {', '.join(known) or 'none'}"
            )
        return {name: (param.coerce(raw[name]) if name in raw else param.default) for name, param in known.items()}


class AudioTool(Tool):
    kind = "audio"
    # Seconds decoded before an excerpt and trimmed afterwards, so denoisers and
    # compressors have settled by the time the human starts listening.
    preroll = 2.0

    def process(self, src: Path, out: Path, settings: dict) -> None:
        raise NotImplementedError

    def sample(self, source: Path, excerpt: Excerpt, settings: dict, out: Path) -> Path:
        lead = min(self.preroll, excerpt.start)
        with tempfile.TemporaryDirectory(prefix="talk-studio-") as staging:
            staging = Path(staging)
            raw = media.extract_wav(source, staging / "raw.wav", start=excerpt.start - lead, length=excerpt.duration + lead)
            processed = staging / "processed.wav"
            self.process(raw, processed, settings)
            media.extract_wav(processed, out, start=lead, length=excerpt.duration)
        media.validate(out, expected=excerpt.duration, tolerance=media.AUDIO_TOLERANCE, streams=("audio",))
        return out

    def apply(self, source: Path, settings: dict, out: Path) -> Path:
        with tempfile.TemporaryDirectory(prefix="talk-studio-") as staging:
            raw = media.extract_wav(source, Path(staging) / "raw.wav")
            self.process(raw, out, settings)
        media.validate(out, expected=media.stream_durations(source)["audio"], tolerance=media.AUDIO_TOLERANCE, streams=("audio",))
        return out


class Original(AudioTool):
    """The unprocessed source, rendered like any candidate so it can be compared."""

    name = "original"
    version = "1"

    def process(self, src: Path, out: Path, settings: dict) -> None:
        shutil.copyfile(src, out)


class GradeTool(Tool):
    """A colour grade: an ffmpeg video filter chain, sampled as a still frame."""

    kind = "grade"
    suffix = ".jpg"

    def filters(self, source: Path, settings: dict) -> str:
        raise NotImplementedError

    def sample(self, source: Path, excerpt: Excerpt, settings: dict, out: Path) -> Path:
        out.parent.mkdir(parents=True, exist_ok=True)
        binaries.run([
            binaries.ffmpeg(), "-y", "-v", "error", "-ss", f"{excerpt.start:.3f}", "-i", str(source),
            "-frames:v", "1", "-vf", self.filters(source, settings), "-q:v", "2", str(out),
        ])
        if not out.exists() or out.stat().st_size == 0:
            raise media.RenderError(f"{out.name} was not written")
        return out


class OriginalFrame(GradeTool):
    """The ungraded frame."""

    name = "original"
    version = "1"

    def filters(self, source: Path, settings: dict) -> str:
        return "null"


def reference_for(decision: str) -> Tool:
    return OriginalFrame() if decision == "grade" else Original()


def all_tools() -> list[Tool]:
    from .deepfilternet import DeepFilterNet
    from .ffmpeg_chain import FfmpegChain
    from .grade import AutoBalance, FfmpegEq
    from .mastering import SpeechLeveler, VoiceMaster

    return [FfmpegChain(), DeepFilterNet(), VoiceMaster(), SpeechLeveler(), AutoBalance(), FfmpegEq()]


def get_tool(name: str) -> Tool:
    for tool in all_tools():
        if tool.name == name:
            return tool
    raise ToolError(f"no tool named {name!r}; available: {', '.join(t.name for t in all_tools())}")
