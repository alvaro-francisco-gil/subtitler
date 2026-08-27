from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.gpu
@pytest.mark.slow
def test_sample_render_end_to_end(tmp_path):
    """The whole pipeline on a 15 s clip: align, group, render, burn."""
    from subtitler import cli, probe

    video = tmp_path / "clip.mov"
    video.write_bytes((FIXTURES / "clip.mov").read_bytes())
    transcript = tmp_path / "script.txt"
    transcript.write_text((FIXTURES / "clip_script.txt").read_text())

    out = tmp_path / "sample.mp4"
    exit_code = cli.main([
        "sample", str(video), str(transcript),
        "--at", "0", "--len", "5", "--out", str(out),
    ])

    assert exit_code == 0
    assert out.exists()

    info = probe.probe(out)
    assert (info.display_width, info.display_height) == (1080, 1920)
    assert info.is_hdr is False
    assert info.duration == pytest.approx(5.0, abs=0.4)

    work = video.parent / ".subtitler" / "clip"
    assert (work / "words.json").exists()
    assert (work / "drift.md").exists()
    # `sample` writes the windowed document; `subs.ass` is what `render` produces.
    assert (work / "sample.ass").exists()


@pytest.mark.gpu
@pytest.mark.slow
def test_second_sample_reuses_cached_alignment(tmp_path):
    from subtitler import cli

    video = tmp_path / "clip.mov"
    video.write_bytes((FIXTURES / "clip.mov").read_bytes())
    transcript = tmp_path / "script.txt"
    transcript.write_text((FIXTURES / "clip_script.txt").read_text())

    cli.main(["sample", str(video), str(transcript), "--at", "0", "--len", "3"])
    words = video.parent / ".subtitler" / "clip" / "words.json"
    first_mtime = words.stat().st_mtime

    cli.main(["sample", str(video), str(transcript), "--at", "0", "--len", "3"])

    assert words.stat().st_mtime == first_mtime, "alignment should not have re-run"
