import pytest

from talk_studio import media, tools
from talk_studio.timecode import Excerpt
from talk_studio.tools import Param, ToolError
from talk_studio.tools.ffmpeg_chain import FfmpegChain, filter_chain


def test_param_coerces_strings():
    assert Param("x", "float", 1.0, "", 0.0, 10.0).coerce("2.5") == 2.5
    assert Param("n", "int", 1, "").coerce("3") == 3
    assert Param("b", "bool", False, "").coerce("true") is True
    assert Param("b", "bool", True, "").coerce("off") is False
    assert Param("c", "choice", "a", "", choices=("a", "b")).coerce("b") == "b"


@pytest.mark.parametrize("param, raw, message", [
    (Param("x", "float", 1.0, "", 0.0, 10.0), "11", "between"),
    (Param("x", "float", 1.0, ""), "loud", "expects float"),
    (Param("b", "bool", False, ""), "maybe", "expects bool"),
    (Param("c", "choice", "a", "", choices=("a", "b")), "z", "one of"),
])
def test_param_rejects_bad_values(param, raw, message):
    with pytest.raises(ToolError, match=message):
        param.coerce(raw)


def test_settings_fill_defaults_and_reject_unknown_keys():
    chain = FfmpegChain()
    settings = chain.settings({"denoise_db": "20"})
    assert settings["denoise_db"] == 20.0
    assert settings["highpass_hz"] == 80.0
    with pytest.raises(ToolError, match="no setting volume"):
        chain.settings({"volume": 3})


def test_filter_chain_default_and_disabled():
    chain = FfmpegChain()
    assert filter_chain(chain.settings({})) == (
        "highpass=f=80,afftdn=nr=12:nf=-50:tn=1,"
        "acompressor=threshold=0.125893:ratio=3:attack=5:release=100:makeup=2,"
        "loudnorm=I=-16:TP=-1.5:LRA=11"
    )
    off = chain.settings({"highpass_hz": 0, "denoise_db": 0, "compress": False, "loudness_lufs": 0})
    assert filter_chain(off) == "anull"


def test_sample_renders_the_excerpt_length(tiny_video, tmp_path):
    chain = FfmpegChain()
    out = chain.sample(tiny_video, Excerpt(2.5, 4.5), chain.settings({}), tmp_path / "s.wav")
    assert media.stream_durations(out)["audio"] == pytest.approx(2.0, abs=media.AUDIO_TOLERANCE)


def test_sample_at_the_very_start_has_no_preroll(tiny_video, tmp_path):
    out = tools.Original().sample(tiny_video, Excerpt(0.0, 1.0), {}, tmp_path / "o.wav")
    assert media.stream_durations(out)["audio"] == pytest.approx(1.0, abs=media.AUDIO_TOLERANCE)


def test_apply_renders_the_full_length(tiny_video, tmp_path):
    chain = FfmpegChain()
    out = chain.apply(tiny_video, chain.settings({"denoise_db": 20}), tmp_path / "full.wav")
    assert media.stream_durations(out)["audio"] == pytest.approx(media.duration(tiny_video), abs=media.AUDIO_TOLERANCE)


def test_registry():
    assert [t.name for t in tools.all_tools()] == ["ffmpeg-chain", "deepfilternet"]
    assert tools.get_tool("ffmpeg-chain").version == "1"
    with pytest.raises(ToolError, match="no tool named"):
        tools.get_tool("magic")
