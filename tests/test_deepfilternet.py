from pathlib import Path

import pytest

from talk_studio import media
from talk_studio.timecode import Excerpt
from talk_studio.tools.deepfilternet import UVX_ARGS, DeepFilterNet


def test_command_is_pinned():
    tool = DeepFilterNet()
    command = tool.command(Path("/in/a.wav"), Path("/out"), tool.settings({"atten_lim_db": "18", "post_filter": "true"}))
    assert command == [
        "uvx", *UVX_ARGS, "deepFilter", "--no-suffix", "--atten-lim", "18", "-o", "/out", "--pf", "/in/a.wav",
    ]
    assert "deepfilternet==0.5.6" in UVX_ARGS
    assert "torchaudio==2.0.2" in UVX_ARGS


def test_requires_uvx(monkeypatch):
    monkeypatch.setattr("talk_studio.tools.deepfilternet.shutil.which", lambda name: None)
    (requirement,) = DeepFilterNet().requires()
    assert (requirement.name, requirement.ok) == ("uvx", False)


@pytest.mark.slow
def test_denoises_a_sample(tiny_video, tmp_path):
    tool = DeepFilterNet()
    out = tool.sample(tiny_video, Excerpt(2.0, 4.0), tool.settings({}), tmp_path / "dfn.wav")
    assert media.stream_durations(out)["audio"] == pytest.approx(2.0, abs=media.AUDIO_TOLERANCE)
