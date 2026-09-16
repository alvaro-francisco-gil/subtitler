"""DeepFilterNet 3: a speech denoiser model, run in its own environment.

The package does not depend on torch, and torchaudio 2.1+ removed an API it
imports, so the environment is pinned here. CPU wheels are enough: the model
runs at roughly 35× real time on a desktop CPU.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .. import binaries, media
from . import AudioTool, Param, Requirement, ToolError

UVX_ARGS = (
    "--python", "3.11",
    "--index", "https://download.pytorch.org/whl/cpu",
    "--index-strategy", "unsafe-best-match",
    "--from", "deepfilternet==0.5.6",
    "--with", "torch==2.0.1",
    "--with", "torchaudio==2.0.2",
    "--with", "numpy<2",
)


class DeepFilterNet(AudioTool):
    name = "deepfilternet"
    version = "0.5.6"
    params = (
        Param("atten_lim_db", "float", 100.0, "most the model may remove, in dB; lower keeps more room sound", 3.0, 100.0),
        Param("post_filter", "bool", False, "extra attenuation of very noisy stretches"),
    )

    def requires(self) -> list[Requirement]:
        return [Requirement("uvx", shutil.which("uvx") is not None, "install uv: https://docs.astral.sh/uv/")]

    def command(self, src: Path, out_dir: Path, settings: dict) -> list[str]:
        command = [
            "uvx", *UVX_ARGS, "deepFilter", "--no-suffix",
            "--atten-lim", f"{settings['atten_lim_db']:g}", "-o", str(out_dir),
        ]
        if settings["post_filter"]:
            command.append("--pf")
        command.append(str(src))
        return command

    def process(self, src: Path, out: Path, settings: dict) -> None:
        with tempfile.TemporaryDirectory(prefix="talk-studio-dfn-") as staging:
            binaries.run(self.command(src, Path(staging), settings))
            produced = Path(staging) / src.name
            if not produced.exists():
                raise ToolError(f"deepFilter finished but wrote no {src.name}")
            media.extract_wav(produced, out)
