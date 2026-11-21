"""Compiled frontend bundle, served by :mod:`nautilus.main`.

``dist/`` is produced by ``npm run build`` inside ``frontend/`` and shipped with
the package so ``pip install nautilus-mc`` yields a panel that is immediately
usable without a Node toolchain.
"""

from pathlib import Path

DIST = Path(__file__).parent / "dist"

__all__ = ["DIST"]
