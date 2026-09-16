"""Rendering a round: the original and every candidate, on every excerpt.

One candidate failing never stops the others; its log is kept and the page
shows it.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import binaries, cache, media, tools
from .project import UPSTREAM, Project, ProjectError
from .timecode import Excerpt
from .tools.mastering import WORKING_LUFS


def _upstream_key(project: Project, name: str) -> str:
    candidate = project.picked(name)
    tool = tools.get_tool(candidate.tool)
    return cache.sample_key(
        fingerprint=project.fingerprint, tool=tool.name, version=tool.version,
        settings=candidate.settings, excerpt="full",
    )


def _build(path: Path, render) -> Path:
    """Render to a writer-unique partial file and move it into place."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(f".{os.getpid()}-{uuid.uuid4().hex[:8]}.partial{path.suffix}")
        try:
            render(partial)
            os.replace(partial, path)
        finally:
            partial.unlink(missing_ok=True)
    return path


def picked_audio(project: Project, name: str) -> Path:
    """The full-length output of a decision's pick, rendered once and cached."""
    candidate = project.picked(name)
    tool = tools.get_tool(candidate.tool)
    path = cache.render_path(project.root, f"{name}-{_upstream_key(project, name)}.wav")
    return _build(path, lambda partial: tool.apply(project.source, candidate.settings, partial))


def working_audio(project: Project, decision: str) -> Path:
    """What a downstream decision is judged on: its upstream pick, set to the working level."""
    upstream = picked_audio(project, UPSTREAM[decision])
    path = upstream.with_name(f"{upstream.stem}-working.wav")
    return _build(path, lambda partial: media.set_loudness(upstream, partial, target=WORKING_LUFS))


def source_for(project: Project, decision: str) -> Path:
    return working_audio(project, decision) if decision in UPSTREAM else project.source


def sample_file(project: Project, decision: str, tool: tools.Tool, settings: dict, excerpt: Excerpt) -> Path:
    # A downstream decision's samples depend on the upstream pick too, so a
    # different pick never serves a stale sample from the cache.
    fingerprint = project.fingerprint
    if decision in UPSTREAM:
        fingerprint = f"{fingerprint}+{_upstream_key(project, UPSTREAM[decision])}@{WORKING_LUFS:g}"
    key = cache.sample_key(
        fingerprint=fingerprint, tool=tool.name, version=tool.version,
        settings=settings, excerpt=str(excerpt),
    )
    return cache.project_dir(project.root) / "samples" / decision / f"{key}{tool.suffix}"


def ready(path: Path) -> bool:
    """A sample the page can show: an image that exists, or audio whose loudness is measured."""
    return path.exists() if path.suffix != ".wav" else loudness(path) is not None


def loudness(path: Path) -> float | None:
    meta = path.with_suffix(".json")
    if not meta.exists():
        return None
    return json.loads(meta.read_text())["lufs"]


def ensure_sample(project: Project, decision: str, tool: tools.Tool, settings: dict, excerpt: Excerpt) -> Path:
    path = sample_file(project, decision, tool, settings, excerpt)
    if not path.exists():
        source = source_for(project, decision)
        # Unique per writer: two processes racing to render the same key (the
        # server's background re-render and a concurrent `talk-studio sample`
        # are the reachable case) must never share a partial path, or each
        # can validate and replace a file the other is still writing.
        _build(path, lambda partial: tool.sample(source, excerpt, settings, partial))
    if path.suffix == ".wav" and loudness(path) is None:
        path.with_suffix(".json").write_text(json.dumps({"lufs": media.integrated_loudness(path)}))
    return path


@dataclass
class RoundReport:
    rendered: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def render_round(project: Project, decision: str) -> RoundReport:
    excerpts = project.decision(decision).excerpts
    if not excerpts:
        raise ProjectError(f"{decision} has no excerpts; run `talk-studio excerpts suggest {decision}`")
    round_ = project.open_round(decision)
    if round_ is None:
        raise ProjectError(f"{decision} has no open round; propose candidates first")
    project.check_source()
    if decision in UPSTREAM:
        project.picked(UPSTREAM[decision])

    for excerpt in excerpts:
        ensure_sample(project, decision, tools.reference_for(decision), {}, excerpt)

    report = RoundReport()
    for candidate in project.round_candidates(decision, round_):
        log = cache.log_path(project.root, decision, candidate.id)
        try:
            tool = tools.get_tool(candidate.tool)
            missing = [r for r in tool.requires() if not r.ok]
            if missing:
                raise tools.ToolError("missing " + "; ".join(f"{r.name} ({r.hint})" for r in missing))
            for excerpt in excerpts:
                ensure_sample(project, decision, tool, candidate.settings, excerpt)
        except (tools.ToolError, binaries.BinaryError, media.RenderError, OSError) as error:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(f"{type(error).__name__}: {error}\n")
            candidate.state, candidate.error = "failed", str(log)
            report.failed.append(candidate.id)
        else:
            candidate.state, candidate.error = "rendered", None
            candidate.tool_version = tool.version
            report.rendered.append(candidate.id)
        project.save_candidate(candidate)
    return report
