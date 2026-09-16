import shutil
import tomllib

import pytest

from talk_studio.project import LABELS, Project, ProjectError
from talk_studio.timecode import Excerpt


@pytest.fixture
def project(tiny_video, tmp_path):
    video = tmp_path / "talk.mp4"
    shutil.copyfile(tiny_video, video)
    return Project.init(video, tmp_path / "proj")


def propose(project, n=1):
    return [project.propose("audio", "ffmpeg-chain", "1", {"denoise_db": 10.0 + i}, "test") for i in range(n)]


def test_init_writes_project_toml(project):
    data = tomllib.loads((project.root / "project.toml").read_text())
    assert data["source"]["path"] == str(project.source)
    assert data["source"]["fingerprint"] == project.fingerprint
    assert data["decisions"]["audio"] == {"status": "open", "excerpts": []}


def test_init_refuses_to_overwrite(project):
    with pytest.raises(ProjectError, match="already exists"):
        Project.init(project.source, project.root)


def test_load_round_trips(project):
    project.add_excerpt("audio", Excerpt(1.0, 3.0))
    loaded = Project.load(project.root)
    assert loaded.decision("audio").excerpts == [Excerpt(1.0, 3.0)]
    assert loaded.duration == pytest.approx(project.duration)


def test_load_without_project_file(tmp_path):
    with pytest.raises(ProjectError, match="talk-studio init"):
        Project.load(tmp_path)


def test_unknown_decision(project):
    with pytest.raises(ProjectError, match="unknown decision"):
        project.decision("grade")


def test_excerpts_are_validated_and_deduplicated(project):
    project.add_excerpt("audio", Excerpt(1.0, 3.0))
    project.add_excerpt("audio", Excerpt(1.0, 3.0))
    assert len(project.decision("audio").excerpts) == 1
    with pytest.raises(ProjectError, match="outside"):
        project.add_excerpt("audio", Excerpt(5.0, 60.0))


def test_proposals_share_a_round_and_get_labels(project):
    first, second = propose(project, 2)
    assert (first.id, second.id) == ("c1", "c2")
    assert first.round == second.round == 1
    assert [project.label(c) for c in (first, second)] == ["A", "B"]
    assert project.by_label("audio", 1, "B").id == "c2"


def test_a_reject_closes_the_round(project):
    propose(project, 2)
    verdict = project.record("audio", "reject", note="  too metallic ")
    assert verdict.note == "too metallic"
    assert project.open_round("audio") is None
    (third,) = propose(project)
    assert (third.id, third.round, project.label(third)) == ("c3", 2, "A")


def test_a_pick_needs_a_rendered_candidate(project):
    propose(project, 1)
    with pytest.raises(ProjectError, match="not rendered"):
        project.record("audio", "pick", label="A")


def test_a_pick_closes_the_decision(project):
    (candidate,) = propose(project, 1)
    candidate.state = "rendered"
    project.save_candidate(candidate)
    project.record("audio", "pick", label="A", note="clean")
    loaded = Project.load(project.root)
    assert (loaded.decision("audio").status, loaded.decision("audio").pick) == ("picked", "c1")
    assert [v.verdict for v in loaded.feedback("audio")] == ["pick"]
    with pytest.raises(ProjectError, match="already picked"):
        propose(loaded)


def test_reopen_allows_new_rounds(project):
    (candidate,) = propose(project, 1)
    candidate.state = "rendered"
    project.save_candidate(candidate)
    project.record("audio", "pick", label="A")
    project.reopen("audio")
    (again,) = propose(project)
    assert again.round == 2


def test_record_without_open_round(project):
    with pytest.raises(ProjectError, match="no open round"):
        project.record("audio", "reject")


def test_round_size_is_capped(project):
    propose(project, len(LABELS))
    with pytest.raises(ProjectError, match="already has 9"):
        propose(project)


def test_a_changed_source_makes_picks_stale(project):
    (candidate,) = propose(project, 1)
    candidate.state = "rendered"
    project.save_candidate(candidate)
    project.record("audio", "pick", label="A")
    old = project.fingerprint

    data = bytearray(project.source.read_bytes())
    data[len(data) // 2] ^= 0xFF
    project.source.write_bytes(bytes(data))

    assert project.check_source() is False
    loaded = Project.load(project.root)
    assert loaded.decision("audio").status == "stale"
    assert loaded.fingerprint != old
    assert loaded.check_source() is True


def test_a_missing_source_is_an_error(project):
    project.source.unlink()
    with pytest.raises(ProjectError, match="not found"):
        project.check_source()
