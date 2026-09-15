import pytest

from talk_studio.timecode import Excerpt, format_timecode, parse_excerpt, parse_timecode


def test_parses_timecodes():
    assert parse_timecode("90") == 90.0
    assert parse_timecode("1:30") == 90.0
    assert parse_timecode("1:02:03") == 3723.0
    assert parse_timecode("0:05.5") == 5.5


@pytest.mark.parametrize("bad", ["banana", "-5", "-1:30", "1:2:3:4", ""])
def test_rejects_bad_timecodes(bad):
    with pytest.raises(ValueError):
        parse_timecode(bad)


def test_formats_timecodes():
    assert format_timecode(180) == "3:00"
    assert format_timecode(3723) == "1:02:03"
    assert format_timecode(5.5) == "0:05.5"


def test_excerpt_round_trip():
    excerpt = parse_excerpt("3:00-3:12")
    assert excerpt == Excerpt(180.0, 192.0)
    assert excerpt.duration == 12.0
    assert str(excerpt) == "3:00-3:12"


@pytest.mark.parametrize("bad", ["3:00", "3:12-3:00", "3:00-3:00"])
def test_rejects_bad_excerpts(bad):
    with pytest.raises(ValueError):
        parse_excerpt(bad)
