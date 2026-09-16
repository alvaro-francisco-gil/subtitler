---
name: publishing-talks-to-youtube
description: Use when a rendered talk video needs to go on YouTube — uploading, setting title, description, chapters, thumbnail or captions, or making it public with talk-studio
---

# Publishing a talk to YouTube

The human uploads the file; `talk-studio youtube` does everything else from a
`youtube.toml` beside the talk. The API cannot do the upload: videos inserted by an
unaudited Cloud project are locked private forever.

## The sequence

1. **Offline check.** `talk-studio youtube check <youtube.toml>`. Fix every problem it
   names before anything else.
2. **Sign-in still alive?** `talk-studio youtube latest`. If it fails on auth, run
   `talk-studio youtube auth` in the background and give the human the printed URL.
   An OAuth app left in *Testing* expires sign-ins after 7 days, so expect this.
3. **Hand over the upload.** Name the account the channel signs in with (look it up;
   ask if unrecorded). Tell the human: Studio → Create → Upload, leave it **Private**,
   fill nothing in, and **close the upload dialog instead of pressing Save/Next** —
   saving that dialog later overwrites what `apply` set.
4. **Find the right video.** Poll `youtube latest` for an id that was not there before.
   Confirm it is this file: `videos.list part=fileDetails` gives the file name and
   byte size; compare with the render.
5. **Apply now, then again after processing.** `talk-studio youtube apply <toml> --video
   <id>` works mid-upload. Wait until `processingDetails.processingStatus` is
   `succeeded`, then apply again (it replaces its own caption track).
6. **Read it back.** From the API: title, privacy, duration, chapter count, thumbnail,
   caption track `serving`. Send the human the link and that summary.
7. **Public only on an explicit go.** `talk-studio youtube privacy <id> public` after the
   human says so in this conversation. "OK" to something else is not a go.
   To publish later instead, `talk-studio youtube schedule <id> 2026-09-18T18:00+02:00`
   (private until then). A batch of Shorts goes out one a day, not all at once. An id
   starting with `-` needs `--` before it.
8. **Record it** beside the talk: the video id and URL, and the date it went public.

## Shorts

The same sequence, one `shorts/<slug>.youtube.toml` per clip: the hook as the title, one
line about the moment, the full talk's link, `#Shorts`. No caption track, because the
captions are burnt in. The human can drag every file into one upload; drafts can be edited
straight away. Offer a schedule, not an instant publish: one a day at 18:00 local, the
strongest first, lined up with any post announcing the talk.

## Common mistakes

| Mistake | Instead |
|---|---|
| Taking the newest upload on trust | Match file name and size first |
| Applying once, during processing | Apply again once processing succeeds |
| Re-running `auth` blindly on any error | Only on auth errors; quota or 403 thumbnail errors are different problems |
| Custom thumbnail refused (403) | The channel is not verified for custom thumbnails; tell the human, keep going |
| Making it public because everything checked out | Wait for the human's go |
| `--video -fhz44aaELE` fails as an unknown flag | `--video=-fhz44aaELE`, or `--` before a positional id |
| A read-back straight after `schedule` shows no `publishAt` | Read again a few seconds later |
