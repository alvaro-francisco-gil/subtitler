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
