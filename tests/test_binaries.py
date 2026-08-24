import pytest

from subtitler import binaries


def test_ffmpeg_prefers_path_binary(monkeypatch):
    monkeypatch.setattr(binaries.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
    assert binaries.ffmpeg() == "/usr/bin/ffmpeg"


def test_ffmpeg_falls_back_to_static_build(monkeypatch):
    def which(name):
        return "/home/u/.local/bin/static_ffmpeg" if name == "static_ffmpeg" else None

    monkeypatch.setattr(binaries.shutil, "which", which)
    assert binaries.ffmpeg() == "/home/u/.local/bin/static_ffmpeg"


def test_ffprobe_falls_back_to_static_build(monkeypatch):
    def which(name):
        return "/home/u/.local/bin/static_ffprobe" if name == "static_ffprobe" else None

    monkeypatch.setattr(binaries.shutil, "which", which)
    assert binaries.ffprobe() == "/home/u/.local/bin/static_ffprobe"


def test_missing_binary_raises(monkeypatch):
    monkeypatch.setattr(binaries.shutil, "which", lambda name: None)
    with pytest.raises(binaries.BinaryError, match="ffmpeg"):
        binaries.ffmpeg()


def test_run_raises_with_stderr_on_failure():
    with pytest.raises(binaries.BinaryError, match="no-such-flag"):
        binaries.run(["python3", "-c", "import sys; sys.stderr.write('no-such-flag'); sys.exit(2)"])


def test_run_returns_stdout_on_success():
    result = binaries.run(["python3", "-c", "print('ok')"])
    assert result.stdout.strip() == "ok"
