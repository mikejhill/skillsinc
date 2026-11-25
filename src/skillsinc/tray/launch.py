"""``skillsinc-tray``: start the tray with no console window."""

from __future__ import annotations

import sys

from skillsinc.__main__ import Application


def main() -> None:
    """Run ``skillsinc tray`` with this process's arguments."""
    Application.main(["tray", *sys.argv[1:]])
