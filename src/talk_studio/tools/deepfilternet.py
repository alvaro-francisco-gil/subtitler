"""DeepFilterNet 3: a speech denoiser model, run in its own environment."""

from __future__ import annotations

from . import AudioTool


class DeepFilterNet(AudioTool):
    name = "deepfilternet"
    version = "0.5.6"
