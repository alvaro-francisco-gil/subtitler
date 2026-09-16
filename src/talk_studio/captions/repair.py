"""Repairing spans where the aligner lost the thread.

Forced alignment over a long recording sometimes stops tracking and flushes
everything it skipped onto a single timestamp: a run of words all sharing one
start and end, preceded by a stretch of video with nothing on screen. Handing
the same words and the same audio to the aligner in a short window recovers
them — the failure is a property of the long pass, not of the audio or the
transcript.

Detection needs no thresholds tuned by eye. Speech has a floor on how fast it
can physically be: 41 words cannot occupy 0.44 seconds. A window is grown
outward until its seconds-per-word becomes possible again, which is exactly
when it has reached anchors the aligner got right.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from .models import Word

# Below this, a span is not slow speech — it is an alignment failure. Rapid
# Spanish runs about 0.2 s/word, so this leaves real speech well clear.
MIN_SECONDS_PER_WORD = 0.15

Realigner = Callable[[Path, str, float, float], list[Word]]


def collapsed_runs(words: Sequence[Word]) -> list[tuple[int, int]]:
    """Maximal runs of zero-duration words, as inclusive index pairs."""
    runs: list[tuple[int, int]] = []
    start = None

    for index, word in enumerate(words):
        if word.duration <= 0:
            if start is None:
                start = index
        elif start is not None:
            runs.append((start, index - 1))
            start = None

    if start is not None:
        runs.append((start, len(words) - 1))
    return runs


def seconds_per_word(words: Sequence[Word], lo: int, hi: int) -> float:
    span = words[hi].end - words[lo].start
    return span / (hi - lo + 1)


def grow(words: Sequence[Word], lo: int, hi: int) -> tuple[int, int]:
    """Widen a collapsed run until each edge reaches speech that is possible.

    A flush does not only collapse the words it gives up on — it crushes the
    ones either side into a fraction of a second too, so the window has to
    reach past them.

    Each side is measured against the near edge of the collapse rather than
    across the whole window. Measured across the whole window, a long healthy
    tail on one side supplies enough seconds to make the other side look fine,
    and growing stops inside the wreckage.
    """
    left, right = lo, hi

    while left > 0 and seconds_per_word(words, left, lo) < MIN_SECONDS_PER_WORD:
        left -= 1
    while right < len(words) - 1 and seconds_per_word(words, hi, right) < MIN_SECONDS_PER_WORD:
        right += 1

    return left, right


def broken_spans(words: Sequence[Word]) -> list[tuple[int, int]]:
    """Index ranges that need re-aligning, merged where they overlap."""
    spans = [grow(words, lo, hi) for lo, hi in collapsed_runs(words)]

    merged: list[tuple[int, int]] = []
    for lo, hi in sorted(spans):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def _collapsed_count(words: Sequence[Word]) -> int:
    return sum(1 for word in words if word.duration <= 0)


def repair(audio: Path, words: list[Word], realign: Realigner) -> list[Word]:
    """Re-align each broken span in isolation and splice the results back in.

    Two guards, because a repair that makes things worse is worse than no
    repair: the aligner must return the same words in the same order, and it
    must leave fewer of them collapsed than it found. Failing either, the
    original timings are kept — flawed, but honest.
    """
    repaired = list(words)

    for lo, hi in reversed(broken_spans(words)):
        window = repaired[lo : hi + 1]
        start, end = window[0].start, window[-1].end
        if end <= start:
            continue

        fresh = realign(audio, " ".join(word.text for word in window), start, end)
        if [word.text for word in fresh] != [word.text for word in window]:
            continue
        if _collapsed_count(fresh) >= _collapsed_count(window):
            continue

        repaired[lo : hi + 1] = [
            replace(word, start=word.start + start, end=word.end + start)
            for word in fresh
        ]

    return repaired
