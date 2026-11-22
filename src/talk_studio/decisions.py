"""What each kind of decision is.

One record per kind, and one lookup. Every other module asks `kind_for()`
instead of testing a decision's name, so a new kind is one entry here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import excerpts

if TYPE_CHECKING:
    from .project import Project
    from .timecode import Excerpt
    from .tools import Tool


@dataclass(frozen=True)
class Kind:
    name: str
    # The `Tool.kind` a candidate for this decision must have.
    tool_kind: str
    # What the review page shows: audio | image | video.
    media: str
    # The tool that renders what key 0 plays, and what the page calls it.
    reference: Callable[[], Tool]
    reference_label: str
    # Where `excerpts suggest` puts the excerpts, given the excerpt length asked for.
    suggest: Callable[[Project, float], list[Excerpt]]
    # A decision judged on the output of another: its samples are cut from the
    # upstream pick, so changing that pick invalidates it.
    upstream: str | None = None
    # Cut from the finished talk, with its word timings, not from the source.
    from_final: bool = False
    # Set when the kind has one decision per passage, named <prefix><slug>.
    prefix: str | None = None


def _original() -> Tool:
    from .tools import Original

    return Original()


def _original_frame() -> Tool:
    from .tools import OriginalFrame

    return OriginalFrame()


def _clip_original() -> Tool:
    from .tools.clips import ClipOriginal

    return ClipOriginal()


def _by_level(project: Project, length: float) -> list[Excerpt]:
    return excerpts.suggest(excerpts.frame_levels(project.source), length=length)


def _stills(project: Project, length: float) -> list[Excerpt]:
    return excerpts.frames(project.duration)


# The fixed kinds are listed in the order the final render applies them.
KINDS = (
    Kind("audio", tool_kind="audio", media="audio", reference=_original, reference_label="Original", suggest=_by_level),
    Kind(
        "master", tool_kind="master", media="audio", reference=_original, reference_label="Before mastering",
        suggest=_by_level, upstream="audio",
    ),
    Kind("grade", tool_kind="grade", media="image", reference=_original_frame, reference_label="Original", suggest=_stills),
    Kind(
        "clip", tool_kind="clip", media="video", reference=_clip_original, reference_label="Wide, no captions",
        suggest=_by_level, from_final=True, prefix="clip-",
    ),
)
CLIP = KINDS[-1]


def find(decision: str) -> Kind | None:
    for kind in KINDS:
        if decision.startswith(kind.prefix) if kind.prefix else decision == kind.name:
            return kind
    return None


def kind_for(decision: str) -> Kind:
    """The kind of a decision by its name: `clip-promise-big` is a clip."""
    kind = find(decision)
    if kind is None:
        from .project import ProjectError

        raise ProjectError(f"{decision!r} is not a kind of decision")
    return kind


def fixed() -> list[str]:
    """The decisions every project has, in render order."""
    return [kind.name for kind in KINDS if kind.prefix is None]


def downstream(decision: str) -> list[str]:
    """The fixed decisions judged on this one's pick."""
    return [kind.name for kind in KINDS if kind.upstream == decision]


def preconditions(project: Project, decision: str) -> None:
    """Raise ProjectError unless what the decision is judged on exists: its upstream pick, the final video."""
    kind = kind_for(decision)
    if kind.upstream:
        project.picked(kind.upstream)
    if kind.from_final:
        from . import sampling

        sampling.clip_inputs(project)
