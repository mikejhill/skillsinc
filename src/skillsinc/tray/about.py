"""The About window: version, environment, and where SkillSinc keeps things."""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import PySide6
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from skillsinc.tray.icons import brand_pixmap

HOMEPAGE: Final = "https://github.com/mikejhill/skillsinc"
CHANGELOG: Final = f"{HOMEPAGE}/blob/main/CHANGELOG.md"


@dataclass(frozen=True, slots=True)
class AboutFacts:
    """What the About window lists."""

    version: str
    config: Path
    settings: Path
    state: Path

    def rows(self) -> list[tuple[str, str]]:
        """Return label and value pairs, in display order."""
        return [
            ("Version", self.version),
            ("Python", f"{platform.python_version()} ({sys.executable})"),
            ("Qt", f"PySide6 {PySide6.__version__}"),
            ("Configuration", str(self.config)),
            ("Settings", str(self.settings)),
            ("History and logs", str(self.state)),
        ]


class AboutDialog(QDialog):
    """SkillSinc's version and environment."""

    def __init__(self, facts: AboutFacts, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About SkillSinc")
        self.facts = facts
        logo = QLabel(self)
        logo.setPixmap(brand_pixmap(64))
        title = QLabel(f"<h2>SkillSinc {facts.version}</h2>", self)
        self.version_label = title
        blurb = QLabel(
            "Sync one library of agent skills, instructions, agents, and prompts"
            " into every AI coding harness you use.",
            self,
        )
        blurb.setWordWrap(True)
        heading = QVBoxLayout()
        heading.addWidget(title)
        heading.addWidget(blurb)
        top = QHBoxLayout()
        top.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
        top.addLayout(heading, 1)
        rows = "".join(
            f"<tr><td><b>{label}</b>&nbsp;&nbsp;</td><td>{_escape(value)}</td></tr>"
            for label, value in facts.rows()
        )
        self.details = QLabel(f"<table>{rows}</table>", self)
        self.details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        links = QLabel(
            f'<a href="{HOMEPAGE}">Project page</a> · '
            f'<a href="{CHANGELOG}">What\'s new</a> · MIT License',
            self,
        )
        links.setOpenExternalLinks(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.details)
        layout.addWidget(links)
        layout.addWidget(buttons)


def _escape(text: str) -> str:
    """Return ``text`` safe inside rich text."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
