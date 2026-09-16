"""The final render, from picks only.

The picked audio treatment runs on the whole source; the picked mastering, if
the project has one, shapes it at the working level; the result is brought to
publishing loudness and muxed back under the untouched video stream.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from . import binaries, cache, media, probe, sampling
from . import tools
from .project import Project, ProjectError


def render(project: Project, out: Path | None = None) -> Path:
    project.check_source()
    project.picked("audio")
    master = project.decision("master")
    if master.status != "picked" and project.candidates("master"):
        raise ProjectError(f"master is {master.status}; pick a mastering candidate in the review page first")

    out = out or cache.render_path(project.root, f"{project.source.stem}-final.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="talk-studio-render-") as staging:
        staging = Path(staging)
        if master.status == "picked":
            candidate = project.picked("master")
            shaped = tools.get_tool(candidate.tool).apply(
                sampling.working_audio(project, "master"), candidate.settings, staging / "mastered.wav",
            )
        else:
            shaped = sampling.picked_audio(project, "audio")
        audio = media.publish_loudness(shaped, staging / "published.wav")
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
