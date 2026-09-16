import shutil

import pytest

from talk_studio import final, media, sampling, tools
from talk_studio.project import Project, ProjectError
from talk_studio.timecode import Excerpt

from conftest import make_media


@pytest.fixture
def project(tiny_video, tmp_path):
    video = tmp_path / "talk.mp4"
    shutil.copyfile(tiny_video, video)
    project = Project.init(video, tmp_path / "proj")
    project.add_excerpt("audio", Excerpt(1.0, 3.0))
    return project


@pytest.fixture
def drifted_project(tmp_path):
    """A source whose audio track runs 0.3s longer than its video track."""
    video = tmp_path / "drifted.mp4"
    make_media(video, seconds=6.0, audio_seconds=6.3)
    project = Project.init(video, tmp_path / "proj")
    project.add_excerpt("audio", Excerpt(1.0, 3.0))
    return project


def test_render_needs_a_pick(project):
    with pytest.raises(ProjectError, match="audio is open"):
        final.render(project)


def test_render_muxes_the_picked_audio(project, tmp_path):
    chain = tools.get_tool("ffmpeg-chain")
    project.propose("audio", chain.name, chain.version, chain.settings({"denoise_db": 20}), "test")
    sampling.render_round(project, "audio")
    project.record("audio", "pick", label="A")

    out = final.render(project, tmp_path / "final.mp4")

    durations = media.stream_durations(out)
    assert set(durations) == {"video", "audio"}
    assert durations["video"] == pytest.approx(6.0, abs=0.1)


def test_render_rejects_missing_picked_candidate(project):
    chain = tools.get_tool("ffmpeg-chain")
    project.propose("audio", chain.name, chain.version, chain.settings({"denoise_db": 20}), "test")
    sampling.render_round(project, "audio")
    project.record("audio", "pick", label="A")

    # Delete the picked candidate's file
    decision = project.decision("audio")
    picked_id = decision.pick
    candidates_dir = project.root / "candidates" / "audio"
    for candidate_file in candidates_dir.glob("*.toml"):
        candidate_file.unlink()

    with pytest.raises(ProjectError, match=f"audio is picked as {picked_id}"):
        final.render(project)


def test_render_refuses_a_stale_pick(project, tmp_path):
    """A source changed after the pick must block the final render.

    This behaviour exists only by the ordering of two lines in final.render:
    project.check_source() (which flips a picked decision to "stale" when the
    source no longer matches) must run before the decision.status check.
    """
    chain = tools.get_tool("ffmpeg-chain")
    project.propose("audio", chain.name, chain.version, chain.settings({"denoise_db": 20}), "test")
    sampling.render_round(project, "audio")
    project.record("audio", "pick", label="A")

    data = bytearray(project.source.read_bytes())
    data[len(data) // 2] ^= 0xFF
    project.source.write_bytes(bytes(data))

    with pytest.raises(ProjectError, match="stale"):
        final.render(project, tmp_path / "final.mp4")


def test_render_accepts_a_source_whose_streams_disagree(drifted_project, tmp_path):
    """A phone recording whose audio and video lengths differ must still render.

    The output video is a bit-exact copy of the source video, so the output's
    own audio and video streams disagree by the same amount the source does —
    that is not drift the render introduced, and must not fail validation.
    """
    project = drifted_project
    chain = tools.get_tool("ffmpeg-chain")
    project.propose("audio", chain.name, chain.version, chain.settings({"denoise_db": 20}), "test")
    sampling.render_round(project, "audio")
    project.record("audio", "pick", label="A")

    out = final.render(project, tmp_path / "final.mp4")

    src = media.stream_durations(project.source)
    out_durations = media.stream_durations(out)
    assert out_durations["video"] == pytest.approx(src["video"], abs=0.05)
    assert out_durations["audio"] == pytest.approx(src["audio"], abs=0.05)


def pick_audio(project):
    chain = tools.get_tool("ffmpeg-chain")
    project.propose("audio", chain.name, chain.version, chain.settings({"denoise_db": 20}), "test")
    sampling.render_round(project, "audio")
    project.record("audio", "pick", label="A")


def test_the_final_audio_is_at_publishing_loudness(project, tmp_path):
    pick_audio(project)
    out = final.render(project, tmp_path / "final.mp4")
    loudness, peak = media.measure(out)
    assert loudness == pytest.approx(media.PUBLISH_LUFS, abs=0.6)
    assert peak <= media.PUBLISH_TRUE_PEAK + 0.3  # AAC encoding adds a little


def test_render_applies_the_picked_mastering(project, tmp_path, monkeypatch):
    pick_audio(project)
    master = tools.get_tool("voice-master")
    project.add_excerpt("master", Excerpt(1.0, 3.0))
    project.propose("master", master.name, master.version, master.settings({}), "test")
    assert sampling.render_round(project, "master").rendered == ["c1"]
    project.record("master", "pick", label="A")

    applied = []
    original_apply = type(master).apply
    monkeypatch.setattr(type(master), "apply", lambda self, *a, **k: applied.append(a[1]) or original_apply(self, *a, **k))
    out = final.render(project, tmp_path / "final.mp4")

    assert applied == [master.settings({})]
    assert media.measure(out)[0] == pytest.approx(media.PUBLISH_LUFS, abs=0.6)


def test_render_refuses_an_unpicked_mastering_round(project):
    pick_audio(project)
    master = tools.get_tool("voice-master")
    project.propose("master", master.name, master.version, master.settings({}), "test")
    with pytest.raises(ProjectError, match="master is open"):
        final.render(project)
