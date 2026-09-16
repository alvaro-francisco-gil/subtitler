"""Publishing to YouTube from a text file of metadata.

The video file itself is uploaded by hand in YouTube Studio: the API locks
every video uploaded by an unaudited Google Cloud project to private, for good.
Everything after the upload is done here — details, chapters, thumbnail,
caption tracks, and the final switch to public — so the metadata lives beside
the talk as text and can be re-applied.

A metadata file (`youtube.toml`), with paths relative to it:

    [video]
    title = "..."
    description = \"\"\"...\"\"\"
    tags = ["..."]
    category = 28          # Science & Technology
    language = "en"

    [thumbnail]
    path = "thumbnail/thumbnail.jpg"

    [[captions]]
    path = "captions.en.srt"
    language = "en"
    name = "English"
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
CHAPTER_RE = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})\s+\S", re.MULTILINE)
TITLE_LIMIT = 100
DESCRIPTION_LIMIT = 5000
TAGS_LIMIT = 500


class YouTubeError(Exception):
    """Missing credentials, an invalid metadata file, or a refused API call."""


def config_dir() -> Path:
    override = os.environ.get("TALK_STUDIO_CONFIG")
    return Path(override) if override else Path.home() / ".config" / "talk-studio"


def client_secret_path() -> Path:
    return config_dir() / "youtube" / "client_secret.json"


def token_path() -> Path:
    return config_dir() / "youtube" / "token.json"


# metadata


@dataclass
class Caption:
    path: Path
    language: str
    name: str


@dataclass
class Metadata:
    title: str
    description: str
    tags: list[str]
    category: int
    language: str
    thumbnail: Path | None = None
    captions: list[Caption] = field(default_factory=list)


def chapters(description: str) -> list[int]:
    """Chapter start times in seconds, as YouTube reads them from a description."""
    return [int(h or 0) * 3600 + int(m) * 60 + int(s) for h, m, s in CHAPTER_RE.findall(description)]


def check_chapters(description: str) -> list[str]:
    """Why YouTube would ignore the chapters, or nothing when it will show them."""
    starts = chapters(description)
    if not starts:
        return []
    problems = []
    if starts[0] != 0:
        problems.append("the first chapter must start at 0:00")
    if len(starts) < 3:
        problems.append("YouTube needs at least three chapters")
    for before, after in zip(starts, starts[1:]):
        if after - before < 10:
            problems.append(f"chapters at {before}s and {after}s are under 10 s apart, or out of order")
    return problems


def load_metadata(path: Path) -> Metadata:
    path = Path(path)
    if not path.exists():
        raise YouTubeError(f"{path} not found")
    data = tomllib.loads(path.read_text())
    base = path.parent
    video = data.get("video", {})
    try:
        metadata = Metadata(
            title=video["title"],
            description=video["description"].strip(),
            tags=list(video.get("tags", [])),
            category=int(video.get("category", 28)),
            language=video.get("language", "en"),
            thumbnail=base / data["thumbnail"]["path"] if "thumbnail" in data else None,
            captions=[Caption(base / c["path"], c["language"], c.get("name", "")) for c in data.get("captions", [])],
        )
    except KeyError as error:
        raise YouTubeError(f"{path}: missing {error.args[0]}") from None

    problems = []
    if not 0 < len(metadata.title) <= TITLE_LIMIT:
        problems.append(f"title must be 1–{TITLE_LIMIT} characters, is {len(metadata.title)}")
    if "<" in metadata.title or ">" in metadata.title or "<" in metadata.description or ">" in metadata.description:
        problems.append("YouTube rejects < and > in titles and descriptions")
    if len(metadata.description.encode()) > DESCRIPTION_LIMIT:
        problems.append(f"description is over {DESCRIPTION_LIMIT} bytes")
    if sum(len(t) + (2 if " " in t else 0) + 1 for t in metadata.tags) > TAGS_LIMIT:
        problems.append(f"tags are over {TAGS_LIMIT} characters together")
    problems += check_chapters(metadata.description)
    for file in [metadata.thumbnail, *[c.path for c in metadata.captions]]:
        if file is not None and not file.exists():
            problems.append(f"{file} not found")
    if metadata.thumbnail is not None and metadata.thumbnail.exists() and metadata.thumbnail.stat().st_size > 2 * 1024 * 1024:
        problems.append("the thumbnail is over YouTube's 2 MB limit")
    if problems:
        raise YouTubeError(f"{path}: " + "; ".join(problems))
    return metadata


# the API


def authorize(open_browser: bool = False, port: int = 8766):
    """Run the consent flow once and keep the token; later calls refresh it silently."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    secret = client_secret_path()
    if not secret.exists():
        raise YouTubeError(
            f"no OAuth client at {secret}. Create a Desktop OAuth client in Google Cloud "
            f"(YouTube Data API v3 enabled) and save its JSON there."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    credentials = flow.run_local_server(
        port=port, open_browser=open_browser,
        authorization_prompt_message="Open this URL in the browser signed in to the channel's Google account:\n{url}",
        success_message="talk-studio can now publish to this channel. You can close this tab.",
    )
    token_path().parent.mkdir(parents=True, exist_ok=True)
    token_path().write_text(credentials.to_json())
    token_path().chmod(0o600)
    return credentials


def service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not token_path().exists():
        raise YouTubeError("not signed in; run `talk-studio youtube auth` first")
    credentials = Credentials.from_authorized_user_file(str(token_path()), SCOPES)
    if not credentials.valid:
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            token_path().write_text(credentials.to_json())
        else:
            raise YouTubeError("the saved sign-in no longer works; run `talk-studio youtube auth` again")
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def _call(request):
    from googleapiclient.errors import HttpError

    try:
        return request.execute()
    except HttpError as error:
        raise YouTubeError(f"YouTube refused the request ({error.status_code}): {error.reason}") from None


def channel(api) -> dict:
    items = _call(api.channels().list(part="snippet,contentDetails", mine=True)).get("items", [])
    if not items:
        raise YouTubeError("the signed-in account has no YouTube channel")
    return items[0]


def latest_uploads(api, count: int = 5) -> list[dict]:
    """The channel's most recent uploads, newest first, drafts and private videos included."""
    uploads = channel(api)["contentDetails"]["relatedPlaylists"]["uploads"]
    items = _call(api.playlistItems().list(part="snippet,status", playlistId=uploads, maxResults=count)).get("items", [])
    return [
        {
            "id": item["snippet"]["resourceId"]["videoId"],
            "title": item["snippet"]["title"],
            "published": item["snippet"]["publishedAt"],
            "privacy": item.get("status", {}).get("privacyStatus", ""),
        }
        for item in items
    ]


def apply(api, video_id: str, metadata: Metadata) -> list[str]:
    """Set details, thumbnail and captions on an uploaded video. Leaves privacy alone."""
    from googleapiclient.http import MediaFileUpload

    done = []
    current = _call(api.videos().list(part="snippet", id=video_id)).get("items", [])
    if not current:
        raise YouTubeError(f"no video {video_id} on the signed-in channel")
    _call(api.videos().update(part="snippet", body={
        "id": video_id,
        "snippet": {
            "title": metadata.title,
            "description": metadata.description,
            "tags": metadata.tags,
            "categoryId": str(metadata.category),
            "defaultLanguage": metadata.language,
            "defaultAudioLanguage": metadata.language,
        },
    }))
    done.append(f"details: {metadata.title!r}, {len(chapters(metadata.description))} chapters, {len(metadata.tags)} tags")

    if metadata.thumbnail is not None:
        _call(api.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(metadata.thumbnail), mimetype="image/jpeg")))
        done.append(f"thumbnail: {metadata.thumbnail.name}")

    existing = _call(api.captions().list(part="snippet", videoId=video_id)).get("items", [])
    for caption in metadata.captions:
        media = MediaFileUpload(str(caption.path), mimetype="application/octet-stream")
        match = next(
            (c for c in existing if c["snippet"]["language"] == caption.language and c["snippet"]["name"] == caption.name
             and c["snippet"].get("trackKind") != "asr"),
            None,
        )
        if match:
            _call(api.captions().update(part="snippet", body={"id": match["id"], "snippet": {"isDraft": False}}, media_body=media))
            done.append(f"captions: replaced {caption.language} {caption.name!r}")
        else:
            _call(api.captions().insert(part="snippet", body={"snippet": {
                "videoId": video_id, "language": caption.language, "name": caption.name, "isDraft": False,
            }}, media_body=media))
            done.append(f"captions: added {caption.language} {caption.name!r}")
    return done


def set_privacy(api, video_id: str, privacy: str) -> None:
    if privacy not in ("public", "unlisted", "private"):
        raise YouTubeError(f"privacy must be public, unlisted or private, not {privacy!r}")
    _call(api.videos().update(part="status", body={
        "id": video_id, "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }))
