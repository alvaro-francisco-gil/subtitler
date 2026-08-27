"""Packing timed words into on-screen cues.

Cues are short by design — a few words at a time, in the karaoke style. The
interesting decisions are where *not* to break: mid-sentence breaks that
strand a single word read badly, so a trailing single word is pulled back
into balance when nothing meaningful separates it from its neighbours.
"""

from __future__ import annotations

from .models import Cue, Word

SENTENCE_ENDINGS = ".!?:;"
TRAILING_PUNCTUATION = '"\')]}»”’'


def ends_sentence(text: str) -> bool:
    stripped = text.rstrip(TRAILING_PUNCTUATION)
    return bool(stripped) and stripped[-1] in SENTENCE_ENDINGS


def group_words(
    words: list[Word],
    *,
    max_words: int = 3,
    pause_break: float = 0.35,
    fits=None,
) -> list[Cue]:
    """Pack words into cues.

    `fits` is an optional predicate taking a list of word texts and returning
    whether they fit the frame on one line. Breaking on width here — rather
    than shrinking an over-wide cue at render time — is what keeps the font
    one consistent size across the whole video.
    """
    cues: list[Cue] = []
    current: list[Word] = []

    for index, word in enumerate(words):
        if current and fits is not None and not fits([w.text for w in current] + [word.text]):
            cues.append(Cue(words=tuple(current)))
            current = []

        current.append(word)

        at_capacity = len(current) >= max_words
        at_sentence_end = ends_sentence(word.text)
        before_pause = (
            index + 1 < len(words)
            and words[index + 1].start - word.end > pause_break
        )

        if at_capacity or at_sentence_end or before_pause:
            cues.append(Cue(words=tuple(current)))
            current = []

    if current:
        cues.append(Cue(words=tuple(current)))

    return _rebalance(cues, max_words=max_words, pause_break=pause_break, fits=fits)


def _rebalance(cues: list[Cue], *, max_words: int, pause_break: float, fits=None) -> list[Cue]:
    """Pull a stranded single word back into balance with the cue before it."""
    result = list(cues)

    for index in range(1, len(result)):
        previous, current = result[index - 1], result[index]

        if len(current.words) != 1 or len(previous.words) < max_words:
            continue

        boundary = previous.words[-1]
        if ends_sentence(boundary.text):
            continue
        if current.words[0].start - boundary.end > pause_break:
            continue
        moved = [boundary.text] + [w.text for w in current.words]
        if fits is not None and not fits(moved):
            continue

        result[index - 1] = Cue(words=previous.words[:-1])
        result[index] = Cue(words=(boundary,) + current.words)

    return result
