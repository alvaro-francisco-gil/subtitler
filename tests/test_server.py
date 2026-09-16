import shutil

import pytest
from fastapi.testclient import TestClient

from talk_studio import sampling, tools
from talk_studio.project import Project
from talk_studio.server.app import create_app
from talk_studio.timecode import Excerpt


@pytest.fixture
def client_and_project(tiny_video, tmp_path):
    video = tmp_path / "talk.mp4"
    shutil.copyfile(tiny_video, video)
    project = Project.init(video, tmp_path / "proj")
    project.add_excerpt("audio", Excerpt(1.0, 3.0))
    chain = tools.get_tool("ffmpeg-chain")
    project.propose("audio", chain.name, chain.version, chain.settings({}), "agent")
    project.propose("audio", chain.name, chain.version, chain.settings({"denoise_db": 0}), "agent")
    sampling.render_round(project, "audio")
    return TestClient(create_app(project.root)), project


def test_index_is_served(client_and_project):
    client, _ = client_and_project
    response = client.get("/")
    assert response.status_code == 200 and "talk-studio" in response.text
    assert 'src="/static/review.js"' in response.text
    assert client.get("/static/review.js").status_code == 200
    assert client.get("/static/review.css").status_code == 200


def test_open_round_is_blind(client_and_project):
    client, _ = client_and_project
    response = client.get("/api/decisions/audio")
    body = response.json()
    assert [c["label"] for c in body["candidates"]] == ["A", "B"]
    assert "ffmpeg-chain" not in response.text and "c1" not in response.text
    assert set(body["loudness"]) == {"original", "A", "B"}
    assert all(isinstance(v, float) for v in body["loudness"]["A"])


def test_samples_are_served_by_label(client_and_project):
    client, _ = client_and_project
    for label in ("original", "A", "B"):
        response = client.get(f"/api/decisions/audio/samples/{label}/0.wav")
        assert response.status_code == 200 and response.content[:4] == b"RIFF"
    assert client.get("/api/decisions/audio/samples/C/0.wav").status_code == 404
    assert client.get("/api/decisions/audio/samples/A/5.wav").status_code == 404


def test_a_pick_reveals_and_is_saved(client_and_project):
    client, project = client_and_project
    response = client.post("/api/decisions/audio/feedback", json={"verdict": "pick", "label": "B", "note": "warmer"})
    assert response.status_code == 200
    revealed = {c["label"]: c for c in response.json()["revealed"]}
    assert revealed["B"]["settings"]["denoise_db"] == 0.0
    loaded = Project.load(project.root)
    assert (loaded.decision("audio").status, loaded.decision("audio").pick) == ("picked", "c2")
    history = client.get("/api/decisions/audio").json()["history"]
    assert history[0]["candidates"][1]["tool"] == "ffmpeg-chain"


def test_bad_feedback_is_a_400(client_and_project):
    client, _ = client_and_project
    response = client.post("/api/decisions/audio/feedback", json={"verdict": "pick", "label": "Z"})
    assert response.status_code == 400 and "no candidate Z" in response.json()["detail"]


def test_sample_here_adds_an_excerpt(client_and_project):
    client, project = client_and_project
    response = client.post("/api/decisions/audio/excerpts", json={"start": 3.0, "end": 5.0})
    assert response.status_code == 200
    assert [e["label"] for e in response.json()["excerpts"]] == ["0:01-0:03", "0:03-0:05"]
    assert client.get("/api/decisions/audio/samples/A/1.wav").status_code == 200


def test_unknown_decision_is_a_404(client_and_project):
    client, _ = client_and_project
    assert client.get("/api/decisions/thumbnail").status_code == 404


