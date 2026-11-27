"""Single source of truth for the Nautilus version.

``pyproject.toml`` reads ``__version__`` from here (setuptools ``attr``), the
API meta endpoints read both fields, and the release workflow stamps
``__commit__`` at build time. Never hardcode the version anywhere else.
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

__version__ = "0.1.0"
#: Stamped by the release workflow (git tag build). Empty in editable installs.
__commit__ = ""


@lru_cache(maxsize=1)
def git_commit() -> str:
    """Short commit hash for display: stamp > env > live git query > empty."""
    if __commit__:
        return __commit__
    env = os.environ.get("NAUTILUS_GIT_COMMIT", "").strip()
    if env:
        return env[:12]
    repo = Path(__file__).resolve().parent.parent
    if not (repo / ".git").exists():
        return ""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""
