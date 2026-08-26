"""Measuring rendered text width.

ASS positions each word individually with `\\pos`, so the generator needs to
know how wide each word will be in order to centre a cue as a group. Pillow
measures with the same font file libass will use — but *not*, by default, at
the same size.

libass does not treat `Fontsize` as pixels-per-em. It treats it as the height
of the font's Windows line box, so the em it actually renders at is

    Fontsize * unitsPerEm / (usWinAscent + usWinDescent)

For Montserrat that ratio is 0.64, so a `Fontsize: 96` style renders at a 61px
em. Measuring at 96 and positioning at 61 leaves the difference as dead space
between words, which is exactly the "too much space" this scaling fixes.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont


@lru_cache(maxsize=None)
def libass_size_ratio(font_path: Path) -> float:
    """The em size libass renders at, per unit of ASS `Fontsize`."""
    tables = _read_tables(font_path)
    units_per_em = struct.unpack(">H", tables["head"][18:20])[0]
    win_ascent, win_descent = struct.unpack(">HH", tables["OS/2"][74:78])
    line_box = win_ascent + win_descent
    if not units_per_em or not line_box:
        return 1.0
    return units_per_em / line_box


def text_measurer(font_path: Path, font_size: int) -> Callable[[str], float]:
    pixels_per_em = font_size * libass_size_ratio(font_path)
    font = ImageFont.truetype(str(font_path), pixels_per_em)

    def measure(text: str) -> float:
        return float(font.getlength(text))

    return measure


def _read_tables(font_path: Path) -> dict[str, bytes]:
    """Read the sfnt table directory, returning the tables layout needs.

    Two integers do not justify a font-toolkit dependency, and the sfnt
    directory is a fixed-width record format that has not changed since 1991.
    """
    data = Path(font_path).read_bytes()
    (table_count,) = struct.unpack(">H", data[4:6])
    wanted = {"head", "OS/2"}
    tables: dict[str, bytes] = {}
    for index in range(table_count):
        offset = 12 + index * 16
        tag = data[offset : offset + 4].decode("latin-1")
        if tag not in wanted:
            continue
        start, length = struct.unpack(">II", data[offset + 8 : offset + 16])
        tables[tag] = data[start : start + length]
    missing = wanted - tables.keys()
    if missing:
        raise ValueError(f"{font_path} is missing the {sorted(missing)} table(s)")
    return tables
