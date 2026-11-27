#!/usr/bin/env python3
"""Stamp ``nautilus/_version.py`` for a release build (used by release.yml).

Usage: python scripts/stamp_version.py v0.2.0 abc1234
Rewrites ``__version__`` (tag without the leading ``v``) and ``__commit__``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "nautilus" / "_version.py"


def main() -> int:
    if len(sys.argv) != 3 or not sys.argv[1].startswith("v"):
        print(__doc__, file=sys.stderr)
        return 2
    version = sys.argv[1].lstrip("v")
    commit = sys.argv[2]
    src = PATH.read_text(encoding="utf-8")
    src = re.sub(r'^__version__ = "[^"]*"', f'__version__ = "{version}"', src, count=1, flags=re.M)
    src = re.sub(r'^__commit__ = "[^"]*"', f'__commit__ = "{commit}"', src, count=1, flags=re.M)
    PATH.write_text(src, encoding="utf-8", newline="\n")
    print(f"stamped version {version} commit {commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
