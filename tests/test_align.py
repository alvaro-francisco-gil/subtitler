import json
from pathlib import Path

import pytest

from subtitler import align
from subtitler.models import Word

FIXTURES = Path(__file__).parent / "fixtures"


def test_words_round_trip_through_json(tmp_path):
    words = [
        Word(text="Vecinas", start=0.0, end=0.52, score=0.98),
        Word(text="y", start=0.52, end=0.61, score=0.71),
    ]
    path = tmp_path / "words.json"

    align.save_words(words, path)
    loaded = align.load_words(path)

    assert loaded == words


def test_saved_json_is_a_readable_list_of_objects(tmp_path):
    path = tmp_path / "words.json"
    align.save_words([Word("hola", 1.0, 1.5, 0.9)], path)

    payload = json.loads(path.read_text())
    assert payload == [{"text": "hola", "start": 1.0, "end": 1.5, "score": 0.9}]


def test_result_conversion_drops_blank_words_and_strips_spacing():
    class FakeWord:
        def __init__(self, word, start, end, probability):
            self.word = word
            self.start = start
            self.end = end
            self.probability = probability

    fake = [
        FakeWord(" Vecinas", 0.0, 0.5, 0.9),
        FakeWord("  ", 0.5, 0.6, 0.1),
        FakeWord(" y", 0.6, 0.7, 0.8),
    ]

    words = align.words_from_result(fake)

    assert [w.text for w in words] == ["Vecinas", "y"]
    assert words[0].score == 0.9


def test_missing_probability_defaults_to_one():
    class Bare:
        word = "hola"
        start = 0.0
        end = 1.0

    words = align.words_from_result([Bare()])
    assert words[0].score == 1.0


def test_a_genuine_zero_probability_is_not_treated_as_full_confidence():
    class Garbled:
        word = "inaudible"
        start = 1.0
        end = 1.4
        probability = 0.0

    words = align.words_from_result([Garbled()])
    assert words[0].score == 0.0, "a 0.0 probability must survive, not become 1.0"


@pytest.mark.gpu
def test_alignment_on_the_real_clip(tmp_path):
    """Aligns a known 15 s excerpt. Downloads a model on first run."""
    from subtitler import extract

    audio = tmp_path / "audio.wav"
    extract.extract_audio(FIXTURES / "clip.mov", audio)
    script = (FIXTURES / "clip_script.txt").read_text().strip()

    words = align.align(audio, script)

    assert len(words) > 10
    assert words[0].start >= 0.0
    assert all(w.end >= w.start for w in words)
    from subtitler.probe import probe as probe_media
    clip_duration = probe_media(FIXTURES / "clip.mov").duration
    assert all(w.end <= clip_duration + 0.5 for w in words), "no word may fall outside the clip"
    # Timings must be monotonic, or grouping and rendering both break.
    assert all(b.start >= a.start for a, b in zip(words, words[1:]))
