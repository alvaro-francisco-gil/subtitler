"""Timecodes and excerpt ranges as people write them: `90`, `1:30`, `1:02:03`, `3:00-3:12`."""

from __future__ import annotations

import re
from dataclasses import dataclass

NUMBER_RE = re.compile(r"-?\d+(\.\d+)?")


def parse_timecode(value: str) -> float:
    parts = value.strip().split(":")
    if len(parts) > 3 or not all(NUMBER_RE.fullmatch(part.strip()) for part in parts):
        raise ValueError(f"could not parse timecode {value!r}")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    if seconds < 0 or value.strip().startswith("-"):
        raise ValueError(f"timecode {value!r} must not be negative")
    return seconds


def format_timecode(seconds: float) -> str:
    seconds = round(seconds, 1)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    sec_text = f"{int(secs):02d}" if secs == int(secs) else f"{secs:04.1f}"
    if hours:
        return f"{int(hours)}:{int(minutes):02d}:{sec_text}"
    return f"{int(minutes)}:{sec_text}"


@dataclass(frozen=True)
class Excerpt:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    def __str__(self) -> str:
        return f"{format_timecode(self.start)}-{format_timecode(self.end)}"


def parse_excerpt(value: str) -> Excerpt:
    start_text, sep, end_text = value.partition("-")
    if not sep:
        raise ValueError(f"excerpt {value!r} must look like 3:00-3:12")
    start, end = parse_timecode(start_text), parse_timecode(end_text)
    if end <= start:
        raise ValueError(f"excerpt {value!r} must end after it starts")
    return Excerpt(start, end)
