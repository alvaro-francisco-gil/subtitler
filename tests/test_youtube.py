import pytest

from talk_studio import cli, youtube

DESCRIPTION = """A talk.

00:00 Introduction
01:28 From Prompt to Product
02:54 My path
"""


def write_metadata(tmp_path, description=DESCRIPTION, captions=True, title="From Prompt to Product - UPM"):
    (tmp_path / "thumb.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 100)
    (tmp_path / "captions.en.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n")
    text = f'''
[video]
title = "{title}"
description = """{description}"""
tags = ["AI", "software engineering"]
category = 28
language = "en"

[thumbnail]
path = "thumb.jpg"
'''
    if captions:
        text += '\n[[captions]]\npath = "captions.en.srt"\nlanguage = "en"\nname = "English"\n'
    path = tmp_path / "youtube.toml"
    path.write_text(text)
    return path


class Call:
    def __init__(self, log, name, kwargs, result):
        self.log, self.name, self.kwargs, self.result = log, name, kwargs, result

    def execute(self):
        self.log.append((self.name, self.kwargs))
        return self.result


class Resource:
    def __init__(self, api, resource):
        self.api, self.resource = api, resource

    def __getattr__(self, method):
        def call(**kwargs):
            key = f"{self.resource}.{method}"
            return Call(self.api.log, key, kwargs, self.api.results.get(key, {}))
        return call


class FakeApi:
    def __init__(self, results):
        self.log, self.results = [], results

    def __getattr__(self, resource):
        return lambda: Resource(self, resource)


def test_chapters_follow_youtube_rules():
    assert youtube.chapters(DESCRIPTION) == [0, 88, 174]
    assert youtube.check_chapters(DESCRIPTION) == []
    assert "0:00" in youtube.check_chapters("01:00 a\n02:00 b\n03:00 c")[0]
    assert any("three" in p for p in youtube.check_chapters("0:00 a\n1:00 b"))
    assert any("10 s" in p for p in youtube.check_chapters("0:00 a\n0:05 b\n1:00 c"))
    assert youtube.chapters("1:02:03 late") == [3723]


def test_metadata_loads_with_paths_beside_it(tmp_path):
    metadata = youtube.load_metadata(write_metadata(tmp_path))
    assert metadata.thumbnail == tmp_path / "thumb.jpg"
    assert metadata.captions[0].path == tmp_path / "captions.en.srt"
    assert metadata.category == 28 and metadata.tags == ["AI", "software engineering"]


def test_metadata_problems_are_named(tmp_path):
    path = write_metadata(tmp_path, description="0:00 a\n0:04 b\n1:00 c <b>", title="x" * 120)
    with pytest.raises(youtube.YouTubeError) as error:
        youtube.load_metadata(path)
    message = str(error.value)
    assert "title must be" in message and "< and >" in message and "10 s" in message


def test_apply_sets_details_thumbnail_and_adds_captions(tmp_path):
    api = FakeApi({"videos.list": {"items": [{"id": "vid"}]}, "captions.list": {"items": []}})
    done = youtube.apply(api, "vid", youtube.load_metadata(write_metadata(tmp_path)))

    names = [name for name, _ in api.log]
    assert names == ["videos.list", "videos.update", "thumbnails.set", "captions.list", "captions.insert"]
    snippet = api.log[1][1]["body"]["snippet"]
    assert snippet["categoryId"] == "28" and snippet["defaultAudioLanguage"] == "en"
    assert "status" not in api.log[1][1]["body"]  # apply never changes privacy
    assert api.log[4][1]["body"]["snippet"] == {"videoId": "vid", "language": "en", "name": "English", "isDraft": False}
    assert done[0].endswith("3 chapters, 2 tags")


def test_apply_replaces_its_own_caption_track_but_not_automatic_ones(tmp_path):
    api = FakeApi({
        "videos.list": {"items": [{"id": "vid"}]},
        "captions.list": {"items": [
            {"id": "asr", "snippet": {"language": "en", "name": "English", "trackKind": "asr"}},
            {"id": "mine", "snippet": {"language": "en", "name": "English", "trackKind": "standard"}},
        ]},
    })
    youtube.apply(api, "vid", youtube.load_metadata(write_metadata(tmp_path)))
    assert api.log[-1][0] == "captions.update" and api.log[-1][1]["body"]["id"] == "mine"


def test_apply_refuses_a_video_that_is_not_on_the_channel(tmp_path):
    api = FakeApi({"videos.list": {"items": []}})
    with pytest.raises(youtube.YouTubeError, match="no video"):
        youtube.apply(api, "nope", youtube.load_metadata(write_metadata(tmp_path, captions=False)))


def test_latest_uploads_read_the_uploads_playlist():
    api = FakeApi({
        "channels.list": {"items": [{"snippet": {"title": "Álvaro"}, "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]},
        "playlistItems.list": {"items": [{
            "snippet": {"resourceId": {"videoId": "vid"}, "title": "PXL_2026", "publishedAt": "2026-09-16T10:00:00Z"},
            "status": {"privacyStatus": "private"},
        }]},
    })
    assert youtube.latest_uploads(api) == [{"id": "vid", "title": "PXL_2026", "published": "2026-09-16T10:00:00Z", "privacy": "private"}]
    assert api.log[1][1]["playlistId"] == "UU1"


def test_set_privacy_only_accepts_youtube_values():
    api = FakeApi({})
    youtube.set_privacy(api, "vid", "public")
    assert api.log[0][1]["body"]["status"]["privacyStatus"] == "public"
    with pytest.raises(youtube.YouTubeError):
        youtube.set_privacy(api, "vid", "friends")


def test_cli_check_and_missing_sign_in(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TALK_STUDIO_CONFIG", str(tmp_path / "config"))
    path = write_metadata(tmp_path)
    assert cli.main(["youtube", "check", str(path)]) == 0
    assert "3 chapters, 1 caption tracks" in capsys.readouterr().out
    assert cli.main(["youtube", "latest"]) == 1
    assert "not signed in" in capsys.readouterr().err


def test_schedule_sends_publish_at_in_utc_and_refuses_past_or_naive_times():
    import datetime
    api = FakeApi({})
    now = datetime.datetime(2026, 9, 17, tzinfo=datetime.timezone.utc)
    at = youtube.schedule(api, "vid", "2026-09-18T18:00+02:00", now=now)
    status = api.log[0][1]["body"]["status"]
    assert status == {"privacyStatus": "private", "publishAt": "2026-09-18T16:00:00Z", "selfDeclaredMadeForKids": False}
    assert at.hour == 16
    for bad in ("2026-09-18T18:00", "2026-09-16T18:00+02:00", "tomorrow"):
        with pytest.raises(youtube.YouTubeError):
            youtube.schedule(api, "vid", bad, now=now)