def test_a_failed_background_rerender_marks_pending_candidates_failed(client_and_project, monkeypatch):
    """A crash in render_round itself (not a per-candidate failure) must not
    leave the page stuck showing "rendering…" forever with no reason."""
    client, project = client_and_project

    # Reopen a fresh round so both candidates start "pending" again.
    chain = tools.get_tool("ffmpeg-chain")
    project.propose("audio", chain.name, chain.version, chain.settings({"denoise_db": 5}), "agent")

    def broken_render_round(project, name):
        raise RuntimeError("disk full")

    from talk_studio.server import app as app_module

    monkeypatch.setattr(app_module.sampling, "render_round", broken_render_round)

    # TestClient runs background tasks synchronously, so this drives `rerender`.
    response = client.post("/api/decisions/audio/excerpts", json={"start": 3.0, "end": 5.0})
    assert response.status_code == 200

    body = client.get("/api/decisions/audio").json()
    states = {c["label"]: c["state"] for c in body["candidates"]}
    assert states.get("C") == "failed"
    failed = next(c for c in body["candidates"] if c["label"] == "C")
    # Blind: a failed candidate in an open round says it failed, not why or
    # with what settings.
    assert failed["error"] == "This candidate failed to render."
    assert "id" not in failed and "tool" not in failed and "settings" not in failed
    assert "disk full" not in client.get("/api/decisions/audio").text


def test_a_failed_candidate_stays_blind_in_an_open_round(tiny_video, tmp_path, monkeypatch):
    video = tmp_path / "talk.mp4"
    shutil.copyfile(tiny_video, video)
    project = Project.init(video, tmp_path / "proj")
    project.add_excerpt("audio", Excerpt(1.0, 3.0))
    chain = tools.get_tool("ffmpeg-chain")
    project.propose("audio", chain.name, chain.version, chain.settings({}), "agent")
    project.propose("audio", chain.name, chain.version, chain.settings({"denoise_db": 0}), "agent")

    real_get_tool = tools.get_tool
    calls = {"n": 0}

    def flaky_get_tool(name):
        calls["n"] += 1
        if calls["n"] == 2:
            raise tools.ToolError("command failed (1): ffmpeg -af denoise_db=37 -i in.wav out.wav")
        return real_get_tool(name)

    monkeypatch.setattr(sampling.tools, "get_tool", flaky_get_tool)
    sampling.render_round(project, "audio")

    client = TestClient(create_app(project.root))
    response = client.get("/api/decisions/audio")
    assert response.status_code == 200
    body = response.json()
    states = {c["label"]: c["state"] for c in body["candidates"]}
    assert states == {"A": "rendered", "B": "failed"}
    failed = next(c for c in body["candidates"] if c["label"] == "B")
    assert failed["error"] and "id" not in failed and "tool" not in failed and "settings" not in failed
    text = response.text
    assert "ffmpeg" not in text and "denoise_db" not in text and "ffmpeg-chain" not in text and "c2" not in text


def test_mastering_waits_for_the_audio_pick(client_and_project):
    client, project = client_and_project
    project.add_excerpt("master", Excerpt(1.0, 3.0))
    master = tools.get_tool("voice-master")
    project.propose("master", master.name, master.version, master.settings({}), "agent")

    body = client.get("/api/decisions/master").json()
    assert body["reference"] == "Before mastering"
    assert "audio is open" in body["blocked"] and body["loudness"] == {}
    assert client.get("/api/decisions/master/samples/A/0.wav").status_code == 404
    assert client.get("/api/decisions/audio").json()["reference"] == "Original"


def test_a_grade_round_serves_frames_and_readiness(client_and_project):
    client, project = client_and_project
    project.set_excerpts("grade", [Excerpt(1.0, 2.0)])
    eq = tools.get_tool("ffmpeg-eq")
    project.propose("grade", eq.name, eq.version, eq.settings({"contrast": 1.3}), "agent")
    sampling.render_round(project, "grade")

    body = client.get("/api/decisions/grade").json()
    assert body["kind"] == "image" and body["loudness"] == {}
    assert body["ready"] == {"original": [True], "A": [True]}
    for label in ("original", "A"):
        response = client.get(f"/api/decisions/grade/samples/{label}/0.jpg")
        assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    assert client.get("/api/decisions/grade/samples/A/0.wav").status_code == 404
    assert client.get("/api/decisions/audio").json()["ready"]["A"] == [True]
