"""Reading the metadata the rest of the pipeline depends on.

Two details of the reference video drive this module:

* Rotation lives in stream side data as a Display Matrix entry, not in
  `stream_tags.rotate`. Reading the tag would silently report 0.
* ffmpeg auto-rotates on decode, so the burn-in sees an upright frame. What
  the pipeline needs from rotation is the *display* resolution, used for the
  ASS canvas and all layout maths. No transpose is ever applied.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from . import binaries

# Transfer functions that indicate an HDR source needing tone-mapping.
HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}


@dataclass(frozen=True)
class MediaInfo:
    stored_width: int
    stored_height: int
    rotation: int
    display_width: int
    display_height: int
    fps: float
    duration: float
    color_transfer: str
    color_primaries: str
    is_hdr: bool


def display_dimensions(width: int, height: int, rotation: int) -> tuple[int, int]:
    """Apply a rotation to stored dimensions to get displayed dimensions."""
    if abs(rotation) % 180 == 90:
        return height, width
    return width, height


def rotation_from_side_data(stream: dict) -> int:
    """Extract rotation from a Display Matrix side-data entry, or 0 if absent."""
    for entry in stream.get("side_data_list", []):
        if entry.get("side_data_type") == "Display Matrix":
            return int(entry.get("rotation", 0))
    return 0


def parse_probe_json(payload: dict) -> MediaInfo:
    video = next(s for s in payload["streams"] if s.get("codec_type") == "video")

    stored_width = int(video["width"])
    stored_height = int(video["height"])
    rotation = rotation_from_side_data(video)
    display_width, display_height = display_dimensions(stored_width, stored_height, rotation)

    fps = float(Fraction(video.get("r_frame_rate", "0/1")))
    duration = float(video.get("duration") or payload["format"]["duration"])

    transfer = video.get("color_transfer", "")
    primaries = video.get("color_primaries", "")

    return MediaInfo(
        stored_width=stored_width,
        stored_height=stored_height,
        rotation=rotation,
        display_width=display_width,
        display_height=display_height,
        fps=fps,
        duration=duration,
        color_transfer=transfer,
        color_primaries=primaries,
        is_hdr=transfer in HDR_TRANSFERS,
    )


def probe(path: Path) -> MediaInfo:
    result = binaries.run([
        binaries.ffprobe(),
        "-v", "error",
        "-show_streams",
        "-show_format",
        "-of", "json",
        str(path),
    ])
    return parse_probe_json(json.loads(result.stdout))
