from subtitler import workdir


def test_for_video_creates_named_directory(tmp_path):
    video = tmp_path / "pregon_matamala.mov"
    video.write_bytes(b"")

    wd = workdir.for_video(video)

    assert wd.root == tmp_path / ".subtitler" / "pregon_matamala"
    assert wd.root.is_dir()


def test_artifact_paths_live_in_the_root(tmp_path):
    video = tmp_path / "clip.mov"
    video.write_bytes(b"")

    wd = workdir.for_video(video)

    assert wd.audio == wd.root / "audio.wav"
    assert wd.words == wd.root / "words.json"
    assert wd.ass == wd.root / "subs.ass"
    assert wd.drift == wd.root / "drift.md"


def test_is_fresh_false_when_artifact_missing(tmp_path):
    source = tmp_path / "s.txt"
    source.write_text("x")
    assert workdir.is_fresh(tmp_path / "nope.json", source) is False


def test_is_fresh_false_when_source_is_newer(tmp_path):
    artifact = tmp_path / "a.json"
    artifact.write_text("x")
    source = tmp_path / "s.txt"
    source.write_text("y")
    import os
    os.utime(artifact, (1, 1))
    assert workdir.is_fresh(artifact, source) is False


def test_is_fresh_true_when_artifact_is_newer(tmp_path):
    source = tmp_path / "s.txt"
    source.write_text("y")
    artifact = tmp_path / "a.json"
    artifact.write_text("x")
    import os
    os.utime(source, (1, 1))
    assert workdir.is_fresh(artifact, source) is True
