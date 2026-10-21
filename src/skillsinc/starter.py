"""Write a starter configuration routing one library to the installed harnesses."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from skillsinc.exceptions import ConfigurationError
from skillsinc.paths import Environment
from skillsinc.profiles.registry import ProfileRegistry

HEADER: Final = """\
# SkillSinc configuration. Reference: https://github.com/mikejhill/skillsinc/blob/main/docs/guides/configuration.md
# Preview with `skillsinc plan`; nothing is written until `skillsinc sync`.
version = 1
"""


class StarterConfig:
    """Generate the file written by ``skillsinc init``."""

    def __init__(self, profiles: ProfileRegistry, environment: Environment) -> None:
        self._profiles = profiles
        self._environment = environment

    def write(self, path: Path, source: Path | None, *, force: bool) -> str:
        """Write the starter configuration to ``path`` and return its text.

        Without ``source``, the default library directory is created empty so
        the first ``plan`` succeeds.

        Raises:
            ConfigurationError: If ``path`` exists and ``force`` is not set.
        """
        if path.exists() and not force:
            msg = f"{path} already exists; pass --force to overwrite it"
            raise ConfigurationError(msg)
        text = self.render(source)
        path.parent.mkdir(parents=True, exist_ok=True)
        if source is None:
            self._environment.default_library.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return text

    def render(self, source: Path | None) -> str:
        """Return starter TOML, enabling destinations whose harness directory exists."""
        source_path = self._display(source or self._environment.default_library)
        lines = [HEADER, "[sources.library]", f"path = {json.dumps(source_path)}"]
        if source is None:
            lines.append(
                "# Default library. Add skills/, instructions/, agents/,"
                " prompts/, mcp/, resources/, hooks/, plugins/"
            )

        detected: list[str] = []
        for profile in self._profiles.profiles:
            default = profile.default_path(self._environment)
            if default is None:
                continue
            installed = default.is_dir()
            prefix = "" if installed else "# "
            note = "" if installed else "  # not found on this machine"
            lines.extend(
                [
                    "",
                    f"{prefix}[destinations.{profile.name}]{note}",
                    f"{prefix}profile = {json.dumps(profile.name)}",
                ]
            )
            if installed:
                detected.append(profile.name)

        if not detected:
            lines.extend(["", *self._route((), (), first=True, enabled=False)])
        for index, (excludes, names) in enumerate(self._groups(detected).items()):
            lines.extend(["", *self._route(names, excludes, first=index == 0)])
        lines.append("")
        return "\n".join(lines)

    def _groups(self, detected: list[str]) -> dict[tuple[str, ...], list[str]]:
        """Group detected destinations by the kinds left out to avoid duplicates.

        Each item is written once per harness: skills go to ``~/.agents`` rather
        than ``~/.copilot`` (Copilot CLI and VS Code read both), and VS Code
        takes instructions and agents from ``~/.copilot`` rather than its
        prompts folder.
        """
        groups: dict[tuple[str, ...], list[str]] = {}
        for name in detected:
            excludes: tuple[str, ...] = ()
            if name == "copilot" and "agents" in detected:
                excludes = ("skills/*",)
            elif name == "vscode" and "copilot" in detected:
                excludes = ("instructions/*", "agents/*")
            groups.setdefault(excludes, []).append(name)
        return groups

    @staticmethod
    def _route(
        names: Sequence[str],
        excludes: Sequence[str],
        *,
        first: bool,
        enabled: bool = True,
    ) -> list[str]:
        """Return the lines of one ``[[routes]]`` table."""
        prefix = "" if enabled else "# "
        targets = ", ".join(json.dumps(name) for name in names)
        lines = [
            f"{prefix}[[routes]]",
            f'{prefix}sources = ["library"]',
            f"{prefix}destinations = [{targets}]",
        ]
        if excludes:
            patterns = ", ".join(json.dumps(pattern) for pattern in excludes)
            lines.append(f"exclude = [{patterns}]  # another destination covers these")
        elif first:
            lines.extend(
                [
                    '# include = ["skills/*", "instructions/*"]',
                    '# exclude = ["skills/experimental-*"]',
                ]
            )
        if first:
            lines.append(
                "# allow_lossy = false  # true accepts conversions that drop fields"
            )
        return lines

    def _display(self, path: Path) -> str:
        """Return ``path`` with forward slashes and ``~`` for the home directory."""
        absolute = path.expanduser().absolute()
        if absolute.is_relative_to(self._environment.home):
            return "~/" + absolute.relative_to(self._environment.home).as_posix()
        return absolute.as_posix()
