"""The final render, from picks only.

The picked audio treatment runs on the whole source; the picked mastering, if
the project has one, shapes it at the working level; the result is brought to
publishing loudness and muxed under the video: copied untouched, or
re-encoded through the picked grade.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from . import binaries, cache, media, probe, sampling
from . import tools
from .project import Project, ProjectError

# A picked grade means re-encoding the video. NVENC when there is a GPU;
# libx264 otherwise, slower but everywhere.
GPU_ENCODER = ["-c:v", "hevc_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "20", "-b:v", "0", "-tag:v", "hvc1"]
CPU_ENCODER = ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]


def render(project: Project, out: Path | None = None) -> Path:
    project.check_source()
    project.picked("audio")
    for name in ("master", "grade"):
        decision = project.decision(name)
        if decision.status != "picked" and project.candidates(name):
            raise ProjectError(f"{name} is {decision.status}; pick a candidate in the review page first")
    master = project.decision("master")
    grade = None
    if project.decision("grade").status == "picked":
        candidate = project.picked("grade")
        grade = tools.get_tool(candidate.tool).filters(project.source, candidate.settings)

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
        head = [binaries.ffmpeg(), "-y", "-v", "error", "-i", str(project.source), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0"]
        tail = ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
        if grade is None:
            binaries.run(head + ["-c:v", "copy"] + tail)
        else:
            chain = f"{grade},scale=out_range=tv:out_color_matrix=bt709,format=yuv420p"
            colour = ["-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
            try:
                binaries.run(head + ["-vf", chain] + GPU_ENCODER + colour + tail)
            except binaries.BinaryError:
                binaries.run(head + ["-vf", chain] + CPU_ENCODER + colour + tail)

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
