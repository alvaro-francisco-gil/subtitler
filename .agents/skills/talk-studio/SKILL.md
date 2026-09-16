---
name: talk-studio
description: Use when editing a recorded talk with talk-studio — proposing audio treatments (and later grades, captions, thumbnails, clips) for a human to judge in the review page
---

# Editing with talk-studio

You explore; the human judges. Never pick for them, and never skip the review page.

## The loop

1. `talk-studio status --project <dir>` — see what is open.
2. `talk-studio excerpts suggest audio --project <dir>` (or `excerpts add audio 3:00-3:12`)
   when the decision has none.
3. Propose a round of **3–4 candidates that differ in kind**, not only in degree:
   `talk-studio tools --json` lists what exists and each setting's range.
   A round of three denoise strengths teaches less than `ffmpeg-chain`,
   `deepfilternet`, and `deepfilternet` with a low `atten_lim_db`.
4. `talk-studio sample audio --project <dir>`. Read failures; fix or drop them.
5. Tell the human the round is ready and to open `talk-studio review` at
   `http://localhost:8765/`. Then `talk-studio wait audio --project <dir> --json`.
6. On a reject, `talk-studio feedback audio --json` shows every round with settings and
   the human's notes. Translate the note into settings — "metallic" means less
   denoising; "boomy" means a higher high-pass; "too quiet in the pauses" means less
   compression — and propose the next round around it.
7. On a pick, stop. `talk-studio render --project <dir>` produces the final file.

## Rules

- Keep the original in mind: every round is compared against it, so a candidate that
  is only a little different from the original wastes a slot.
- Nine candidates is the hard cap; four is kind.
- A `stale` decision means the source changed. Re-sample and ask the human to pick
  again; `talk-studio reopen <decision>` is the command that reopens a decision that
  was already picked.
- Project folders live beside the material (for talks, the `recording/` folder of the
  deck in the owner's repo), never in this repository.
