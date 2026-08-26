import pytest

from subtitler.models import Cue, Word
from subtitler import group


def w(text, start, end):
    return Word(text=text, start=start, end=end, score=1.0)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Matamala.", True),
        ("¡Viva!", True),
        ("¿Qué?", True),
        ('dijo:', True),
        ('"cierto."', True),
        ("Matamala", False),
        ("libre,", False),
    ],
)
def test_ends_sentence(text, expected):
    assert group.ends_sentence(text) is expected


def test_cue_exposes_span_and_text():
    cue = Cue(words=(w("Vecinas", 1.0, 1.4), w("y", 1.4, 1.5)))
    assert cue.start == 1.0
    assert cue.end == 1.5
    assert cue.text == "Vecinas y"


def test_splits_at_max_words():
    words = [w(f"p{i}", i * 0.4, i * 0.4 + 0.3) for i in range(6)]
    cues = group.group_words(words, max_words=3, pause_break=10.0)
    assert [len(c.words) for c in cues] == [3, 3]


def test_breaks_at_sentence_end():
    words = [w("Hola.", 0.0, 0.4), w("Adios", 0.4, 0.8), w("amigo", 0.8, 1.2)]
    cues = group.group_words(words, max_words=3, pause_break=10.0)
    assert [c.text for c in cues] == ["Hola.", "Adios amigo"]


def test_breaks_on_long_pause():
    words = [w("uno", 0.0, 0.4), w("dos", 3.0, 3.4), w("tres", 3.4, 3.8)]
    cues = group.group_words(words, max_words=3, pause_break=0.35)
    assert [c.text for c in cues] == ["uno", "dos tres"]


def test_rebalances_a_stranded_single_word():
    words = [w(f"p{i}", i * 0.3, i * 0.3 + 0.25) for i in range(4)]
    cues = group.group_words(words, max_words=3, pause_break=10.0)
    assert [len(c.words) for c in cues] == [2, 2]


def test_does_not_rebalance_across_a_sentence_end():
    words = [w("uno", 0.0, 0.3), w("dos", 0.3, 0.6), w("tres.", 0.6, 0.9), w("cuatro", 0.9, 1.2)]
    cues = group.group_words(words, max_words=3, pause_break=10.0)
    assert [c.text for c in cues] == ["uno dos tres.", "cuatro"]


def test_does_not_rebalance_across_a_pause():
    words = [w("uno", 0.0, 0.3), w("dos", 0.3, 0.6), w("tres", 0.6, 0.9), w("cuatro", 5.0, 5.3)]
    cues = group.group_words(words, max_words=3, pause_break=0.35)
    assert [c.text for c in cues] == ["uno dos tres", "cuatro"]


def test_empty_input_gives_no_cues():
    assert group.group_words([], max_words=3, pause_break=0.35) == []


def test_every_word_survives_grouping():
    words = [w(f"p{i}", i * 0.3, i * 0.3 + 0.25) for i in range(17)]
    cues = group.group_words(words, max_words=3, pause_break=0.35)
    assert [x.text for c in cues for x in c.words] == [x.text for x in words]


def width_limit(characters: int):
    """A `fits` predicate standing in for frame width, one unit per character."""

    def fits(texts: list[str]) -> bool:
        return sum(len(t) for t in texts) + len(texts) - 1 <= characters

    return fits


def test_breaks_a_cue_that_would_not_fit_the_frame():
    words = [w("largouno", 0.0, 0.3), w("largodos", 0.3, 0.6), w("largotres", 0.6, 0.9)]
    cues = group.group_words(words, max_words=3, pause_break=10.0, fits=width_limit(17))
    assert [c.text for c in cues] == ["largouno largodos", "largotres"]


def test_a_word_too_wide_on_its_own_still_gets_a_cue():
    words = [w("uno", 0.0, 0.3), w("interminablemente", 0.3, 0.6)]
    cues = group.group_words(words, max_words=3, pause_break=10.0, fits=width_limit(5))
    assert [c.text for c in cues] == ["uno", "interminablemente"]


def test_rebalancing_never_creates_an_over_wide_cue():
    # Pulling "enormemente" forward would balance the word counts but would
    # put 27 units on a line that holds 20, so the stranded word stays alone.
    words = [
        w("a", 0.0, 0.3),
        w("de", 0.3, 0.6),
        w("enormemente", 0.6, 0.9),
        w("descomunalmente", 0.9, 1.2),
    ]
    cues = group.group_words(words, max_words=3, pause_break=10.0, fits=width_limit(20))
    assert [c.text for c in cues] == ["a de enormemente", "descomunalmente"]


def test_no_words_are_lost_when_breaking_on_width():
    words = [w(f"palabra{i}", i * 0.3, i * 0.3 + 0.25) for i in range(11)]
    cues = group.group_words(words, max_words=3, pause_break=0.35, fits=width_limit(20))
    assert [x.text for c in cues for x in c.words] == [x.text for x in words]
