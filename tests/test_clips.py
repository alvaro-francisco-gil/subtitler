import json
import shutil

import numpy as np
import pytest

from talk_studio import cache, cli, media, sampling, tools
from talk_studio.project import Project, ProjectError
from talk_studio.timecode import Excerpt
from talk_studio.tools.clips import BlurLetterbox, FollowSpeaker, SlideAndSpeaker, crop_x, screen_box, smooth_track


def test_smooth_track_holds_the_last_good_position_and_ignores_a_jump():
    raw = [0.8, 0.8, None, None, 0.2, 0.8, 0.8, 0.8]
    smoothed = smooth_track(raw)
    assert all(abs(x - 0.8) < 0.15 for x in smoothed)


def test_smooth_track_without_any_detection_centres():
    assert smooth_track([None, None, None]) == [0.5, 0.5, 0.5]


def test_crop_x_interpolates_per_second_and_clamps_to_the_frame():
    expression = crop_x((0.5, 0.75), 1920, 608)
    assert expression.startswith("clip(656.0+(480.0)*clip(t-0,0,1),0,1312)")
    assert crop_x((0.5, 0.5), 1920, 608) == "clip(656.0,0,1312)"


def test_screen_box_finds_the_bright_projection():
    background = np.full((270, 480), 40.0)
    background[30:240, 120:420] = 220.0
    left, top, right, bottom = screen_box(background)
    assert (round(left, 2), round(top, 2), round(right, 2), round(bottom, 2)) == (0.25, 0.11, 0.88, 0.89)


@pytest.fixture
def words(tmp_path):
    path = tmp_path / "words.json"
    path.write_text(json.dumps([
        {"text": text, "start": 1.0 + i * 0.3, "end": 1.25 + i * 0.3, "score": 1.0}
        for i, text in enumerate("programming is no longer the job".split())
    ]))
    return path


@pytest.mark.parametrize("tool", [FollowSpeaker(), SlideAndSpeaker(), BlurLetterbox()], ids=lambda t: t.name)
def test_every_layout_renders_a_vertical_clip_with_sound(tool, tiny_video, words, tmp_path):
    out = tool.sample(tiny_video, Excerpt(1.0, 3.0), tool.settings({}), tmp_path / "clip.mp4", words=words)
    from talk_studio import probe
    info = probe.probe(out)
    assert (info.display_width, info.display_height) == (1080, 1920)
    durations = media.stream_durations(out)
    assert durations["video"] == pytest.approx(2.0, abs=0.1) and "audio" in durations


@pytest.fixture
def clip_project(tiny_video, words, tmp_path):
    video = tmp_path / "talk.mp4"
    shutil.copyfile(tiny_video, video)
    project = Project.init(video, tmp_path / "proj")
    project.words = words
    project.add_clip("no-longer-the-job", Excerpt(1.0, 3.0))
    return project


def test_clip_decisions_round_trip_with_their_words(clip_project):
    loaded = Project.load(clip_project.root)
    assert loaded.decision("clip-no-longer-the-job").excerpts == [Excerpt(1.0, 3.0)]
    assert loaded.words == clip_project.words
    with pytest.raises(ProjectError, match="lowercase"):
        loaded.add_clip("Bad Name", Excerpt(1.0, 2.0))


def test_clips_are_cut_from_the_final_render(clip_project):
    name = "clip-no-longer-the-job"
    letterbox = tools.get_tool("blur-letterbox")
    clip_project.propose(name, letterbox.name, letterbox.version, letterbox.settings({}), "test")
    with pytest.raises(ProjectError, match="run `talk-studio render` first"):
        sampling.render_round(clip_project, name)

    final = sampling.final_video(clip_project)
    final.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(clip_project.source, final)
    report = sampling.render_round(clip_project, name)
    assert report.rendered == ["c1"]
    clip_project.record(name, "pick", label="A")

    out = clip_project.root.parent / "shorts"
    assert cli.main(["clips", "export", "--out", str(out), "--project", str(clip_project.root)]) == 0
    assert (out / "no-longer-the-job.mp4").exists()


def test_propose_accepts_clip_tools_for_clip_decisions(clip_project, capsys):
    root = str(clip_project.root)
    assert cli.main(["propose", "clip-no-longer-the-job", "--tool", "follow-speaker", "--project", root]) == 0
    assert cli.main(["propose", "clip-no-longer-the-job", "--tool", "ffmpeg-eq", "--project", root]) == 1
    assert "tools for clip-no-longer-the-job" in capsys.readouterr().err
