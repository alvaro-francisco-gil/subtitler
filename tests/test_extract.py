from pathlib import Path

import pytest

from subtitler import extract, probe

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_builds_16k_mono_command(monkeypatch):
    captured = {}

    def fake_run(args):
        captured["args"] = args

    monkeypatch.setattr(extract.binaries, "run", fake_run)
    monkeypatch.setattr(extract.binaries, "ffmpeg", lambda: "ffmpeg")

    extract.extract_audio(Path("in.mov"), Path("out.wav"))

    args = captured["args"]
    assert "-ar" in args and args[args.index("-ar") + 1] == "16000"
    assert "-ac" in args and args[args.index("-ac") + 1] == "1"
    assert "-vn" in args
    assert args[-1] == "out.wav"


@pytest.mark.slow
def test_extract_produces_real_audio(tmp_path):
    out = tmp_path / "audio.wav"
    extract.extract_audio(FIXTURES / "clip.mov", out)

    assert out.exists() and out.stat().st_size > 0
    info = extract.audio_info(out)
    assert info["sample_rate"] == 16000
    assert info["channels"] == 1
