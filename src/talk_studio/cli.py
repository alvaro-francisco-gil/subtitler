"""talk-studio command line.

Agents drive the propose → sample → pick loop from here; humans judge in the
review page. Every data-producing command takes `--json`. `captions …` still
reaches the original subtitler pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from . import binaries, cache, excerpts, final, media, sampling, tools
from .project import Project, ProjectError
from .timecode import parse_excerpt


def emit(args, human: str, data) -> None:
    print(json.dumps(data, indent=1, ensure_ascii=False) if getattr(args, "json", False) else human)


def command_init(args) -> int:
    project = Project.init(args.video, args.project)
    emit(args, f"created {project.root / 'project.toml'}", {"root": str(project.root), "duration": project.duration})
    return 0


def tool_info(tool: tools.AudioTool) -> dict:
    return {
        "name": tool.name, "kind": tool.kind, "version": tool.version,
        "params": [asdict(p) | {"choices": list(p.choices)} for p in tool.params],
        "requires": [asdict(r) for r in tool.requires()],
    }


def command_tools(args) -> int:
    listed = [t for t in tools.all_tools() if args.kind in (None, t.kind)]
    lines = []
    for tool in listed:
        lines.append(f"{tool.name} ({tool.kind}, v{tool.version})")
        for p in tool.params:
            bounds = f" [{p.minimum}..{p.maximum}]" if p.minimum is not None else ""
            lines.append(f"  {p.name}: {p.kind} = {p.default}{bounds} — {p.help}")
    emit(args, "\n".join(lines), [tool_info(t) for t in listed])
    return 0


def command_excerpts(args) -> int:
    project = Project.load(args.project)
    decision = project.decision(args.decision)
    if args.action == "add":
        project.add_excerpt(args.decision, parse_excerpt(args.range))
    else:
        if decision.excerpts and not args.replace:
            raise ProjectError(f"{args.decision} already has excerpts; pass --replace to overwrite them")
        project.set_excerpts(args.decision, excerpts.suggest(excerpts.frame_levels(project.source), length=args.length))
    current = [str(e) for e in project.decision(args.decision).excerpts]
    emit(args, "\n".join(current), current)
    return 0


def command_propose(args) -> int:
    project = Project.load(args.project)
    tool = tools.get_tool(args.tool)
    project.decision(args.decision)
    if tool.kind != args.decision:
        fitting = ", ".join(t.name for t in tools.all_tools() if t.kind == args.decision) or "none yet"
        raise ProjectError(f"{tool.name} is an {tool.kind} tool; tools for {args.decision}: {fitting}")
    raw = {}
    for pair in args.set:
        key, sep, value = pair.partition("=")
        if not sep:
            raise ValueError(f"--set expects key=value, got {pair!r}")
        raw[key] = value
    candidate = project.propose(args.decision, tool.name, tool.version, tool.settings(raw), args.proposer)
    label = project.label(candidate)
    emit(args, f"{candidate.id} proposed as {label} in round {candidate.round}", asdict(candidate) | {"label": label})
    return 0


def command_sample(args) -> int:
    project = Project.load(args.project)
    report = sampling.render_round(project, args.decision)
    human = f"rendered {len(report.rendered)}, failed {len(report.failed)}"
    for candidate_id in report.failed:
        candidate = next(c for c in project.candidates(args.decision) if c.id == candidate_id)
        human += f"\n  {candidate_id} failed — log: {candidate.error}"
    emit(args, human, asdict(report))
    return 0


def command_review(args) -> int:
    import uvicorn

    from .server.app import create_app

    project = Project.load(args.project)
    print(f"review page: http://{args.host}:{args.port}/  (project {project.root})")
    uvicorn.run(create_app(project.root), host=args.host, port=args.port, log_level="warning")
    return 0


def rounds_report(project: Project, decision: str) -> list[dict]:
    verdicts = project.feedback(decision)
    rounds = sorted({c.round for c in project.candidates(decision)})
    return [
        {
            "round": r,
            "candidates": [asdict(c) | {"label": project.label(c)} for c in project.round_candidates(decision, r)],
            "verdicts": [asdict(v) for v in verdicts if v.round == r],
        }
        for r in rounds
    ]


def command_feedback(args) -> int:
    project = Project.load(args.project)
    rounds = rounds_report(project, args.decision)
    lines = []
    for entry in rounds:
        lines.append(f"round {entry['round']}")
        for c in entry["candidates"]:
            lines.append(f"  {c['label']} {c['id']} {c['tool']} {json.dumps(c['settings'])} [{c['state']}]")
        for v in entry["verdicts"] or [{"verdict": "open", "candidate": None, "note": ""}]:
            note = f" — {v['note']}" if v["note"] else ""
            lines.append(f"  → {v['verdict']} {v['candidate'] or ''}{note}")
    emit(args, "\n".join(lines) or "no rounds yet", {"decision": args.decision, "rounds": rounds})
    return 0


def command_wait(args) -> int:
    seen = len(Project.load(args.project).feedback(args.decision))
    deadline = time.monotonic() + args.timeout if args.timeout > 0 else None
    while True:
        project = Project.load(args.project)
        verdicts = project.feedback(args.decision)
        if len(verdicts) > seen:
            new = [asdict(v) for v in verdicts[seen:]]
            human = "\n".join(f"{v['verdict']} {v['candidate'] or ''} {v['note']}".strip() for v in new)
            emit(args, human, new)
            return 0
        if deadline is not None and time.monotonic() > deadline:
            print(f"timed out waiting for feedback on {args.decision}", file=sys.stderr)
            return 2
        time.sleep(args.interval)


def command_reopen(args) -> int:
    project = Project.load(args.project)
    project.reopen(args.decision)
    emit(args, f"{args.decision} reopened", {"decision": args.decision, "status": "open"})
    return 0


def command_render(args) -> int:
    out = final.render(Project.load(args.project), args.out)
    emit(args, str(out), {"out": str(out)})
    return 0


def command_status(args) -> int:
    project = Project.load(args.project)
    matches = project.check_source()
    decisions = {}
    lines = [f"source {project.source} ({project.duration:.1f}s)" + ("" if matches else " — CHANGED, picks are stale")]
    for decision in project.decisions.values():
        open_round = project.open_round(decision.name)
        states: dict[str, int] = {}
        for c in project.round_candidates(decision.name, open_round) if open_round else []:
            states[c.state] = states.get(c.state, 0) + 1
        decisions[decision.name] = {
            "status": decision.status, "pick": decision.pick,
            "excerpts": [str(e) for e in decision.excerpts],
            "open_round": open_round, "candidates": states,
        }
        lines.append(
            f"{decision.name}: {decision.status}"
            + (f" (pick {decision.pick})" if decision.pick else "")
            + f", {len(decision.excerpts)} excerpts"
            + (f", round {open_round}: {states}" if open_round else "")
        )
    emit(args, "\n".join(lines), {"source_matches": matches, "decisions": decisions})
    return 0


def command_doctor(args) -> int:
    rows = []
    for name, finder in (("ffmpeg", binaries.ffmpeg), ("ffprobe", binaries.ffprobe)):
        try:
            rows.append({"check": name, "ok": True, "detail": finder()})
        except binaries.BinaryError as error:
            rows.append({"check": name, "ok": False, "detail": str(error)})
    for tool in tools.all_tools():
        for requirement in tool.requires():
            rows.append({"check": f"{tool.name}: {requirement.name}", "ok": requirement.ok, "detail": requirement.hint})
    rows.append({"check": "cache", "ok": True, "detail": str(cache.root())})
    human = "\n".join(f"{'ok ' if r['ok'] else 'MISSING'} {r['check']}  {r['detail']}" for r in rows)
    emit(args, human, rows)
    return 0 if all(r["ok"] for r in rows[:2]) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="talk-studio",
        description="Agents propose edits to a recorded talk; a human picks what feels right.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name, func, help_text, *, project=True, json_flag=True):
        p = sub.add_parser(name, help=help_text)
        if project:
            p.add_argument("--project", type=Path, default=Path("."))
        if json_flag:
            p.add_argument("--json", action="store_true")
        p.set_defaults(func=func)
        return p

    p = command("init", command_init, "create project.toml for a video")
    p.add_argument("video", type=Path)

    p = command("tools", command_tools, "list tools and their settings", project=False)
    p.add_argument("--kind", default=None)

    p = command("excerpts", command_excerpts, "suggest or add excerpts")
    p.add_argument("action", choices=["suggest", "add"])
    p.add_argument("decision")
    p.add_argument("range", nargs="?", help="for add: e.g. 3:00-3:12")
    p.add_argument("--replace", action="store_true")
    p.add_argument("--length", type=float, default=12.0)

    p = command("propose", command_propose, "propose a candidate")
    p.add_argument("decision")
    p.add_argument("--tool", required=True)
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--proposer", default="agent")

    p = command("sample", command_sample, "render samples for the open round")
    p.add_argument("decision")

    p = command("review", command_review, "start the review page", json_flag=False)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)

    p = command("wait", command_wait, "block until new feedback lands")
    p.add_argument("decision")
    p.add_argument("--timeout", type=float, default=0.0, help="seconds; 0 waits forever")
    p.add_argument("--interval", type=float, default=1.0)

    p = command("feedback", command_feedback, "rounds, candidates and verdicts")
    p.add_argument("decision")

    p = command("reopen", command_reopen, "reopen a picked decision")
    p.add_argument("decision")

    p = command("render", command_render, "render the final video from picks")
    p.add_argument("--out", type=Path, default=None)

    command("status", command_status, "decisions, rounds and source state")
    command("doctor", command_doctor, "check binaries and tool requirements", project=False)
    sub.add_parser("captions", help="align a transcript and burn subtitles (the former subtitler)")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["captions"]:
        from .captions import cli as captions_cli

        return captions_cli.main(argv[1:])
    args = build_parser().parse_args(argv)
    if args.command == "excerpts" and args.action == "add" and not args.range:
        print("talk-studio: excerpts add needs a range such as 3:00-3:12", file=sys.stderr)
        return 1
    try:
        return args.func(args)
    except (ProjectError, tools.ToolError, binaries.BinaryError, media.RenderError, ValueError, OSError) as error:
        print(f"talk-studio: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
