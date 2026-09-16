import shutil
import wave
from concurrent.futures import ThreadPoolExecutor

import pytest

from talk_studio import cache, sampling, tools
from talk_studio.project import Project, ProjectError
from talk_studio.timecode import Excerpt


@pytest.fixture
def project(tiny_video, tmp_path):
    video = tmp_path / "talk.mp4"
    shutil.copyfile(tiny_video, video)
    project = Project.init(video, tmp_path / "proj")
    project.add_excerpt("audio", Excerpt(1.0, 3.0))
    return project


def test_sample_keys_ignore_dict_order_and_see_settings():
    a = cache.sample_key(fingerprint="f", tool="t", version="1", settings={"a": 1, "b": 2}, excerpt="0:01-0:03")
    b = cache.sample_key(fingerprint="f", tool="t", version="1", settings={"b": 2, "a": 1}, excerpt="0:01-0:03")
    c = cache.sample_key(fingerprint="f", tool="t", version="1", settings={"a": 1, "b": 3}, excerpt="0:01-0:03")
    assert a == b != c


def test_sample_key_is_sensitive_to_version_excerpt_and_fingerprint():
    base = dict(fingerprint="f", tool="t", version="1", settings={"a": 1}, excerpt="0:01-0:03")
    baseline = cache.sample_key(**base)
    assert cache.sample_key(**{**base, "version": "2"}) != baseline
    assert cache.sample_key(**{**base, "excerpt": "0:02-0:04"}) != baseline
    assert cache.sample_key(**{**base, "fingerprint": "g"}) != baseline


def test_cache_lives_under_the_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("TALK_STUDIO_CACHE", str(tmp_path / "c"))
    assert cache.project_dir(tmp_path / "proj").parent == tmp_path / "c"


def test_render_round_renders_original_and_candidates(project):
    chain = tools.get_tool("ffmpeg-chain")
    project.propose("audio", chain.name, chain.version, chain.settings({}), "test")
    project.propose("audio", chain.name, chain.version, chain.settings({"denoise_db": 0}), "test")

    report = sampling.render_round(project, "audio")

    assert report.rendered == ["c1", "c2"] and report.failed == []
    assert [c.state for c in project.candidates("audio")] == ["rendered", "rendered"]
    excerpt = project.decision("audio").excerpts[0]
    original = sampling.sample_file(project, "audio", tools.Original(), {}, excerpt)
    assert original.exists() and sampling.loudness(original) is not None


def test_render_round_is_a_cache_hit_the_second_time(project):
    chain = tools.get_tool("ffmpeg-chain")
    candidate = project.propose("audio", chain.name, chain.version, chain.settings({}), "test")
    sampling.render_round(project, "audio")
    path = sampling.sample_file(project, "audio", chain, candidate.settings, project.decision("audio").excerpts[0])
    first = path.stat().st_mtime_ns

    sampling.render_round(project, "audio")

    assert path.stat().st_mtime_ns == first


class Broken(tools.AudioTool):
    name = "broken"
    version = "1"

    def process(self, src, out, settings):
        raise tools.ToolError("it broke")


def test_a_failing_tool_is_marked_failed_with_a_log(project, monkeypatch):
    monkeypatch.setattr(tools, "get_tool", lambda name: Broken())
    project.propose("audio", "broken", "1", {}, "test")

    report = sampling.render_round(project, "audio")

    (candidate,) = project.candidates("audio")
    assert report.failed == ["c1"] and candidate.state == "failed"
    assert "it broke" in open(candidate.error).read()


def test_render_round_needs_excerpts_and_an_open_round(tiny_video, tmp_path):
    project = Project.init(tiny_video, tmp_path / "bare")
    with pytest.raises(ProjectError, match="no excerpts"):
        sampling.render_round(project, "audio")
    project.add_excerpt("audio", Excerpt(1.0, 2.0))
    with pytest.raises(ProjectError, match="no open round"):
        sampling.render_round(project, "audio")


def test_concurrent_ensure_sample_does_not_corrupt_the_cached_file(project):
    """Two writers racing on the same key must not share a partial path.

    The reachable case: the server's background re-render and a concurrent
    `talk-studio sample` both render the same candidate/excerpt at once. Each
    used to write to the same `<key>.partial.wav`, so one could validate or
    replace a file the other was still writing, corrupting the cached sample.
    """
    chain = tools.get_tool("ffmpeg-chain")
    settings = chain.settings({"denoise_db": 10})
    excerpt = project.decision("audio").excerpts[0]

    def run():
        return sampling.ensure_sample(project, "audio", chain, settings, excerpt)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        results = [f.result() for f in futures]

    for path in results:
        assert path == results[0]
    final_path = results[0]
    assert final_path.exists()
    with wave.open(str(final_path), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
    assert frames > 0
    assert frames / rate == pytest.approx(excerpt.duration, abs=0.1)
    # No writer-specific partial files were left behind.
    leftovers = list(final_path.parent.glob("*.partial.wav"))
    assert leftovers == []


def test_mastering_samples_need_an_audio_pick(project):
    project.add_excerpt("master", Excerpt(1.0, 3.0))
    master = tools.get_tool("voice-master")
    project.propose("master", master.name, master.version, master.settings({}), "test")
    with pytest.raises(ProjectError, match="audio is open"):
        sampling.render_round(project, "master")


def test_mastering_samples_follow_the_audio_pick(project):
    chain = tools.get_tool("ffmpeg-chain")
    for strength in (10, 30):
        project.propose("audio", chain.name, chain.version, chain.settings({"denoise_db": strength}), "test")
    sampling.render_round(project, "audio")
    project.record("audio", "pick", label="A")
    master = tools.get_tool("voice-master")
    excerpt = Excerpt(1.0, 3.0)
    first = sampling.sample_file(project, "master", master, master.settings({}), excerpt)

    project.reopen("audio")
    project.propose("audio", chain.name, chain.version, chain.settings({"denoise_db": 30}), "test")
    sampling.render_round(project, "audio")
    project.record("audio", "pick", label="A")

    assert sampling.sample_file(project, "master", master, master.settings({}), excerpt) != first


def test_working_audio_sits_at_the_working_level(project):
    chain = tools.get_tool("ffmpeg-chain")
    project.propose("audio", chain.name, chain.version, chain.settings({}), "test")
    sampling.render_round(project, "audio")
    project.record("audio", "pick", label="A")
    from talk_studio import media
    from talk_studio.tools.mastering import WORKING_LUFS
    assert media.integrated_loudness(sampling.working_audio(project, "master")) == pytest.approx(WORKING_LUFS, abs=0.5)
