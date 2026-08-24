"""Measuring rendered text width.

ASS positions each word individually, so the generator needs to know how wide
each word will be in order to centre a cue as a group. Pillow measures with
the same font file libass will use, which is close enough for layout.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PIL import ImageFont


def text_measurer(font_path: Path, font_size: int) -> Callable[[str], float]:
    font = ImageFont.truetype(str(font_path), font_size)

    def measure(text: str) -> float:
        return float(font.getlength(text))

    return measure
