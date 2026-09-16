"""Locating and invoking the ffmpeg toolchain.

A system ffmpeg is preferred. The `static-ffmpeg` package installs its
binaries under the names `static_ffmpeg` and `static_ffprobe`, which is how
this machine currently provides them, so those are the fallback.
"""

from __future__ import annotations

import shutil
import subprocess


class BinaryError(Exception):
    """A required external binary is missing, or a run of one failed."""


def _find(*names: str) -> str:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    raise BinaryError(
        f"could not find any of {', '.join(names)} on PATH. "
        f"Install ffmpeg (apt install ffmpeg) or run: uv tool install static-ffmpeg"
    )


def ffmpeg() -> str:
    return _find("ffmpeg", "static_ffmpeg")


def ffprobe() -> str:
    return _find("ffprobe", "static_ffprobe")


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing output. Raises BinaryError on non-zero exit."""
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise BinaryError(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stderr}"
        )
    return result
