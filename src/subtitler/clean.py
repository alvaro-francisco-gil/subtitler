"""Turning an authored transcript into the words that were actually spoken.

The written document contains scaffolding the speaker never said aloud: a
title and Roman-numeral section headings. Feeding those to a forced aligner
would make it hunt for audio that does not exist and drag the surrounding
timings out of place.

Every surviving word keeps a pointer back to its line and column in the
original file, so the drift report can tell the user where to look in the
document they actually wrote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HEADING_RE = re.compile(r"^\s*[IVXLCDM]+\.\s+\S")

TRANSLATIONS = {
    "\u00ab": '"',   # «
    "\u00bb": '"',   # »
    "\u201c": '"',   # “
    "\u201d": '"',   # ”
    "\u2018": "'",   # ‘
    "\u2019": "'",   # ’
    "\u2014": "-",   # em dash
    "\u2013": "-",   # en dash
}

TITLE_TERMINATORS = (".", "!", "?", ":")


@dataclass(frozen=True)
class SourcePos:
    line: int
    column: int


@dataclass(frozen=True)
class CleanResult:
    text: str
    positions: list[SourcePos]


def is_heading(line: str) -> bool:
    return bool(HEADING_RE.match(line))


def normalise(text: str) -> str:
    for source, target in TRANSLATIONS.items():
        text = text.replace(source, target)
    return text.replace("…", "...")


def clean_transcript(raw: str) -> CleanResult:
    words: list[str] = []
    positions: list[SourcePos] = []
    seen_first_content_line = False

    for line_number, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue

        if is_heading(line):
            continue

        if not seen_first_content_line:
            seen_first_content_line = True
            # Rule 2: a leading line without terminal punctuation is the title.
            if not stripped.endswith(TITLE_TERMINATORS):
                continue

        for match in re.finditer(r"\S+", line):
            words.append(normalise(match.group()))
            positions.append(SourcePos(line=line_number, column=match.start() + 1))

    return CleanResult(text=" ".join(words), positions=positions)
