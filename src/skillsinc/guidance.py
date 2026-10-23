"""Install optional starter guidance without replacing source-library content."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Final

from skillsinc.exceptions import ConfigurationError

GUIDANCE_PATHS: Final = (
    "skills/skillsinc/SKILL.md",
    "instructions/skillsinc.instructions.md",
)


@dataclass(frozen=True, slots=True)
class GuidanceFile:
    """One absent library file and the bundled content planned for it."""

    path: Path
    content: bytes


class GuidanceInstaller:
    """Plan every starter file before creating any of them."""

    def plan(self, library: Path, config: Path) -> tuple[GuidanceFile, ...]:
        """Validate paths and conflicts, keeping identical files untouched."""
        root = library.expanduser().absolute()
        pending: list[GuidanceFile] = []
        for relative in GUIDANCE_PATHS:
            path = root / relative
            self._check_path(path)
            if config.absolute() in (path, *path.parents):
                msg = f"{config}: configuration overlaps the guidance library"
                raise ConfigurationError(msg)
            content = files("skillsinc").joinpath("guidance", relative).read_bytes()
            try:
                if path.exists():
                    if not path.is_file() or path.read_bytes() != content:
                        msg = f"{path}: guidance conflicts with existing content"
                        raise ConfigurationError(msg)
                else:
                    pending.append(GuidanceFile(path, content))
            except OSError as err:
                msg = f"{path}: cannot read guidance target: {err}"
                raise ConfigurationError(msg) from err
        return tuple(pending)

    @staticmethod
    def install(pending: tuple[GuidanceFile, ...]) -> None:
        """Create only planned absent files, refusing a concurrent replacement."""
        for item in pending:
            GuidanceInstaller._check_path(item.path)
            try:
                item.path.parent.mkdir(parents=True, exist_ok=True)
                with item.path.open("xb") as stream:
                    stream.write(item.content)
            except OSError as err:
                msg = f"{item.path}: cannot create guidance: {err}"
                raise ConfigurationError(msg) from err

    @staticmethod
    def _check_path(path: Path) -> None:
        """Reject links and non-directory parents before reading or writing."""
        for part in reversed((path, *path.parents)):
            if part.is_symlink() or part.is_junction():
                msg = f"{part}: guidance path passes through a link"
                raise ConfigurationError(msg)
            if part != path and part.exists() and not part.is_dir():
                msg = f"{part}: guidance parent is not a directory"
                raise ConfigurationError(msg)
