import pytest

from subtitler import cli
from subtitler.models import Word


def w(text, start, end):
    return Word(text=text, start=start, end=end, score=1.0)


def test_first_dense_span_prefers_the_busiest_window():
    # Sparse speech at the start, dense speech from 60 s.
    sparse = [w(f"s{i}", i * 5.0, i * 5.0 + 0.3) for i in range(10)]
    dense = [w(f"d{i}", 60.0 + i * 0.3, 60.0 + i * 0.3 + 0.25) for i in range(40)]

    assert cli.first_dense_span(sparse + dense, window=10.0) == pytest.approx(60.0, abs=1.0)


def test_first_dense_span_of_empty_input_is_zero():
    assert cli.first_dense_span([], window=10.0) == 0.0


def test_parses_sample_timecode():
    assert cli.parse_timecode("2:15") == 135.0
    assert cli.parse_timecode("0:05") == 5.0
    assert cli.parse_timecode("90") == 90.0
    assert cli.parse_timecode("1:02:03") == 3723.0


def test_rejects_a_bad_timecode():
    with pytest.raises(ValueError):
        cli.parse_timecode("banana")


def test_rejects_a_negative_timecode():
    with pytest.raises(ValueError):
        cli.parse_timecode("-5")
    with pytest.raises(ValueError):
        cli.parse_timecode("-1:30")


def test_first_dense_span_ignores_collapsed_words():
    """An alignment failure collapses many words onto one timestamp."""
    real = [w(f"r{i}", 100.0 + i * 0.3, 100.0 + i * 0.3 + 0.25) for i in range(30)]
    collapsed = [w(f"c{i}", 259.0, 259.0) for i in range(50)]

    assert cli.first_dense_span(real + collapsed, window=10.0) == pytest.approx(100.0, abs=1.0)


def test_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0
    assert "sample" in capsys.readouterr().out
