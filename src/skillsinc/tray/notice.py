"""A tray notification."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Notice:
    """A tray notification."""

    title: str
    message: str
    failure: bool = False
