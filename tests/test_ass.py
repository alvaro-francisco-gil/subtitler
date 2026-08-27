import dataclasses
import re
from pathlib import Path

import pytest

from subtitler import ass
from subtitler.models import Cue, Word
from subtitler.style import Style


def stub_measure(text: str) -> float:
    """Every glyph is 50 px wide, so expected positions are easy to compute."""
    return 50.0 * len(text)


@pytest.fixture
def sty():
    return Style(
        font_family="Montserrat ExtraBold",
        font_path=Path("assets/fonts/Montserrat-ExtraBold.ttf"),
        font_size=96,
        fill="#FFFFFF",
        highlight="#FFD400",
        outline="#000000",
        position=0.72,
        max_width=0.92,
        word_spacing=1.0,
        outline_width=6.0,
        shadow_depth=3.0,
        all_caps=True,
        title_size=150,
        title_position=0.42,
        title_line_spacing=1.18,
        title_hold=4.0,
        title_fade_ms=450,
        title_rise=0.018,
        title_stagger_ms=130,
        pop_scale=1.08,
        pop_ms=140,
        max_words=3,
        pause_break=0.35,
    )


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0.0, "0:00:00.00"),
        (1.5, "0:00:01.50"),
        (61.234, "0:01:01.23"),
        (3661.0, "1:01:01.00"),
    ],
)
def test_ass_time(seconds, expected):
    assert ass.ass_time(seconds) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("hola", "hola"),
        ("a{b}c", r"a\{b\}c"),
        ("back\\slash", r"back\\slash"),
    ],
)
def test_escape(raw, expected):
    assert ass.escape(raw) == expected


def test_header_uses_display_resolution(sty):
    doc = ass.build_ass([], sty, 1080, 1920, stub_measure)
    assert "PlayResX: 1080" in doc
    assert "PlayResY: 1920" in doc


def test_style_line_names_the_family_without_bold_flag(sty):
    doc = ass.build_ass([], sty, 1080, 1920, stub_measure)
    style_line = next(l for l in doc.splitlines() if l.startswith("Style:"))
    fields = style_line.removeprefix("Style: ").split(",")
    assert fields[1] == "Montserrat ExtraBold"
    assert fields[2] == "96"
    assert fields[7] == "0", "Bold must be 0; the vendored face is already ExtraBold"


def test_words_are_centred_as_a_group(sty):
    cue = Cue(words=(Word("AB", 0.0, 1.0), Word("CD", 1.0, 2.0)))
    doc = ass.build_ass([cue], sty, 1000, 2000, stub_measure)

    # "AB" and "CD" are 100 px each; one space is 50 px. Total 250 px.
    # Left edge = (1000 - 250) / 2 = 375. Centres at 425 and 575.
    assert r"\pos(425,1440)" in doc
    assert r"\pos(575,1440)" in doc


def test_each_word_gets_fill_then_highlight_then_fill(sty):
    cue = Cue(words=(Word("AB", 0.0, 1.0), Word("CD", 1.0, 2.0)))
    doc = ass.build_ass([cue], sty, 1000, 2000, stub_measure)
    events = [l for l in doc.splitlines() if l.startswith("Dialogue:")]

    # AB: highlighted 0-1, fill 1-2. CD: fill 0-1, highlighted 1-2.
    assert len(events) == 4
    assert sum("&H0000D4FF" in e for e in events) == 2, "one highlight phase per word"


def test_pop_transform_only_on_events_starting_with_the_cue(sty):
    cue = Cue(words=(Word("AB", 0.0, 1.0), Word("CD", 1.0, 2.0)))
    doc = ass.build_ass([cue], sty, 1000, 2000, stub_measure)
    events = [l for l in doc.splitlines() if l.startswith("Dialogue:")]

    starting_at_cue_start = [e for e in events if ",0:00:00.00," in e]
    later = [e for e in events if ",0:00:00.00," not in e]
    assert all(r"\t(" in e for e in starting_at_cue_start)
    assert all(r"\t(" not in e for e in later)


def test_all_caps_is_applied(sty):
    cue = Cue(words=(Word("hola", 0.0, 1.0),))
    doc = ass.build_ass([cue], sty, 1000, 2000, stub_measure)
    assert "HOLA" in doc
    assert "hola" not in doc.split("[Events]")[1]


def test_all_caps_can_be_disabled(sty):
    lower = ass.build_ass(
        [Cue(words=(Word("hola", 0.0, 1.0),))],
        dataclasses.replace(sty, all_caps=False),
        1000, 2000, stub_measure,
    )
    assert "hola" in lower.split("[Events]")[1]


