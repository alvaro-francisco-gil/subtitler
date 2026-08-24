import json
from pathlib import Path

import pytest

from subtitler import probe

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize(
    "width,height,rotation,expected",
    [
        (1920, 1080, 0, (1920, 1080)),
        (1920, 1080, 90, (1080, 1920)),
        (1920, 1080, -90, (1080, 1920)),
        (1920, 1080, 270, (1080, 1920)),
        (1920, 1080, 180, (1920, 1080)),
        (1920, 1080, -180, (1920, 1080)),
    ],
)
def test_display_dimensions(width, height, rotation, expected):
    assert probe.display_dimensions(width, height, rotation) == expected


def test_rotation_read_from_display_matrix_not_tags():
    stream = load("ffprobe_rotated.json")["streams"][0]
    assert probe.rotation_from_side_data(stream) == -90


def test_rotation_defaults_to_zero_without_display_matrix():
    stream = load("ffprobe_unrotated.json")["streams"][0]
    assert probe.rotation_from_side_data(stream) == 0


def test_parse_rotated_hdr_source():
    info = probe.parse_probe_json(load("ffprobe_rotated.json"))
    assert (info.stored_width, info.stored_height) == (1920, 1080)
    assert (info.display_width, info.display_height) == (1080, 1920)
    assert info.rotation == -90
    assert info.fps == pytest.approx(23.976, abs=0.001)
    assert info.duration == pytest.approx(691.135)
    assert info.is_hdr is True


def test_parse_plain_sdr_source():
    info = probe.parse_probe_json(load("ffprobe_unrotated.json"))
    assert (info.display_width, info.display_height) == (1920, 1080)
    assert info.rotation == 0
    assert info.is_hdr is False


def test_display_matrix_wins_over_a_contradicting_rotate_tag():
    stream = load("ffprobe_rotated.json")["streams"][0]
    # Tag says 90, Display Matrix says -90; Display Matrix should win
    assert probe.rotation_from_side_data(stream) == -90


def test_rotate_tag_without_display_matrix_is_ignored():
    stream = load("ffprobe_tag_only.json")["streams"][0]
    # Only has rotate tag, no Display Matrix; should return 0
    assert probe.rotation_from_side_data(stream) == 0
