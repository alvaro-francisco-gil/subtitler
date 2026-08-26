import dataclasses
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
        word_spacing=1.0,
        outline_width=6.0,
        shadow_depth=3.0,
        all_caps=True,
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
