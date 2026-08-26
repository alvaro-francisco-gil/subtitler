import pytest

from subtitler import style


@pytest.mark.parametrize(
    "hex_colour,expected",
    [
        ("#FFFFFF", "&H00FFFFFF"),
        ("#000000", "&H00000000"),
        ("#FFD400", "&H0000D4FF"),  # R=FF G=D4 B=00 -> BGR order
        ("#112233", "&H00332211"),
        ("FFD400", "&H0000D4FF"),   # leading hash optional
    ],
)
def test_ass_colour_converts_rgb_to_bgr(hex_colour, expected):
    assert style.ass_colour(hex_colour) == expected


def test_ass_colour_rejects_bad_input():
    with pytest.raises(ValueError):
        style.ass_colour("#12345")


def test_load_reads_every_section(tmp_path):
    path = tmp_path / "style.toml"
    path.write_text(
        """
[font]
family = "Montserrat ExtraBold"
file = "assets/fonts/Montserrat-ExtraBold.ttf"
size = 96

[colour]
fill = "#FFFFFF"
highlight = "#FFD400"
outline = "#000000"

[layout]
position = 0.72
word_spacing = 0.55
outline_width = 6.0
shadow_depth = 3.0
all_caps = true

[animation]
pop_scale = 1.08
pop_ms = 140

[cues]
max_words = 3
pause_break = 0.35
"""
    )

    loaded = style.load(path)

    assert loaded.font_family == "Montserrat ExtraBold"
    assert loaded.font_size == 96
    assert loaded.fill == "#FFFFFF"
    assert loaded.highlight == "#FFD400"
    assert loaded.position == 0.72
    assert loaded.word_spacing == 0.55
    assert loaded.all_caps is True
    assert loaded.pop_scale == 1.08
    assert loaded.pop_ms == 140
    assert loaded.max_words == 3
    assert loaded.pause_break == 0.35


def test_font_path_resolves_relative_to_the_style_file(tmp_path):
    (tmp_path / "assets" / "fonts").mkdir(parents=True)
    font = tmp_path / "assets" / "fonts" / "Montserrat-ExtraBold.ttf"
    font.write_bytes(b"")
    path = tmp_path / "style.toml"
    path.write_text(
        """
[font]
family = "X"
file = "assets/fonts/Montserrat-ExtraBold.ttf"
size = 10
[colour]
fill = "#FFFFFF"
highlight = "#FFD400"
outline = "#000000"
[layout]
position = 0.5
word_spacing = 1.0
outline_width = 1.0
shadow_depth = 1.0
all_caps = false
[animation]
pop_scale = 1.0
pop_ms = 0
[cues]
max_words = 2
pause_break = 0.5
"""
    )

    loaded = style.load(path)
    assert loaded.font_path == font


def test_repo_style_file_loads():
    loaded = style.load(style.DEFAULT_STYLE_PATH)
    assert loaded.font_path.exists(), "the vendored font asset is missing"
