import shutil

import pytest

from talk_studio import final, media, sampling, tools
from talk_studio.project import Project, ProjectError
from talk_studio.timecode import Excerpt


@pytest.fixture
def project(tiny_video, tmp_path):
    video = tmp_path / "talk.mp4"
    shutil.copyfile(tiny_video, video)
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
