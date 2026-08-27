from pathlib import Path

import pytest

from subtitler import repair
from subtitler.models import Word

AUDIO = Path("/tmp/audio.wav")


def w(text, start, end):
    return Word(text=text, start=start, end=end)


def steady(count, *, start=0.0, step=0.4):
    """Words at a believable speaking rate, for padding around a failure."""
    return [w(f"p{i}", start + i * step, start + i * step + step) for i in range(count)]


def test_no_collapsed_words_means_nothing_to_repair():
    assert repair.collapsed_runs(steady(6)) == []
    assert repair.broken_spans(steady(6)) == []


def test_a_collapsed_run_is_found():
    words = steady(3) + [w("a", 5.0, 5.0), w("b", 5.0, 5.0)] + steady(3, start=6.0)
    assert repair.collapsed_runs(words) == [(3, 4)]


def test_a_collapsed_run_at_the_end_is_closed():
    words = steady(2) + [w("a", 5.0, 5.0)]
    assert repair.collapsed_runs(words) == [(2, 2)]


def test_the_window_grows_past_neighbours_that_are_also_compressed():
    """The shape of a real failure: the flush crushes its neighbours too.

    Words 5-8 are not collapsed, but four of them share a tenth of a second —
    they were swept along by the same failure. A margin of two would stop
    inside that wreckage, so growth has to keep going until it reaches speech
    that is actually possible.
    """
    words = (
        steady(5)                                                    # 0-4, sound
        + [w(f"c{i}", 5.0 + i * 0.02, 5.02 + i * 0.02) for i in range(4)]  # 5-8, crushed
        + [w(f"x{i}", 5.1, 5.1) for i in range(6)]                   # 9-14, collapsed
        + steady(5, start=9.0)                                       # 15-19, sound
    )
    (lo, hi), = repair.broken_spans(words)

    assert repair.seconds_per_word(words, lo, hi) >= repair.MIN_SECONDS_PER_WORD
    assert lo < 5, "must reach back past the crushed words into sound timings"
    assert (lo, hi) == (4, 15)


def test_growing_stops_at_the_ends_of_the_word_list():
    words = [w("a", 5.0, 5.0), w("b", 5.0, 5.0)]
    assert repair.broken_spans(words) == [(0, 1)]


def test_overlapping_windows_are_merged():
    words = (
        steady(4)
        + [w("x", 2.0, 2.0)]
        + steady(2, start=2.0)
        + [w("y", 3.0, 3.0)]
        + steady(4, start=9.0)
    )
    assert len(repair.broken_spans(words)) == 1


def test_repair_splices_realigned_timings_back_in():
    words = steady(4) + [w("uno", 2.0, 2.0), w("dos", 2.0, 2.0)] + steady(4, start=8.0)
    calls = []

    def realign(audio, text, start, end):
        calls.append((text, start, end))
        # Window-relative timings, as a real aligner returns.
        return [
            Word(text=t, start=i * 0.5, end=i * 0.5 + 0.5)
            for i, t in enumerate(text.split())
        ]

    result = repair.repair(AUDIO, words, realign)

    assert len(calls) == 1
    assert [x.text for x in result] == [x.text for x in words]
    # Rebased onto the window start, so timings are absolute again.
    window_start = calls[0][1]
    repaired_first = next(i for i, x in enumerate(words) if x.start >= window_start)
    assert result[repaired_first].start == pytest.approx(window_start)
    assert result[0] == words[0], "words outside the window are left alone"
    assert all(later.start >= earlier.start for earlier, later in zip(result, result[1:]))


def test_a_repair_that_changes_the_words_is_rejected():
    words = steady(4) + [w("uno", 2.0, 2.0), w("dos", 2.0, 2.0)] + steady(4, start=8.0)

    def realign(audio, text, start, end):
        return [Word(text="algo-distinto", start=0.0, end=1.0)]

    assert repair.repair(AUDIO, words, realign) == words


def test_an_empty_window_is_skipped_rather_than_realigned():
    # Every word on one instant: there is no audio window to hand the aligner.
    words = [w("a", 5.0, 5.0), w("b", 5.0, 5.0)]

    def realign(audio, text, start, end):  # pragma: no cover - must not run
        raise AssertionError("should not realign an empty window")

    assert repair.repair(AUDIO, words, realign) == words


def test_clean_words_are_returned_untouched():
    words = steady(8)

    def realign(audio, text, start, end):  # pragma: no cover - must not run
        raise AssertionError("should not realign clean words")

    assert repair.repair(AUDIO, words, realign) == words


def test_a_repair_that_leaves_things_no_better_is_rejected():
    words = steady(4) + [w("uno", 2.0, 2.0), w("dos", 2.0, 2.0)] + steady(4, start=8.0)

    def realign(audio, text, start, end):
        # Same words, still collapsed: no improvement, so keep the original.
        return [Word(text=t, start=0.0, end=0.0) for t in text.split()]

    assert repair.repair(AUDIO, words, realign) == words


def test_a_partial_improvement_is_accepted():
    words = steady(4) + [w("uno", 2.0, 2.0), w("dos", 2.0, 2.0)] + steady(4, start=8.0)

    def realign(audio, text, start, end):
        texts = text.split()
        # One word still collapsed, the rest timed: fewer than we started with.
        return [
            Word(text=t, start=0.0, end=0.0) if i == 0
            else Word(text=t, start=i * 0.5, end=i * 0.5 + 0.5)
            for i, t in enumerate(texts)
        ]

    result = repair.repair(AUDIO, words, realign)
    assert result != words
    assert repair._collapsed_count(result) < repair._collapsed_count(words)
