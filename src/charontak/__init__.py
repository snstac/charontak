"""Charontak: PyTAK CoT bridge between transports."""

from pathlib import Path

__version__ = Path(__file__).resolve().parent.joinpath("VERSION").read_text(
    encoding="utf-8"
).strip()
