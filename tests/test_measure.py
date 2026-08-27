"""Text measurement must match the size libass actually renders at."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from PIL import ImageFont

from subtitler import measure

FONT = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "Montserrat-ExtraBold.ttf"


def test_ratio_is_units_per_em_over_the_windows_line_box():
    tables = measure._read_tables(FONT)
    units_per_em = struct.unpack(">H", tables["head"][18:20])[0]
    ascent, descent = struct.unpack(">HH", tables["OS/2"][74:78])

    assert measure.libass_size_ratio(FONT) == pytest.approx(units_per_em / (ascent + descent))


def test_montserrat_renders_well_below_its_nominal_size():
    # The regression this guards: measuring at 96 while libass renders at 61
    # leaves the difference as dead space between words.
    assert measure.libass_size_ratio(FONT) == pytest.approx(0.64, abs=0.01)


def test_measurer_measures_at_the_scaled_size_not_the_nominal_one():
    scaled = measure.text_measurer(FONT, 96)("PUEBLOS")
    nominal = ImageFont.truetype(str(FONT), 96).getlength("PUEBLOS")

    assert scaled == pytest.approx(nominal * measure.libass_size_ratio(FONT), rel=0.02)
    assert scaled < nominal


def test_missing_tables_are_reported(tmp_path):
    stub = tmp_path / "empty.ttf"
    stub.write_bytes(b"\x00\x01\x00\x00" + struct.pack(">H", 0) + b"\x00" * 6)

    with pytest.raises(ValueError, match="OS/2"):
        measure.libass_size_ratio(stub)
