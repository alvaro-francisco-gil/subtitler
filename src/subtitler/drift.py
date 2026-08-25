"""Auditing where the audio and the script disagree.

The subtitles say what the transcript says. That is the user's choice, and
this module exists because it is sometimes the wrong one: a live speaker
improvises, repeats, and skips lines. Rather than silently repairing the
alignment, the pipeline reports the places where the scripted text and the
audio pulled apart, with timecodes to jump to and transcript line numbers to
edit.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from .models import Word


@dataclass(frozen=True)
class Flag:
    kind: str
    start: float
    end: float
    text: str
    line: int = 0


def _timecode(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


def _line_for(index: int, positions) -> int:
    if not positions or index >= len(positions):
        return 0
    return positions[index].line


def find_drift(
    words: list[Word],
    *,
    score_threshold: float = 0.5,
    run_length: int = 3,
    gap_threshold: float = 2.0,
    duration_factor: float = 3.0,
    positions=None,
) -> list[Flag]:
    flags: list[Flag] = []
    if not words:
        return flags

    # 1. Runs of low-confidence words.
    run_start = None
    for index, word in enumerate(words):
        if word.score < score_threshold:
            if run_start is None:
                run_start = index
            continue
        if run_start is not None and index - run_start >= run_length:
            flags.append(_run_flag(words, run_start, index - 1, positions))
        run_start = None
    if run_start is not None and len(words) - run_start >= run_length:
        flags.append(_run_flag(words, run_start, len(words) - 1, positions))

    # 2. Silences inside supposedly continuous speech.
    for index, (before, after) in enumerate(zip(words, words[1:])):
        if after.start - before.end > gap_threshold:
            flags.append(
                Flag(
                    kind="gap",
                    start=before.end,
                    end=after.start,
                    text=f"{before.text} | {after.text}",
                    line=_line_for(index, positions),
                )
            )

    # 3. Words stretched across audio the aligner could not match.
    median = statistics.median(word.duration for word in words)
    if median > 0:
        for index, word in enumerate(words):
            if word.duration > median * duration_factor:
                flags.append(
                    Flag(
                        kind="long-word",
                        start=word.start,
                        end=word.end,
                        text=word.text,
                        line=_line_for(index, positions),
                    )
                )

    return sorted(flags, key=lambda flag: flag.start)


def _run_flag(words: list[Word], first: int, last: int, positions) -> Flag:
    return Flag(
        kind="low-confidence",
        start=words[first].start,
        end=words[last].end,
        text=" ".join(word.text for word in words[first : last + 1]),
        line=_line_for(first, positions),
    )


def render_report(flags: list[Flag], total_words: int) -> str:
    lines = [
        "# Drift report",
        "",
        f"Aligned {total_words} words from the transcript.",
        "",
    ]

    if not flags:
        lines += [
            "No drift detected. The audio follows the transcript closely.",
            "",
        ]
        return "\n".join(lines)

    lines += [
        f"{len(flags)} span(s) where the audio and the transcript may disagree.",
        "Jump to each timecode in the video and check before rendering.",
        "",
        "| Time | Kind | Transcript | Text |",
        "| --- | --- | --- | --- |",
    ]

    for flag in flags:
        location = f"line {flag.line}" if flag.line else "-"
        text = flag.text.replace("|", "\\|")
        lines.append(
            f"| {_timecode(flag.start)}-{_timecode(flag.end)} | {flag.kind} | {location} | {text} |"
        )

    lines.append("")
    return "\n".join(lines)
