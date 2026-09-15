from pathlib import Path

import pytest

from talk_studio import binaries


def make_media(out: Path, *, seconds: float = 6.0, video: bool = True) -> Path:
    """A tone under pink noise, optionally with a test-pattern video track."""
    args = [binaries.ffmpeg(), "-y", "-v", "error"]
    if video:
        args += ["-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=30:duration={seconds}"]
    args += [
        "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={seconds}",
        "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.05:sample_rate=48000:duration={seconds}",
    ]
    tone, noise = (1, 2) if video else (0, 1)
    args += ["-filter_complex", f"[{tone}:a][{noise}:a]amix=inputs=2:normalize=0[a]"]
    if video:
        args += ["-map", "0:v", "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    args += ["-map", "[a]", "-c:a", "aac" if video else "pcm_s16le", "-shortest", str(out)]
    binaries.run(args)
    return out


@pytest.fixture(scope="session")
def tiny_video(tmp_path_factory) -> Path:
    return make_media(tmp_path_factory.mktemp("media") / "tiny.mp4")


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("TALK_STUDIO_CACHE", str(tmp_path / "cache"))
