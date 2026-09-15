"""Dataclasses that cross stage boundaries.

Alignment, grouping, drift auditing and ASS generation all talk about words
and cues. Defining them once here keeps those stages speaking the same
vocabulary, and keeps `cues.json` a stable, renderer-agnostic contract.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float
    score: float = 1.0

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class Cue:
    words: tuple[Word, ...]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)
