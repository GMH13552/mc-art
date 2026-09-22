"""Tests for the CLI's own argument plumbing.

The family path never reads --size, so a double parse there stayed hidden until
a plain single-asset run hit it.
"""

from __future__ import annotations

from mc_art.cli import build_parser


