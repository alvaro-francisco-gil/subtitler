---
name: talk-studio
description: Use when editing a recorded talk with talk-studio — proposing audio treatments, grades or vertical clips (Shorts) for a human to judge in the review page
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
7. On an `audio` pick, run the same loop for `master`: its samples are cut from the
   picked audio, so it cannot start before that pick. Vary the kind here too —
   `voice-master` (EQ, de-esser, compressor) against `speech-leveler` (phrase-by-phrase
   levelling). Notes translate as: "thin" → more `warmth_db` or less `presence_db`;
   "harsh" or "hissy" → more `deess`, less `air_db`; "flat" or "lifeless" → a gentler
   `ratio`; "quiet bits get lost" → `speech-leveler` with a higher `max_boost`.
8. `grade` is independent of the audio: `excerpts suggest grade` picks six stills spread
   across the talk, and the page shows each with a before/after divider. Put
   `auto-balance` (measures and removes the cast) against `ffmpeg-eq` (manual temperature,
   tint and tone). Notes translate as: "green" or "sickly" → `auto-balance` or a negative
   `tint`; "washed out" → `contrast` 1.1–1.2; "faces too dark" → `gamma` above 1;
   "too orange" → a lower `strength` or a higher `temperature`.
9. On the last pick, stop. `talk-studio render --project <dir>` produces the final file
   at -14 LUFS / -1 dBTP. Never propose loudness as a candidate; the render owns it.
10. **Shorts come after the render**, cut from it. Find passages in the word timings
   (`words.json`): 30–60 s, one idea, a line that stands alone. `clips add <slug> <range>
   --words words.json`, then the loop per `clip-<slug>`. Settle in this order, one
   thing a round: layout first (`follow-speaker` against `slide-and-speaker` and
   `blur-letterbox`), then the hook's wording (three lines the speaker actually says or
   implies, same style), then its look (`hook_style` slam / sticker / comic, `hook_emoji`).
   Pass `hook_size=1.0` with the animated styles. Before telling the human, pull frames
   (0.5 s, 3 s) from every render and look: text cut off at the top, words run together,
   an emoji alone on a line over the face. `clips export --out <dir>` collects the picks.

## Reading the human's verdicts

When they ask "did you get the feedback?", read `feedback/<decision>.jsonl`: notes left in
the page say what to change. A pick with a note ("I like the sentence but bigger") means
keep that candidate's settings and vary what the note names. When they say "A for all of
them" in chat, record it with `Project.record(name, "pick", label="A", note=...)` and say
the note came from chat.

## Rules

- Keep the original in mind: every round is compared against it, so a candidate that
  is only a little different from the original wastes a slot.
- Nine candidates is the hard cap; four is kind.
- A `stale` `master` means the audio pick changed under it. Otherwise a `stale` decision means the source changed. Re-sample and ask the human to pick
  again; `talk-studio reopen <decision>` is the command that reopens a decision that
  was already picked.
- Restart `talk-studio review` after changing tool code; it has no `--no-open` flag.
  Stop it with `pkill -f "talk-studio revie[w]"` in its own command — an unbracketed
  pattern matches the shell running pkill and kills it.
- `Excerpt(start, end)` takes an end, not a length.
- Project folders live beside the material (for talks, the `recording/` folder of the
  deck in the owner's repo), never in this repository.
