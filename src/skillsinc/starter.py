"""Write a starter configuration routing one library to the installed harnesses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from skillsinc.exceptions import ConfigurationError
from skillsinc.paths import Environment
from skillsinc.profiles.registry import ProfileRegistry

PLACEHOLDER_SOURCE: Final = "~/agent-library"
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

        Raises:
            ConfigurationError: If ``path`` exists and ``force`` is not set.
        """
        if path.exists() and not force:
            msg = f"{path} already exists; pass --force to overwrite it"
            raise ConfigurationError(msg)
        text = self.render(source)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return text

    def render(self, source: Path | None) -> str:
        """Return starter TOML, enabling destinations whose harness directory exists."""
        source_path = self._display(source) if source else PLACEHOLDER_SOURCE
        lines = [HEADER, "[sources.library]", f"path = {json.dumps(source_path)}"]
        if source is None:
            lines.append(
                "# Replace with your library: skills/, instructions/, agents/,"
                " prompts/, mcp/, resources/"
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

        targets = ", ".join(json.dumps(name) for name in detected)
        route = "" if detected else "# "
        lines.extend(
            [
                "",
                f"{route}[[routes]]",
                f'{route}sources = ["library"]',
                f"{route}destinations = [{targets}]",
                '# include = ["skills/*", "instructions/*"]',
                '# exclude = ["skills/experimental-*"]',
                "# allow_lossy = false  # true accepts conversions that drop fields",
                "",
            ]
        )
        return "\n".join(lines)

    def _display(self, path: Path) -> str:
        """Return ``path`` with forward slashes and ``~`` for the home directory."""
        absolute = path.expanduser().absolute()
        if absolute.is_relative_to(self._environment.home):
            return "~/" + absolute.relative_to(self._environment.home).as_posix()
        return absolute.as_posix()
