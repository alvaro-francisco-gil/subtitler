import shutil

from fastapi.testclient import TestClient

from talk_studio import cli, media
from talk_studio.server.app import create_app


def test_audio_loop_end_to_end(tiny_video, tmp_path):
    video = tmp_path / "talk.mp4"
    shutil.copyfile(tiny_video, video)
    root = tmp_path / "proj"
    p = ["--project", str(root)]

    assert cli.main(["init", str(video), *p]) == 0
    assert cli.main(["excerpts", "add", "audio", "0:01-0:04", *p]) == 0
    assert cli.main(["propose", "audio", "--tool", "ffmpeg-chain", *p]) == 0
    assert cli.main(["propose", "audio", "--tool", "ffmpeg-chain", "--set", "denoise_db=0", *p]) == 0
    assert cli.main(["sample", "audio", *p]) == 0

    client = TestClient(create_app(root))
    assert client.post("/api/decisions/audio/feedback", json={"verdict": "pick", "label": "B"}).status_code == 200

    out = tmp_path / "final.mp4"
    assert cli.main(["render", "--out", str(out), *p]) == 0
    media.validate(out, expected=media.duration(video), tolerance=0.05, streams=("video", "audio"))
