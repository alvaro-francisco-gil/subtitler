"""Per-video cache directory.

Every stage writes one artifact here. Because each stage's output is a file
on disk, a run can be stopped, its intermediate output inspected or
hand-edited, and resumed. Freshness is decided by mtime, which is what makes
the sample loop fast: alignment is paid for once per video.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkDir:
    root: Path

    @property
    def meta(self) -> Path:
        return self.root / "meta.json"

    @property
    def audio(self) -> Path:
        return self.root / "audio.wav"

    @property
    def script(self) -> Path:
        return self.root / "script.txt"

    @property
    def script_map(self) -> Path:
        return self.root / "script_map.json"

    @property
    def words(self) -> Path:
        return self.root / "words.json"

    @property
    def drift(self) -> Path:
        return self.root / "drift.md"

    @property
    def cues(self) -> Path:
        return self.root / "cues.json"

    @property
    def ass(self) -> Path:
        return self.root / "subs.ass"


def for_video(video: Path, base: Path | None = None) -> WorkDir:
    base = base or video.parent
    root = base / ".subtitler" / video.stem
    root.mkdir(parents=True, exist_ok=True)
    return WorkDir(root=root)


def is_fresh(artifact: Path, *sources: Path) -> bool:
    """True when `artifact` exists and is at least as new as every source."""
    if not artifact.exists():
        return False
    artifact_mtime = artifact.stat().st_mtime
    return all(
        source.exists() and source.stat().st_mtime <= artifact_mtime
        for source in sources
    )
