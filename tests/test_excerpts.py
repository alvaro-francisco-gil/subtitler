import numpy as np
import pytest

from talk_studio import excerpts
from talk_studio.timecode import Excerpt


def overlap(excerpt: Excerpt, low: float, high: float) -> float:
    return max(0.0, min(excerpt.end, high) - max(excerpt.start, low)) / excerpt.duration


def test_suggest_finds_quiet_loud_and_noisy_pause():
    levels = np.full(120, -60.0)      # 60 s of room tone
    levels[10:34] = -30.0             # quiet speech, 5–17 s
    levels[50:74] = -10.0             # loud speech, 25–37 s
    levels[90:114] = -48.0            # noisy pause, 45–57 s

    quiet, loud, pause = excerpts.suggest(levels)

    assert overlap(quiet, 5, 17) >= 0.6
    assert overlap(loud, 25, 37) >= 0.6
    assert overlap(pause, 45, 57) >= 0.6
    assert all(e.duration == 12.0 for e in (quiet, loud, pause))


def test_suggest_never_overlaps_its_picks():
    levels = np.full(200, -20.0)      # steady speech …
    levels[::5] = -60.0               # … with brief gaps, so no 12 s window is a pause
    picks = excerpts.suggest(levels)
    assert len(picks) == 2
    assert picks[0].end <= picks[1].start or picks[1].end <= picks[0].start


def test_suggest_rejects_a_source_shorter_than_one_excerpt():
    with pytest.raises(ValueError, match="shorter"):
        excerpts.suggest(np.full(10, -20.0))


def test_frame_levels_of_generated_media(tiny_video):
    levels = excerpts.frame_levels(tiny_video)
    assert len(levels) == 12
    assert np.all(levels > -60.0) and np.all(levels < 0.0)


def test_suggest_fallback_when_all_windows_in_ambiguous_band():
    # Build an array where every window's speech ratio falls in the ambiguous band (0.4 < ratio < 0.6).
    # Alternating frames: half above speech threshold, half below, so ratio = 0.5 for every window.
    levels = np.empty(120)
    levels[::2] = -30.0  # above floor + 15 dB: this is "speech"
    levels[1::2] = -50.0  # below floor + 15 dB: this is "noise"
    # floor = 10th percentile ≈ -50, speech threshold = -50 + 15 = -35
    # Every 24-frame window has 12 frames at -30 (speech) and 12 at -50 (noise), ratio = 0.5

    picks = excerpts.suggest(levels)

    # With all windows in the ambiguous band, fallback should return at least one excerpt.
    assert len(picks) >= 1
    # The excerpt should stay within the source's time span.
    assert picks[0].start >= 0.0
    assert picks[0].end <= 60.0  # 120 frames * 0.5 seconds per frame


def test_suggest_still_separates_speech_in_a_compressed_recording():
    # A phone recording with automatic gain: the whole talk inside a 12 dB band.
    levels = np.full(400, -42.0)      # room tone, only 12 dB below speech
    levels[20:140] = -33.0            # speech, 10–70 s
    levels[200:320] = -30.0           # louder speech, 100–160 s

    picks = excerpts.suggest(levels)

    assert len(picks) == 3
    assert all(p.duration == 12.0 for p in picks)


def test_frames_spread_across_the_talk():
    picks = excerpts.frames(600.0)
    assert [p.start for p in picks] == [50.0, 150.0, 250.0, 350.0, 450.0, 550.0]
    assert all(p.duration == 1.0 for p in picks)
