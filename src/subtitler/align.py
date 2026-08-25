"""Forced alignment of a known script against the audio.

This is alignment, not transcription. The words are given; the job is finding
when each one was said. Where the speaker improvised or skipped a line the
aligner will still place the scripted words somewhere — `drift.py` is what
turns that into something the user can see and act on.

`stable_whisper` is imported lazily so that importing this module, and
therefore running the fast unit tests, does not pull in torch.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import Word


def words_from_result(raw_words) -> list[Word]:
    """Convert stable-ts word timings into our Word model."""
    words = []
    for raw in raw_words:
        text = raw.word.strip()
        if not text:
            continue
        words.append(
            Word(
                text=text,
                start=float(raw.start),
                end=float(raw.end),
                score=float(getattr(raw, "probability", None) or 1.0),
            )
        )
    return words


def align(
    audio: Path,
    script: str,
    *,
    model_name: str = "large-v3",
    device: str = "cuda",
    language: str = "es",
) -> list[Word]:
    import stable_whisper

    model = stable_whisper.load_model(model_name, device=device)
    result = model.align(str(audio), script, language=language)
    return words_from_result(result.all_words())


def save_words(words: list[Word], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(word) for word in words], ensure_ascii=False, indent=1))


def load_words(path: Path) -> list[Word]:
    return [Word(**entry) for entry in json.loads(path.read_text())]
