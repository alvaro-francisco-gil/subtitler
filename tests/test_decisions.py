import pytest

from talk_studio import decisions, tools
from talk_studio.project import ProjectError
from talk_studio.tools.clips import ClipOriginal


@pytest.mark.parametrize(
    "decision, name, tool_kind, media, reference, label, upstream",
    [
        ("audio", "audio", "audio", "audio", tools.Original, "Original", None),
        ("master", "master", "master", "audio", tools.Original, "Before mastering", "audio"),
        ("grade", "grade", "grade", "image", tools.OriginalFrame, "Original", None),
        ("clip-promise-big", "clip", "clip", "video", ClipOriginal, "Wide, no captions", None),
    ],
)
def test_each_kind(decision, name, tool_kind, media, reference, label, upstream):
    kind = decisions.kind_for(decision)
    assert kind.name == name
    assert kind.tool_kind == tool_kind
    assert kind.media == media
    assert type(kind.reference()) is reference
    assert kind.reference_label == label
    assert kind.upstream == upstream


def test_only_clips_are_cut_from_the_final_video():
    assert [kind.name for kind in decisions.KINDS if kind.from_final] == ["clip"]


def test_fixed_decisions_are_in_render_order():
    assert decisions.fixed() == ["audio", "master", "grade"]


def test_downstream_is_the_reverse_of_upstream():
    assert decisions.downstream("audio") == ["master"]
    assert decisions.downstream("master") == []


@pytest.mark.parametrize("name", ["captions", "clip", "clips-x", "Audio", ""])
def test_unknown_names_have_no_kind(name):
    assert decisions.find(name) is None
    with pytest.raises(ProjectError):
        decisions.kind_for(name)


def test_every_tool_serves_a_kind():
    kinds = {kind.tool_kind for kind in decisions.KINDS}
    assert {tool.kind for tool in tools.all_tools()} <= kinds
