import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from talk_studio import media
from talk_studio.timecode import Excerpt
from talk_studio.tools.deepfilternet import UVX_ARGS, DeepFilterNet


def test_command_is_pinned():
    tool = DeepFilterNet()
    command = tool.command([Path("/in/a.wav")], Path("/out"), tool.settings({"atten_lim_db": "18", "post_filter": "true"}))
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


def write_wav(path: Path, frames: np.ndarray) -> Path:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(frames.shape[1])
        out.setsampwidth(2)
        out.setframerate(48000)
        out.writeframes(frames.astype("<i2").tobytes())
    return path


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as source:
        raw = source.readframes(source.getnframes())
        return np.frombuffer(raw, dtype="<i2").reshape(-1, source.getnchannels())


class FakeModel:
    """Stands in for deepFilter: scales every file by a gain and records each call."""

    def __init__(self, gain: float = 1.0):
        self.gain, self.calls = gain, []

    def __call__(self, sources, out_dir, settings):
        self.calls.append(list(sources))
        for source in sources:
            write_wav(out_dir / source.name, np.round(read_wav(source) * self.gain))


@pytest.fixture
def small_chunks(monkeypatch):
    monkeypatch.setattr("talk_studio.tools.deepfilternet.CHUNK_SECONDS", 1.0)
    monkeypatch.setattr("talk_studio.tools.deepfilternet.OVERLAP_SECONDS", 0.2)
    monkeypatch.setattr("talk_studio.tools.deepfilternet.FADE_SECONDS", 0.1)


def noise(seconds: float) -> np.ndarray:
    return np.random.default_rng(7).integers(-8000, 8000, size=(int(seconds * 48000), 2))


def test_long_input_is_denoised_in_overlapping_chunks_in_one_call(small_chunks, monkeypatch, tmp_path):
    model = FakeModel()
    monkeypatch.setattr(DeepFilterNet, "denoise", model)
    tool = DeepFilterNet()
    source = write_wav(tmp_path / "long.wav", noise(3.5))

    tool.process(source, tmp_path / "out.wav", tool.settings({}))

    assert len(model.calls) == 1 and len(model.calls[0]) == 3
    assert np.array_equal(read_wav(tmp_path / "out.wav"), read_wav(source))


def test_seams_blend_to_the_model_output(small_chunks, monkeypatch, tmp_path):
    monkeypatch.setattr(DeepFilterNet, "denoise", FakeModel(gain=0.5))
    tool = DeepFilterNet()
    source = write_wav(tmp_path / "long.wav", noise(2.4))

    tool.process(source, tmp_path / "out.wav", tool.settings({}))

    expected = np.round(read_wav(source) * 0.5)
    assert np.abs(read_wav(tmp_path / "out.wav") - expected).max() <= 1


def test_short_input_is_one_chunk(small_chunks, monkeypatch, tmp_path):
    model = FakeModel()
    monkeypatch.setattr(DeepFilterNet, "denoise", model)
    tool = DeepFilterNet()
    source = write_wav(tmp_path / "short.wav", noise(1.3))

    tool.process(source, tmp_path / "out.wav", tool.settings({}))

    assert len(model.calls[0]) == 1
    assert np.array_equal(read_wav(tmp_path / "out.wav"), read_wav(source))
