"""The final render, from picks only.

Step 1 knows one decision: the picked audio treatment is applied to the whole
source and muxed back under the untouched video stream.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from . import binaries, cache, media, probe, tools
from .project import Project, ProjectError


def render(project: Project, out: Path | None = None) -> Path:
    project.check_source()
    decision = project.decision("audio")
    if decision.status != "picked":
        raise ProjectError(f"audio is {decision.status}; pick a candidate in the review page first")
    candidate = next((c for c in project.candidates("audio") if c.id == decision.pick), None)
    if candidate is None:
        raise ProjectError(f"audio is picked as {decision.pick}, but that candidate is missing from {project.root / 'candidates' / 'audio'}")
    tool = tools.get_tool(candidate.tool)

    out = out or cache.render_path(project.root, f"{project.source.stem}-final.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="talk-studio-render-") as staging:
        audio = tool.apply(project.source, candidate.settings, Path(staging) / "audio.wav")
        binaries.run([
            binaries.ffmpeg(), "-y", "-v", "error",
            "-i", str(project.source), "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(out),
        ])

    fps = probe.probe(project.source).fps
    tolerance = max(1 / fps, media.AUDIO_TOLERANCE) if fps else media.AUDIO_TOLERANCE
    # Validate each output stream against the MATCHING source stream, not a
    # single project-wide duration: a phone source's own audio and video
    # streams routinely differ by tens to hundreds of ms, and the render must
    # only be held to not introducing further drift, not to fixing the source.
    src = media.stream_durations(project.source)
    for kind in ("video", "audio"):
        if kind not in src:
            raise ProjectError(f"{project.source} has no {kind} stream")
        media.validate(out, expected=src[kind], tolerance=tolerance, streams=(kind,))
    return out
