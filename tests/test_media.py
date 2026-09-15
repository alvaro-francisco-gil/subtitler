import shutil

import pytest

from talk_studio import binaries, media
from conftest import make_media


def test_fingerprint_is_stable_and_sees_content_changes(tiny_video, tmp_path):
    copy = tmp_path / "copy.mp4"
    shutil.copyfile(tiny_video, copy)
    assert media.fingerprint(copy) == media.fingerprint(tiny_video)

    # Flip a byte in the middle (frame data). The last bytes of an MP4 are
    # usually its index, and corrupting that would break ffprobe instead.
    data = bytearray(copy.read_bytes())
    data[len(data) // 2] ^= 0xFF
    copy.write_bytes(bytes(data))
    assert media.fingerprint(copy) != media.fingerprint(tiny_video)


def test_stream_durations(tiny_video):
    durations = media.stream_durations(tiny_video)
    assert set(durations) == {"video", "audio"}
    assert durations["video"] == pytest.approx(6.0, abs=0.1)
    assert media.duration(tiny_video) == pytest.approx(6.0, abs=0.1)


def test_extract_wav_window(tiny_video, tmp_path):
    out = media.extract_wav(tiny_video, tmp_path / "cut.wav", start=1.0, length=2.0)
    assert media.stream_durations(out) == {"audio": pytest.approx(2.0, abs=0.02)}
    rate = binaries.run([
        binaries.ffprobe(), "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate", "-of", "csv=p=0", str(out),
    ]).stdout.strip()
    assert rate == "48000"


def test_louder_file_measures_louder(tmp_path):
    quiet = make_media(tmp_path / "quiet.wav", seconds=4.0, video=False)
    loud = tmp_path / "loud.wav"
    binaries.run([binaries.ffmpeg(), "-y", "-v", "error", "-i", str(quiet), "-af", "volume=6dB", str(loud)])
    assert -60.0 < media.integrated_loudness(quiet) < 0.0
    assert media.integrated_loudness(loud) == pytest.approx(media.integrated_loudness(quiet) + 6.0, abs=0.5)


def test_validate_accepts_a_whole_render(tiny_video):
    media.validate(tiny_video, expected=6.0, tolerance=0.1, streams=("video", "audio"))


def test_validate_rejects_a_short_render(tiny_video):
    with pytest.raises(media.RenderError, match="expected 8.000"):
        media.validate(tiny_video, expected=8.0, tolerance=0.1, streams=("audio",))


def test_validate_rejects_a_missing_stream(tmp_path):
    wav = make_media(tmp_path / "a.wav", seconds=2.0, video=False)
    with pytest.raises(media.RenderError, match="no video stream"):
        media.validate(wav, expected=2.0, tolerance=0.1, streams=("video", "audio"))


def test_validate_rejects_audio_video_drift(tmp_path):
    # Create file with video and audio durations that differ by more than tolerance,
    # but each is within tolerance of the expected duration.
    # video ~6.08s (diff 0.08, within 0.1), audio ~5.97s (diff 0.03, within 0.1),
    # but drift 0.11s which exceeds tolerance 0.1s
    out = tmp_path / "drift.mp4"
    binaries.run([
        binaries.ffmpeg(), "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=6.08",
        "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=5.97",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-map", "0:v", "-map", "1:a", str(out)
    ])

    with pytest.raises(media.RenderError, match="differ by more than"):
        media.validate(out, expected=6.0, tolerance=0.1, streams=("video", "audio"))
