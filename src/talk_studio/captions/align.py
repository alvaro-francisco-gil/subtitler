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
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from . import extract
from .models import Word


def words_from_result(raw_words) -> list[Word]:
    """Convert stable-ts word timings into our Word model."""
    words = []
    for raw in raw_words:
        text = raw.word.strip()
        if not text:
            continue
        probability = getattr(raw, "probability", None)
        words.append(
            Word(
                text=text,
                start=float(raw.start),
                end=float(raw.end),
                score=1.0 if probability is None else float(probability),
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


def realigner(
    *,
    model_name: str = "large-v3",
    device: str = "cuda",
    language: str = "es",
) -> Callable[[Path, str, float, float], list[Word]]:
    """Build the re-aligner `repair` needs, loading the model only if it is used.

    Most videos have nothing to repair, and loading a 3 GB model to discover
    that would undo the caching the sample loop depends on.
    """
    model = None

    def realign(audio: Path, text: str, start: float, end: float) -> list[Word]:
        nonlocal model
        if model is None:
            import stable_whisper

            model = stable_whisper.load_model(model_name, device=device)

        with tempfile.TemporaryDirectory(prefix="subtitler-repair-") as staging:
            span = Path(staging) / "span.wav"
            extract.cut_audio(audio, span, start, end)
            return words_from_result(model.align(str(span), text, language=language).all_words())

    return realign


def save_words(words: list[Word], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(word) for word in words], ensure_ascii=False, indent=1))


def load_words(path: Path) -> list[Word]:
    return [Word(**entry) for entry in json.loads(path.read_text())]
