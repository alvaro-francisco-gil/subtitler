"""Rendering a round: the original and every candidate, on every excerpt.

One candidate failing never stops the others; its log is kept and the page
shows it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import binaries, cache, media, tools
from .project import Project, ProjectError
from .timecode import Excerpt


def sample_file(project: Project, decision: str, tool: tools.AudioTool, settings: dict, excerpt: Excerpt) -> Path:
    key = cache.sample_key(
        fingerprint=project.fingerprint, tool=tool.name, version=tool.version,
        settings=settings, excerpt=str(excerpt),
    )
    return cache.project_dir(project.root) / "samples" / decision / f"{key}.wav"


def loudness(path: Path) -> float | None:
    meta = path.with_suffix(".json")
    if not meta.exists():
        return None
    return json.loads(meta.read_text())["lufs"]


def ensure_sample(project: Project, decision: str, tool: tools.AudioTool, settings: dict, excerpt: Excerpt) -> Path:
    path = sample_file(project, decision, tool, settings, excerpt)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".partial.wav")
        tool.sample(project.source, excerpt, settings, partial)
        os.replace(partial, path)
    if loudness(path) is None:
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

    for excerpt in excerpts:
        ensure_sample(project, decision, tools.Original(), {}, excerpt)

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
