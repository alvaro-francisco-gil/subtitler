from pathlib import Path

import pytest

from subtitler import probe, render
from subtitler.models import Cue, Word

FIXTURES = Path(__file__).parent / "fixtures"


def hdr_info():
    return probe.MediaInfo(
        stored_width=1920, stored_height=1080, rotation=-90,
        display_width=1080, display_height=1920,
        fps=23.976, duration=691.135,
        color_transfer="arib-std-b67", color_primaries="bt2020", is_hdr=True,
    )


def sdr_info():
    return probe.MediaInfo(
        stored_width=1920, stored_height=1080, rotation=0,
        display_width=1920, display_height=1080,
        fps=30.0, duration=12.0,
        color_transfer="bt709", color_primaries="bt709", is_hdr=False,
    )


def test_filter_chain_tone_maps_before_subtitles():
    chain = render.build_filter_chain(Path("/w/subs.ass"), Path("/w/fonts"), tone_map=True)

    assert chain.index("tonemap") < chain.index("subtitles")
    assert "bt709" in chain
    assert "format=yuv420p" in chain


def test_filter_chain_without_tone_mapping_is_just_subtitles():
    chain = render.build_filter_chain(Path("/w/subs.ass"), Path("/w/fonts"), tone_map=False)

    assert "tonemap" not in chain
    assert chain.startswith("subtitles=")


def test_filter_path_escaping():
    assert render.escape_filter_path(Path("/a b/subs.ass")) == "/a b/subs.ass"
    assert render.escape_filter_path(Path("/a:b/subs.ass")) == r"/a\:b/subs.ass"


def test_unsafe_staged_path_is_rejected_loudly():
    with pytest.raises(render.UnsafePathError):
        render._assert_safe(Path("/tmp/o'brien/subs.ass"))


def test_command_never_transposes():
    command = render.build_command(
        Path("in.mov"), Path("subs.ass"), Path("out.mp4"), hdr_info(),
        fontsdir=Path("fonts"),
    )
    assert not any("transpose" in arg for arg in command)


def test_command_tone_maps_an_hdr_source():
    command = render.build_command(
        Path("in.mov"), Path("subs.ass"), Path("out.mp4"), hdr_info(),
        fontsdir=Path("fonts"),
    )
    assert any("tonemap" in arg for arg in command)


def test_command_skips_tone_mapping_for_an_sdr_source():
    command = render.build_command(
        Path("in.mov"), Path("subs.ass"), Path("out.mp4"), sdr_info(),
        fontsdir=Path("fonts"),
    )
    assert not any("tonemap" in arg for arg in command)


def test_sample_window_seeks_before_the_input():
    command = render.build_command(
        Path("in.mov"), Path("subs.ass"), Path("out.mp4"), hdr_info(),
        fontsdir=Path("fonts"), start=135.0, duration=10.0,
    )
    assert command.index("-ss") < command.index("-i")
    assert command[command.index("-ss") + 1] == "135.0"
    assert command[command.index("-t") + 1] == "10.0"


def test_shift_cues_rebases_to_the_window_and_drops_the_rest():
    cues = [
        Cue(words=(Word("a", 1.0, 2.0),)),
        Cue(words=(Word("b", 11.0, 12.0),)),
        Cue(words=(Word("c", 30.0, 31.0),)),
    ]

    shifted = render.shift_cues(cues, start=10.0, end=20.0)

    assert len(shifted) == 1
    assert shifted[0].text == "b"
    assert shifted[0].start == pytest.approx(1.0)
    assert shifted[0].end == pytest.approx(2.0)


def test_shift_cues_clamps_a_cue_straddling_the_window_start():
    cues = [Cue(words=(Word("a", 8.0, 9.0), Word("b", 9.0, 12.0)))]
    shifted = render.shift_cues(cues, start=10.0, end=20.0)
    assert shifted[0].start == 0.0
    assert all(w.start >= 0.0 and w.end >= 0.0 for w in shifted[0].words)


def test_shift_cues_clamps_a_cue_straddling_the_window_end():
    cues = [Cue(words=(Word("a", 18.0, 25.0),))]
    shifted = render.shift_cues(cues, start=10.0, end=20.0)
    assert shifted[0].end == 10.0


@pytest.mark.slow
def test_burn_produces_an_upright_sdr_file(tmp_path):
    ass_path = tmp_path / "subs.ass"
    ass_path.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: K,DejaVu Sans,96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,6,3,5,0,0,0,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:03.00,K,,0,0,0,,{\\an5\\pos(540,1382)}HOLA\n"
    )

    info = probe.probe(FIXTURES / "clip.mov")
    out = tmp_path / "out.mp4"
    render.burn(
        FIXTURES / "clip.mov", ass_path, out, info,
        fontsdir=Path("assets/fonts"), start=0.0, duration=3.0,
        encoder="libx264",
    )

    assert out.exists()
    result = probe.probe(out)
    assert (result.display_width, result.display_height) == (1080, 1920)
    assert result.is_hdr is False
    assert result.color_primaries == "bt709"
    assert result.duration == pytest.approx(3.0, abs=0.3)


@pytest.mark.slow
def test_burn_works_from_a_path_containing_special_characters(tmp_path):
    """The staging fix exists because ffmpeg's filter parser mangles such paths."""
    awkward = tmp_path / "o'brien's dir"
    awkward.mkdir()
    ass_path = awkward / "subs.ass"
    ass_path.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: K,DejaVu Sans,96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,6,3,5,0,0,0,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:02.00,K,,0,0,0,,{\\an5\\pos(540,1382)}HOLA\n"
    )

    info = probe.probe(FIXTURES / "clip.mov")
    out = awkward / "out.mp4"
    render.burn(
        FIXTURES / "clip.mov", ass_path, out, info,
        fontsdir=Path("assets/fonts"), start=0.0, duration=2.0, encoder="libx264",
    )

    assert out.exists()
    assert probe.probe(out).display_width == 1080