def test_events_are_ordered_by_start_time(sty):
    cues = [
        Cue(words=(Word("A", 5.0, 6.0),)),
        Cue(words=(Word("B", 0.0, 1.0),)),
    ]
    doc = ass.build_ass(cues, sty, 1000, 2000, stub_measure)
    events = [l for l in doc.splitlines() if l.startswith("Dialogue:")]
    starts = [e.split(",")[1] for e in events]
    assert starts == sorted(starts)


def test_word_spacing_tightens_the_gap(sty):
    """The gap is the font's space advance scaled by style.word_spacing."""
    cue = Cue(words=(Word("AB", 0.0, 1.0), Word("CD", 1.0, 2.0)))

    wide = ass.build_ass([cue], sty, 1000, 2000, stub_measure)
    tight = ass.build_ass(
        [cue], dataclasses.replace(sty, word_spacing=0.5), 1000, 2000, stub_measure
    )

    # Stub: glyphs are 50px, so a full space is 50px and half a space is 25px.
    # Total width shrinks by 25px, so the left word moves right by 12.5px.
    assert r"\pos(425,1440)" in wide
    assert r"\pos(438,1440)" in tight


def test_a_wide_cue_is_scaled_to_fit(sty):
    """119 of 756 real cues overflowed the frame before this existed."""
    cue = Cue(words=(Word("ABCDEFGHIJ", 0.0, 1.0), Word("KLMNOPQRST", 1.0, 2.0)))
    doc = ass.build_ass([cue], sty, 1000, 2000, stub_measure)

    # Stub: 50px per glyph, so this cue is 500 + 500 + one gap — far over 1000.
    import re
    for x in (int(m) for m in re.findall(r"\\pos\((\d+),", doc)):
        assert 0 <= x <= 1000, f"word centre {x} is off-canvas"
    assert "\\fscx" in doc


def test_a_narrow_cue_is_not_scaled(sty):
    cue = Cue(words=(Word("AB", 0.0, 1.0),))
    doc = ass.build_ass([cue], sty, 1000, 2000, stub_measure)
    # Only the pop transform should touch scale, never a base shrink.
    assert "\\fscx100\\fscy100" in doc or "\\fscx93" in doc


def test_wrap_title_breaks_on_the_usable_width(sty):
    # stub_measure is 50px/char and max_width 0.92 of 1000 gives 920px = 18 chars.
    lines = ass.wrap_title("Pregon fiestas de Matamala 2026", sty, 1000, stub_measure)
    assert lines == ["Pregon fiestas de", "Matamala 2026"]


def test_wrap_title_keeps_a_short_title_on_one_line(sty):
    assert ass.wrap_title("Matamala", sty, 1000, stub_measure) == ["Matamala"]


def test_wrap_title_of_empty_text_is_empty(sty):
    assert ass.wrap_title("   ", sty, 1000, stub_measure) == []


def test_title_lines_are_staggered_and_share_an_end(sty):
    events = ass.title_events("Pregon fiestas de Matamala 2026", 2.0, sty, 1000, 1920, 96.0, stub_measure)

    assert len(events) == 2
    assert [round(start, 3) for start, _ in events] == [2.0, 2.13]
    assert all("0:00:06.00" in line for _, line in events)


def test_title_lines_stack_around_the_configured_position(sty):
    events = ass.title_events("Pregon fiestas de Matamala 2026", 2.0, sty, 1000, 1920, 96.0, stub_measure)

    # line_height = 96 * 1.18 = 113.28; block centres on 0.42 * 1920 = 806.4
    resting = [int(re.search(r"\\move\(\d+,\d+,\d+,(\d+),", line).group(1)) for _, line in events]
    assert resting == [750, 863]
    assert sum(resting) / 2 == pytest.approx(1920 * sty.title_position, abs=1.0)


def test_a_title_line_rises_into_place_as_it_fades_in(sty):
    (_, line), = ass.title_events("Matamala", 2.0, sty, 1000, 1920, 96.0, stub_measure)

    # 0.018 * 1920 = 34.56 -> 35px below its resting place, arriving over the fade.
    assert "\\move(500,841,500,806,0,450)" in line
    assert "\\fad(450,450)" in line
    assert "\\pos(" not in line


def test_no_title_means_no_title_events(sty):
    document = ass.build_ass([Cue(words=(Word("uno", 0.0, 0.5),))], sty, 1000, 1920, stub_measure)
    assert ",T,," not in document


def test_a_title_is_emitted_before_the_first_cue(sty):
    document = ass.build_ass(
        [Cue(words=(Word("uno", 10.0, 10.5),))], sty, 1000, 1920, stub_measure,
        title="Matamala", title_at=2.0, title_em=96.0, title_measure=stub_measure,
    )
    lines = [l for l in document.splitlines() if l.startswith("Dialogue")]
    assert ",T,," in lines[0]
    assert ",K,," in lines[1]
