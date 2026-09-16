"""A project on disk: `project.toml`, `candidates/`, `feedback/`.

Everything a human or an agent decides is written here as text, so a project
can be committed beside the material it describes. Renders never live here —
see `cache.py`. The CLI and the review server both go through this module, and
every rewrite of `project.toml` is atomic.
"""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import tomli_w

from . import media
from .timecode import Excerpt, parse_excerpt

PROJECT_FILE = "project.toml"
DECISIONS = ("audio",)
LABELS = "ABCDEFGHI"


class ProjectError(Exception):
    """The project is missing, or a request does not fit its current state."""


@dataclass
class Decision:
    name: str
    status: str = "open"  # open | picked | stale
    excerpts: list[Excerpt] = field(default_factory=list)
    pick: str | None = None


@dataclass
class Candidate:
    id: str
    decision: str
    round: int
    tool: str
    tool_version: str
    settings: dict
    proposer: str
    created: str
    state: str = "pending"  # pending | rendered | failed
    error: str | None = None  # path to the failure log


@dataclass
class Verdict:
    round: int
    verdict: str  # pick | reject
    candidate: str | None
    note: str
    at: str


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.replace(tmp, path)


@dataclass
class Project:
    root: Path
    source: Path
    fingerprint: str
    duration: float
    decisions: dict[str, Decision]

    @classmethod
    def init(cls, video: Path, root: Path) -> Project:
        root = root.resolve()
        if (root / PROJECT_FILE).exists():
            raise ProjectError(f"{root / PROJECT_FILE} already exists")
        video = video.resolve()
        if not video.exists():
            raise ProjectError(f"source {video} not found")
        project = cls(
            root=root,
            source=video,
            fingerprint=media.fingerprint(video),
            duration=media.duration(video),
            decisions={name: Decision(name) for name in DECISIONS},
        )
        project.save()
        return project

    @classmethod
    def load(cls, root: Path) -> Project:
        root = Path(root).resolve()
        path = root / PROJECT_FILE
        if not path.exists():
            raise ProjectError(f"no {PROJECT_FILE} in {root}; run `talk-studio init` first")
        data = tomllib.loads(path.read_text())
        decisions = {
            name: Decision(
                name=name,
                status=raw.get("status", "open"),
                excerpts=[parse_excerpt(text) for text in raw.get("excerpts", [])],
                pick=raw.get("pick") or None,
            )
            for name, raw in data.get("decisions", {}).items()
        }
        source = data["source"]
        return cls(
            root=root,
            source=Path(source["path"]),
            fingerprint=source["fingerprint"],
            duration=float(source["duration"]),
            decisions=decisions,
        )

    def save(self) -> None:
        decisions = {}
        for decision in self.decisions.values():
            entry = {"status": decision.status, "excerpts": [str(e) for e in decision.excerpts]}
            if decision.pick:
                entry["pick"] = decision.pick
            decisions[decision.name] = entry
        data = {
            "source": {"path": str(self.source), "fingerprint": self.fingerprint, "duration": self.duration},
            "decisions": decisions,
        }
        atomic_write(self.root / PROJECT_FILE, tomli_w.dumps(data))

    def decision(self, name: str) -> Decision:
        try:
            return self.decisions[name]
        except KeyError:
            raise ProjectError(
                f"unknown decision {name!r}; this project has {', '.join(self.decisions)}"
            ) from None

    # source

    def check_source(self) -> bool:
        """True when the source still matches. Otherwise picks go stale and the new fingerprint is kept."""
        if not self.source.exists():
            raise ProjectError(f"source {self.source} not found")
        current = media.fingerprint(self.source)
        if current == self.fingerprint:
            return True
        for decision in self.decisions.values():
            if decision.status == "picked":
                decision.status = "stale"
        self.fingerprint = current
        self.duration = media.duration(self.source)
        self.save()
        return False

    # excerpts

    def _checked(self, excerpt: Excerpt) -> Excerpt:
        if excerpt.start < 0 or excerpt.end > self.duration + media.AUDIO_TOLERANCE:
            raise ProjectError(f"excerpt {excerpt} is outside the {self.duration:.1f}s source")
        if excerpt.end <= excerpt.start:
            raise ProjectError(f"excerpt {excerpt} must end after it starts")
        return excerpt

    def add_excerpt(self, name: str, excerpt: Excerpt) -> None:
        decision = self.decision(name)
        if self._checked(excerpt) not in decision.excerpts:
            decision.excerpts.append(excerpt)
            self.save()

    def set_excerpts(self, name: str, excerpts: list[Excerpt]) -> None:
        self.decision(name).excerpts = [self._checked(e) for e in excerpts]
        self.save()

    # candidates and rounds

    def _candidates_dir(self, name: str) -> Path:
        return self.root / "candidates" / name

    def candidates(self, name: str) -> list[Candidate]:
        self.decision(name)
        folder = self._candidates_dir(name)
        if not folder.exists():
            return []
        items = [Candidate(**tomllib.loads(path.read_text())) for path in folder.glob("*.toml")]
        return sorted(items, key=lambda c: int(c.id[1:]))

    def save_candidate(self, candidate: Candidate) -> None:
        data = {key: value for key, value in asdict(candidate).items() if value is not None}
        atomic_write(self._candidates_dir(candidate.decision) / f"{candidate.id}.toml", tomli_w.dumps(data))

    def open_round(self, name: str) -> int | None:
        """The latest round that has candidates and no verdict yet."""
        rounds = {c.round for c in self.candidates(name)}
        closed = {v.round for v in self.feedback(name)}
        pending = sorted(rounds - closed)
        return pending[-1] if pending else None

    def current_round(self, name: str) -> int:
        """The round a new proposal joins."""
        open_round = self.open_round(name)
        if open_round is not None:
            return open_round
        rounds = [c.round for c in self.candidates(name)]
        return max(rounds) + 1 if rounds else 1

    def round_candidates(self, name: str, round_: int) -> list[Candidate]:
        return [c for c in self.candidates(name) if c.round == round_]

    def label(self, candidate: Candidate) -> str:
        members = [c.id for c in self.round_candidates(candidate.decision, candidate.round)]
        return LABELS[members.index(candidate.id)]

    def by_label(self, name: str, round_: int, label: str) -> Candidate:
        for candidate in self.round_candidates(name, round_):
            if self.label(candidate) == label:
                return candidate
        raise ProjectError(f"round {round_} of {name} has no candidate {label}")

    def propose(self, name: str, tool: str, tool_version: str, settings: dict, proposer: str) -> Candidate:
        decision = self.decision(name)
        if decision.status == "picked":
            raise ProjectError(f"{name} is already picked ({decision.pick}); run `talk-studio reopen {name}` first")
        round_ = self.current_round(name)
        if len(self.round_candidates(name, round_)) >= len(LABELS):
            raise ProjectError(f"round {round_} of {name} already has {len(LABELS)} candidates")
        highest = max((int(c.id[1:]) for c in self.candidates(name)), default=0)
        candidate = Candidate(
            id=f"c{highest + 1}",
            decision=name,
            round=round_,
            tool=tool,
            tool_version=tool_version,
            settings=dict(settings),
            proposer=proposer,
            created=now(),
        )
        self.save_candidate(candidate)
        return candidate

    # feedback

    def _feedback_path(self, name: str) -> Path:
        return self.root / "feedback" / f"{name}.jsonl"

    def feedback(self, name: str) -> list[Verdict]:
        self.decision(name)
        path = self._feedback_path(name)
        if not path.exists():
            return []
        return [Verdict(**json.loads(line)) for line in path.read_text().splitlines() if line.strip()]

    def record(self, name: str, verdict: str, *, label: str | None = None, note: str = "") -> Verdict:
        decision = self.decision(name)
        round_ = self.open_round(name)
        if round_ is None:
            raise ProjectError(f"{name} has no open round to judge")
        if verdict == "pick":
            if label is None:
                raise ProjectError("a pick needs a candidate label")
            chosen = self.by_label(name, round_, label)
            if chosen.state != "rendered":
                raise ProjectError(f"{label} is {chosen.state}, not rendered; it cannot be picked")
            candidate_id = chosen.id
        elif verdict == "reject":
            candidate_id = None
        else:
            raise ProjectError(f"verdict must be pick or reject, not {verdict!r}")

        entry = Verdict(round=round_, verdict=verdict, candidate=candidate_id, note=note.strip(), at=now())

        # Save project state BEFORE appending the verdict: a crash here loses
        # an unrecorded verdict, which the human simply re-submits. The
        # reverse order is worse — a crash after the journal append but
        # before save() would close the round (feedback recorded) while the
        # decision stays "open" with no pick, so `final.render` refuses and
        # the next `propose` silently opens a second round on a decision that
        # was actually settled.
        if verdict == "pick":
            decision.status, decision.pick = "picked", candidate_id
            self.save()

        path = self._feedback_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")

        return entry

    def reopen(self, name: str) -> None:
        decision = self.decision(name)
        decision.status, decision.pick = "open", None
        self.save()
