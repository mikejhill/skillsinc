"""The tray icon in each state: the brand mark with a status badge."""

from __future__ import annotations

from enum import StrEnum
from importlib.resources import files
from typing import Final

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

SIZES: Final = (16, 20, 24, 32, 48, 64, 128, 256)
# Badge colors: syncing is the brand periwinkle; failure and pause stand out
BADGES: Final = {
    "syncing": "#A9B8FF",
    "failed": "#E5484D",
    "paused": "#8B8D98",
}


class TrayState(StrEnum):
    """What the tray icon shows."""

    IDLE = "idle"
    SYNCING = "syncing"
    FAILED = "failed"
    PAUSED = "paused"


def brand_pixmap(size: int) -> QPixmap:
    """Return the brand icon at ``size`` pixels (the nearest export, scaled)."""
    nearest = min(
        SIZES, key=lambda candidate: (candidate < size, abs(candidate - size))
    )
    data = (
        files("skillsinc.tray").joinpath("assets", f"icon-{nearest}.png").read_bytes()
    )
    pixmap = QPixmap()
    pixmap.loadFromData(data, "PNG")
    if pixmap.width() != size:
        pixmap = pixmap.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return pixmap


def badged(pixmap: QPixmap, color: str) -> QPixmap:
    """Return ``pixmap`` with a round badge of ``color`` in its lower right."""
    result = QPixmap(pixmap)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    side = result.width() * 0.45
    edge = max(1.0, result.width() / 16)
    rect = QRectF(result.width() - side, result.height() - side, side, side)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#172544"))
    painter.drawEllipse(rect)
    painter.setBrush(QColor(color))
    painter.drawEllipse(rect.adjusted(edge, edge, -edge, -edge))
    painter.end()
    return result


def tray_icon(state: TrayState) -> QIcon:
    """Return the icon for ``state`` at every size the shell may ask for."""
    icon = QIcon()
    for size in SIZES:
        pixmap = brand_pixmap(size)
        color = BADGES.get(state.value)
        icon.addPixmap(badged(pixmap, color) if color is not None else pixmap)
    return icon
