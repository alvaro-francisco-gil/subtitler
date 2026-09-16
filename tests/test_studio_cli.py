import json
import shutil

import pytest

from talk_studio import cli
from talk_studio.project import Project


def test_help_lists_captions(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0
    assert "captions" in capsys.readouterr().out


def test_captions_delegates_to_the_caption_cli(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["captions", "--help"])
    assert exit_info.value.code == 0
    assert "talk-studio captions" in capsys.readouterr().out


def run(argv, capsys):
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def project_dir(tiny_video, tmp_path, capsys):
    video = tmp_path / "talk.mp4"
    shutil.copyfile(tiny_video, video)
    root = tmp_path / "proj"
    assert run(["init", str(video), "--project", str(root)], capsys)[0] == 0
    return root


def test_init_creates_a_project(project_dir):
    assert Project.load(project_dir).decision("audio").status == "open"


def test_tools_json_lists_settings(capsys):
    code, out, _ = run(["tools", "--json"], capsys)
    names = {tool["name"]: tool for tool in json.loads(out)}
    assert code == 0 and {"ffmpeg-chain", "deepfilternet"} <= set(names)
    assert any(p["name"] == "denoise_db" for p in names["ffmpeg-chain"]["params"])


def test_propose_rejects_bad_settings(project_dir, capsys):
    code, _, err = run(["propose", "audio", "--tool", "ffmpeg-chain", "--set", "denoise_db=99", "--project", str(project_dir)], capsys)
    assert code == 1 and "between" in err


def test_propose_sample_status_feedback(project_dir, capsys):
    p = ["--project", str(project_dir)]
    assert run(["excerpts", "add", "audio", "0:01-0:03", *p], capsys)[0] == 0
    assert run(["propose", "audio", "--tool", "ffmpeg-chain", *p], capsys)[0] == 0
    assert run(["propose", "audio", "--tool", "ffmpeg-chain", "--set", "denoise_db=0", *p], capsys)[0] == 0
    code, out, _ = run(["sample", "audio", "--json", *p], capsys)
    assert code == 0 and json.loads(out)["rendered"] == ["c1", "c2"]

    code, out, _ = run(["status", "--json", *p], capsys)
    audio = json.loads(out)["decisions"]["audio"]
    assert audio["open_round"] == 1 and audio["candidates"] == {"rendered": 2}

    Project.load(project_dir).record("audio", "reject", note="both too dry")
    code, out, _ = run(["feedback", "audio", "--json", *p], capsys)
    rounds = json.loads(out)["rounds"]
    assert rounds[0]["verdicts"][0]["note"] == "both too dry"
    assert rounds[0]["candidates"][1]["settings"]["denoise_db"] == 0.0


def test_wait_times_out_without_new_feedback(project_dir, capsys):
    code, _, err = run(["wait", "audio", "--timeout", "0.3", "--interval", "0.1", "--project", str(project_dir)], capsys)
    assert code == 2 and "timed out" in err


def test_render_without_a_pick_is_an_error(project_dir, capsys):
    code, _, err = run(["render", "--project", str(project_dir)], capsys)
    assert code == 1 and "pick a candidate" in err


def test_doctor_reports_ffmpeg(capsys):
    code, out, _ = run(["doctor"], capsys)
    assert code == 0 and "ffmpeg" in out


def test_propose_refuses_a_tool_of_another_kind(project_dir, capsys):
    code, _, err = run(["propose", "master", "--tool", "deepfilternet", "--project", str(project_dir)], capsys)
    assert code == 1 and "deepfilternet is an audio tool" in err and "voice-master" in err
