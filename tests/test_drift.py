import pytest

from subtitler import drift
from subtitler.clean import SourcePos
from subtitler.models import Word


def w(text, start, end, score=1.0):
    return Word(text=text, start=start, end=end, score=score)


def test_no_flags_on_clean_alignment():
    words = [w(f"p{i}", i * 0.4, i * 0.4 + 0.35) for i in range(20)]
    assert drift.find_drift(words) == []


def test_flags_a_run_of_low_confidence_words():
    words = [w(f"p{i}", i * 0.4, i * 0.4 + 0.35) for i in range(20)]
    words[5:9] = [w(f"p{i}", i * 0.4, i * 0.4 + 0.35, score=0.2) for i in range(5, 9)]

    flags = drift.find_drift(words, score_threshold=0.5, run_length=3)

    low = [f for f in flags if f.kind == "low-confidence"]
    assert len(low) == 1
    assert low[0].start == words[5].start
    assert low[0].end == words[8].end


def test_a_short_low_confidence_run_is_not_flagged():
    words = [w(f"p{i}", i * 0.4, i * 0.4 + 0.35) for i in range(20)]
    words[5] = w("p5", 2.0, 2.35, score=0.1)

    flags = drift.find_drift(words, score_threshold=0.5, run_length=3)
    assert [f for f in flags if f.kind == "low-confidence"] == []


def test_flags_a_long_silence():
    words = [w("uno", 0.0, 0.4), w("dos", 9.0, 9.4), w("tres", 9.4, 9.8)]

    flags = drift.find_drift(words, gap_threshold=2.0)

    gaps = [f for f in flags if f.kind == "gap"]
    assert len(gaps) == 1
    assert gaps[0].start == 0.4
    assert gaps[0].end == 9.0


def test_flags_an_implausibly_long_word():
    words = [w(f"p{i}", i * 0.4, i * 0.4 + 0.35) for i in range(20)]
    words[7] = w("estirada", 2.8, 7.0)

    flags = drift.find_drift(words, duration_factor=3.0, gap_threshold=100.0)

    long_words = [f for f in flags if f.kind == "long-word"]
    assert len(long_words) == 1
    assert long_words[0].text == "estirada"


def test_flags_carry_the_transcript_line_number():
    words = [w("uno", 0.0, 0.4), w("dos", 9.0, 9.4)]
    positions = [SourcePos(line=12, column=1), SourcePos(line=12, column=5)]

    flags = drift.find_drift(words, gap_threshold=2.0, positions=positions)

    assert flags[0].line == 12


def test_report_states_clean_when_there_are_no_flags():
    report = drift.render_report([], total_words=1500)
    assert "No drift detected" in report
    assert "1500" in report


def test_report_lists_each_flag_with_a_timecode():
    flags = [drift.Flag(kind="gap", start=65.0, end=70.0, text="uno dos", line=12)]
    report = drift.render_report(flags, total_words=1500)

    assert "1:05" in report
    assert "gap" in report
    assert "line 12" in report
    assert "uno dos" in report


@pytest.mark.gpu
@pytest.mark.slow
def test_a_sentence_absent_from_the_audio_is_flagged(tmp_path):
    """Align a script containing a sentence the speaker never said."""
    from pathlib import Path

    from subtitler import align, extract

    fixtures = Path(__file__).parent / "fixtures"
    audio = tmp_path / "audio.wav"
    extract.extract_audio(fixtures / "clip.mov", audio)

    real = (fixtures / "clip_script.txt").read_text().strip()
    padded = real + " Esta frase no aparece en el audio de ninguna manera."

    words = align.align(audio, padded)
    flags = drift.find_drift(words)

    assert flags, "an interpolated sentence must produce at least one flag"
    # The invented sentence is at the end, so a flag must land in its span.
    assert any(f.start > words[len(real.split())].start - 1.0 for f in flags)
