"""The review page's API.

Blind by construction: an open round is described by labels only. Ids, tools
and settings appear once the round has a verdict.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import cache, sampling, tools
from ..project import UPSTREAM, Candidate, Project, ProjectError
from ..timecode import Excerpt

STATIC = Path(__file__).parent / "static"
# What key 0 plays: the untouched source, or the upstream pick before this step.
REFERENCE = {"master": "Before mastering"}
MAX_LOG = 4000


class FeedbackIn(BaseModel):
    verdict: str
    label: str | None = None
    note: str = ""


class ExcerptIn(BaseModel):
    start: float
    end: float


def create_app(project_root: Path) -> FastAPI:
    app = FastAPI(title="talk-studio")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def load() -> Project:
        return Project.load(project_root)

    def require(project: Project, name: str):
        try:
            return project.decision(name)
        except ProjectError as error:
            raise HTTPException(404, str(error)) from None

    def view(project: Project, candidate: Candidate, *, revealed: bool) -> dict:
        entry = {"label": project.label(candidate), "state": candidate.state, "error": None}
        if candidate.state == "failed":
            if revealed and candidate.error and Path(candidate.error).exists():
                entry["error"] = Path(candidate.error).read_text()[:MAX_LOG]
            elif not revealed:
                # An open round is blind: say it failed, not why or on what.
                entry["error"] = "This candidate failed to render."
        if revealed:
            entry |= {"id": candidate.id, "tool": candidate.tool, "settings": candidate.settings, "proposer": candidate.proposer}
        return entry

    def excerpt_list(project: Project, name: str) -> list[dict]:
        return [{"start": e.start, "end": e.end, "label": str(e)} for e in project.decision(name).excerpts]

    def sample_path(project: Project, name: str, label: str, index: int) -> Path:
        excerpts = project.decision(name).excerpts
        if not 0 <= index < len(excerpts):
            raise HTTPException(404, "no such excerpt")
        if name in UPSTREAM:
            try:
                project.picked(UPSTREAM[name])
            except ProjectError as error:
                raise HTTPException(404, str(error)) from None
        if label == "original":
            return sampling.sample_file(project, name, tools.Original(), {}, excerpts[index])
        round_ = project.open_round(name)
        if round_ is None:
            raise HTTPException(404, "no open round")
        try:
            candidate = project.by_label(name, round_, label)
            tool = tools.get_tool(candidate.tool)
        except (ProjectError, tools.ToolError) as error:
            raise HTTPException(404, str(error)) from None
        return sampling.sample_file(project, name, tool, candidate.settings, excerpts[index])

    def rerender(name: str) -> None:
        # A background task has nobody to raise to. sampling.render_round
        # already marks a per-candidate failure through Project (state
        # "failed", with a log) and keeps going; this catches only a failure
        # in render_round itself — e.g. the shared "original" sample, or a
        # stale source — which would otherwise leave every candidate stuck
        # showing "rendering…" forever with no way for the page to know why.
        try:
            sampling.render_round(load(), name)
        except Exception as error:
            project = load()
            round_ = project.open_round(name)
            if round_ is None:
                return
            for candidate in project.round_candidates(name, round_):
                if candidate.state != "pending":
                    continue
                log = cache.log_path(project.root, name, candidate.id)
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(f"{type(error).__name__}: {error}\n")
                candidate.state, candidate.error = "failed", str(log)
                project.save_candidate(candidate)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/project")
    def project_summary():
        project = load()
        return {
            "source": project.source.name,
            "duration": project.duration,
            "decisions": [
                {"name": d.name, "status": d.status, "open_round": project.open_round(d.name)}
                for d in project.decisions.values()
            ],
        }

    @app.get("/api/decisions/{name}")
    def decision_view(name: str):
        project = load()
        decision = require(project, name)
        round_ = project.open_round(name)
        verdicts = project.feedback(name)
        closed = sorted({v.round for v in verdicts})
        members = project.round_candidates(name, round_) if round_ else []

        blocked = None
        if name in UPSTREAM:
            try:
                project.picked(UPSTREAM[name])
            except ProjectError as error:
                blocked = str(error)

        loudness = {}
        if round_ and not blocked:
            count = len(decision.excerpts)
            for label in ["original", *[project.label(c) for c in members]]:
                loudness[label] = [sampling.loudness(sample_path(project, name, label, i)) for i in range(count)]

        return {
            "name": name,
            "status": decision.status,
            "reference": REFERENCE.get(name, "Original"),
            "blocked": blocked,
            "excerpts": excerpt_list(project, name),
            "open_round": round_,
            "candidates": [view(project, c, revealed=False) for c in members],
            "loudness": loudness,
            "history": [
                {
                    "round": r,
                    "verdicts": [asdict(v) for v in verdicts if v.round == r],
                    "candidates": [view(project, c, revealed=True) for c in project.round_candidates(name, r)],
                }
                for r in closed
            ],
        }

    @app.get("/api/decisions/{name}/samples/{label}/{index}.wav")
    def sample(name: str, label: str, index: int):
        project = load()
        require(project, name)
        path = sample_path(project, name, label, index)
        if not path.exists():
            raise HTTPException(404, "sample not rendered yet")
        return FileResponse(path, media_type="audio/wav")

    @app.post("/api/decisions/{name}/feedback")
    def feedback(name: str, body: FeedbackIn):
        project = load()
        require(project, name)
        round_ = project.open_round(name)
        try:
            verdict = project.record(name, body.verdict, label=body.label, note=body.note)
        except ProjectError as error:
            raise HTTPException(400, str(error)) from None
        return {
            "verdict": asdict(verdict),
            "revealed": [view(project, c, revealed=True) for c in project.round_candidates(name, round_)],
        }

    @app.post("/api/decisions/{name}/excerpts")
    def add_excerpt(name: str, body: ExcerptIn, background: BackgroundTasks):
        project = load()
        require(project, name)
        try:
            project.add_excerpt(name, Excerpt(round(body.start, 1), round(body.end, 1)))
        except ProjectError as error:
            raise HTTPException(400, str(error)) from None
        if project.open_round(name) is not None:
            background.add_task(rerender, name)
        return {"excerpts": excerpt_list(project, name)}

    return app
