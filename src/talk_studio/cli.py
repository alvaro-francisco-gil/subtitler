"""talk-studio command line.

Agents drive the propose → sample → pick loop from here; humans judge in the
review page. `captions …` still reaches the original subtitler pipeline.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="talk-studio",
        description="Agents propose edits to a recorded talk; a human picks what feels right.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("captions", help="align a transcript and burn subtitles (the former subtitler)")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["captions"]:
        from .captions import cli as captions_cli

        return captions_cli.main(argv[1:])
    build_parser().parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
