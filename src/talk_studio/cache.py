"""Where renders live, and how they are named.

Keys are content hashes of everything that determines the output, so asking
for a render that already exists costs nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def root() -> Path:
    override = os.environ.get("TALK_STUDIO_CACHE")
    return Path(override) if override else Path.home() / ".cache" / "talk-studio"


def project_dir(project_root: Path) -> Path:
    resolved = Path(project_root).resolve()
    digest = hashlib.sha256(str(resolved).encode()).hexdigest()[:12]
    return root() / f"{resolved.name}-{digest}"


def sample_key(*, fingerprint: str, tool: str, version: str, settings: dict, excerpt: str) -> str:
    payload = json.dumps(
        {"fingerprint": fingerprint, "tool": tool, "version": version, "settings": settings, "excerpt": excerpt},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def log_path(project_root: Path, decision: str, candidate_id: str) -> Path:
    return project_dir(project_root) / "logs" / decision / f"{candidate_id}.log"


def render_path(project_root: Path, name: str) -> Path:
    return project_dir(project_root) / "renders" / name
