"""Look-and-feel configuration.

Everything visual is here or in `style.toml`, so iterating on the look never
means editing Python. ASS stores colours as `&HAABBGGRR` — alpha first, then
blue, green, red — which is the reverse byte order of the `#RRGGBB` a person
would write, so the conversion is explicit and tested.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_STYLE_PATH = Path(__file__).resolve().parents[2] / "style.toml"

HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


@dataclass(frozen=True)
class Style:
    font_family: str
    font_path: Path
    font_size: int
    fill: str
    highlight: str
    outline: str
    position: float
    max_width: float
    word_spacing: float
    outline_width: float
    shadow_depth: float
    all_caps: bool
    pop_scale: float
    pop_ms: int
    max_words: int
    pause_break: float


def ass_colour(hex_colour: str) -> str:
    """Convert `#RRGGBB` to the ASS `&HAABBGGRR` form, fully opaque."""
    match = HEX_RE.match(hex_colour.strip())
    if not match:
        raise ValueError(f"expected a #RRGGBB colour, got {hex_colour!r}")
    red, green, blue = (match.group(1)[i : i + 2] for i in (0, 2, 4))
    return f"&H00{blue}{green}{red}".upper()


def load(path: Path = DEFAULT_STYLE_PATH) -> Style:
    data = tomllib.loads(Path(path).read_text())
    font_file = Path(data["font"]["file"])
    if not font_file.is_absolute():
        font_file = Path(path).resolve().parent / font_file

    return Style(
        font_family=data["font"]["family"],
        font_path=font_file,
        font_size=int(data["font"]["size"]),
        fill=data["colour"]["fill"],
        highlight=data["colour"]["highlight"],
        outline=data["colour"]["outline"],
        position=float(data["layout"]["position"]),
        max_width=float(data["layout"]["max_width"]),
        word_spacing=float(data["layout"]["word_spacing"]),
        outline_width=float(data["layout"]["outline_width"]),
        shadow_depth=float(data["layout"]["shadow_depth"]),
        all_caps=bool(data["layout"]["all_caps"]),
        pop_scale=float(data["animation"]["pop_scale"]),
        pop_ms=int(data["animation"]["pop_ms"]),
        max_words=int(data["cues"]["max_words"]),
        pause_break=float(data["cues"]["pause_break"]),
    )
