"""Command line entry point.

Three commands, but `sample` is the one that matters. Alignment is the
expensive stage and it is cached per video, so the first run on a new video
costs minutes and every subsequent sample costs seconds. That is what makes
iterating on style.toml bearable.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from . import align, ass, binaries, clean, drift, extract, group, measure, probe, render, style, workdir
from .models import Cue, Word
from .probe import MediaInfo


@dataclass
class PipelineResult:
    info: MediaInfo
    words: list[Word]
    cues: list[Cue]
    work: workdir.WorkDir


def parse_timecode(value: str) -> float:
    """Parse `90`, `1:30` or `1:02:03` into seconds."""
    parts = value.strip().split(":")
    if not all(part.strip().lstrip("-").isdigit() for part in parts) or len(parts) > 3:
        raise ValueError(f"could not parse timecode {value!r}")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    if seconds < 0:
        raise ValueError(f"timecode {value!r} must not be negative")
    return seconds


def first_dense_span(words: list[Word], *, window: float = 10.0) -> float:
    """Start time of the densest `window` seconds of speech.

    The opening of a live recording is usually applause or silence, which
    makes a poor reference for judging subtitle style.

    Words with no duration are excluded. The aligner emits those when it
    fails, collapsing many of them onto one timestamp — which would otherwise
    look like the densest speech in the video and send the sample straight to
    the one region where the subtitles are certainly wrong.
    """
    words = [word for word in words if word.duration > 0]
    if not words:
        return 0.0

    best_start, best_count = words[0].start, 0
    for candidate in words:
        count = sum(1 for word in words if candidate.start <= word.start < candidate.start + window)
        if count > best_count:
            best_start, best_count = candidate.start, count
    return best_start


def pipeline(video: Path, transcript: Path, style_path: Path, *, force: bool = False) -> PipelineResult:
    work = workdir.for_video(video)
    sty = style.load(style_path)
    info = probe.probe(video)
    work.meta.write_text(json.dumps(asdict(info), indent=1))

    if force or not workdir.is_fresh(work.audio, video):
        print(f"extracting audio from {video.name}", file=sys.stderr)
        extract.extract_audio(video, work.audio)

    cleaned = clean.clean_transcript(transcript.read_text())
    script_map = json.dumps(
        [{"line": p.line, "column": p.column} for p in cleaned.positions]
    )
    # Only write when the content changed. Rewriting unconditionally would bump
    # the mtime and invalidate the cached alignment on every run, which is the
    # opposite of what the sample loop needs.
    if not work.script.exists() or work.script.read_text() != cleaned.text:
        work.script.write_text(cleaned.text)
    if not work.script_map.exists() or work.script_map.read_text() != script_map:
        work.script_map.write_text(script_map)

    if force or not workdir.is_fresh(work.words, work.audio, work.script):
        print("aligning transcript to audio (this is the slow part)", file=sys.stderr)
        words = align.align(work.audio, cleaned.text)
        align.save_words(words, work.words)
    else:
        words = align.load_words(work.words)

    positions = cleaned.positions
    if len(words) != len(positions):
        print(
            f"warning: aligned {len(words)} words but the transcript has "
            f"{len(positions)} — line numbers in the drift report may be off",
            file=sys.stderr,
        )
        positions = None
    flags = drift.find_drift(words, positions=positions)
    work.drift.write_text(drift.render_report(flags, total_words=len(words)))
    if flags:
        print(f"{len(flags)} drift span(s) flagged — see {work.drift}", file=sys.stderr)

    measurer = measure.text_measurer(sty.font_path, sty.font_size)

    def fits(texts: list[str]) -> bool:
        rendered = [t.upper() if sty.all_caps else t for t in texts]
        gap = measurer(" ") * sty.word_spacing
        total = sum(measurer(t) for t in rendered) + gap * (len(rendered) - 1)
        return total <= info.display_width * sty.max_width

    cues = group.group_words(
        words,
        max_words=sty.max_words,
        pause_break=sty.pause_break,
        fits=fits,
    )
    work.cues.write_text(json.dumps(
        [
            {"start": c.start, "end": c.end,
             "words": [{"text": w.text, "start": w.start, "end": w.end} for w in c.words]}
            for c in cues
        ],
        ensure_ascii=False,
    ))

    return PipelineResult(info=info, words=words, cues=cues, work=work)


def _write_ass(
    cues: list[Cue],
    sty: style.Style,
    info: MediaInfo,
    path: Path,
    *,
    title: str | None = None,
    title_at: float = 0.0,
) -> Path:
    measurer = measure.text_measurer(sty.font_path, sty.font_size)
    document = ass.build_ass(
        cues, sty, info.display_width, info.display_height, measurer,
        title=title,
        title_at=title_at,
        title_em=measure.rendered_em(sty.font_path, sty.title_size),
        title_measure=measure.text_measurer(sty.font_path, sty.title_size),
    )
    path.write_text(document)
    return path


def command_align(args) -> int:
    result = pipeline(args.video, args.transcript, args.style, force=args.force)
    print(f"aligned {len(result.words)} words -> {result.work.words}")
    print(f"drift report -> {result.work.drift}")
    return 0


def command_sample(args) -> int:
    sty = style.load(args.style)
    result = pipeline(args.video, args.transcript, args.style, force=args.force)

    start = parse_timecode(args.at) if args.at is not None else first_dense_span(result.words)
    if start >= result.info.duration:
        raise ValueError(
            f"--at {start:.0f}s is past the end of a {result.info.duration:.0f}s video"
        )
    end = start + args.len

    windowed = render.shift_cues(result.cues, start=start, end=end)
    # The title is placed against the source timeline, so a sample only shows it
    # when the window it renders actually contains it.
    title_at = args.title_at - start
    show_title = args.title is not None and start <= args.title_at < end
    ass_path = _write_ass(
        windowed, sty, result.info, result.work.root / "sample.ass",
        title=args.title if show_title else None,
        title_at=max(title_at, 0.0),
    )

    out = args.out or args.video.with_name(f"{args.video.stem}_sample.mp4")
    print(f"rendering {args.len}s from {start:.1f}s -> {out}", file=sys.stderr)
    render.burn(
        args.video, ass_path, out, result.info,
        fontsdir=sty.font_path.parent, start=start, duration=float(args.len),
        encoder=args.encoder,
    )
    print(out)
    return 0


def command_render(args) -> int:
    sty = style.load(args.style)
    result = pipeline(args.video, args.transcript, args.style, force=args.force)

    ass_path = _write_ass(
        result.cues, sty, result.info, result.work.ass,
        title=args.title, title_at=args.title_at,
    )
    out = args.out or args.video.with_name(f"{args.video.stem}_subtitled.mp4")

    print(f"rendering full video -> {out}", file=sys.stderr)
    render.burn(
        args.video, ass_path, out, result.info,
        fontsdir=sty.font_path.parent, encoder=args.encoder,
    )
    print(out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="subtitler",
        description="Burn karaoke-style subtitles into a video from a supplied transcript.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub):
        sub.add_argument("video", type=Path)
        sub.add_argument("transcript", type=Path)
        sub.add_argument("--style", type=Path, default=style.DEFAULT_STYLE_PATH)
        sub.add_argument("--force", action="store_true", help="ignore cached artifacts")
        sub.add_argument("--encoder", default="h264_nvenc")
        sub.add_argument("--out", type=Path, default=None)
        sub.add_argument("--title", default=None, help="title card text, shown once near the start")
        sub.add_argument(
            "--title-at", type=parse_timecode, default=2.0,
            help="when the title card appears (default: 2)",
        )

    sample = subparsers.add_parser("sample", help="render a short window to check the style")
    add_common(sample)
    sample.add_argument("--at", default=None, help="start timecode, e.g. 2:15 (default: densest speech)")
    sample.add_argument("--len", type=float, default=10.0, help="sample length in seconds")
    sample.set_defaults(func=command_sample)

    full = subparsers.add_parser("render", help="render the whole video")
    add_common(full)
    full.set_defaults(func=command_render)

    align_cmd = subparsers.add_parser("align", help="align only; write words.json and drift.md")
    add_common(align_cmd)
    align_cmd.set_defaults(func=command_align)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (binaries.BinaryError, ValueError, OSError) as error:
        print(f"subtitler: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
