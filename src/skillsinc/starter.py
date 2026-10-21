"""Write a starter configuration routing one library to the installed harnesses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from skillsinc.config import TOML_RETIRED
from skillsinc.exceptions import ConfigurationError
from skillsinc.paths import Environment
from skillsinc.profiles.registry import ProfileRegistry

HEADER: Final = """\
// SkillSinc configuration (JSONC: comments and trailing commas are allowed).
// Reference: https://github.com/mikejhill/skillsinc/blob/main/docs/guides/configuration.md
// Preview with `skillsinc plan`; nothing is written until `skillsinc sync`.
{
  "version": 1,
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
            ConfigurationError: If ``path`` is a ``.toml`` file, or exists and
                ``force`` is not set.
        """
        if path.suffix == ".toml":
            msg = f"{path}: {TOML_RETIRED}"
            raise ConfigurationError(msg)
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
        """Return starter JSONC, enabling destinations whose harness folder exists."""
        source_path = self._display(source or self._environment.default_library)
        lines = [HEADER.rstrip("\n"), '  "sources": {']
        if source is None:
            lines.append(
                "    // Default library. Add skills/, instructions/, agents/,"
                " prompts/, mcp/, resources/, hooks/, plugins/"
            )
        lines.extend(
            [f'    "library": {{ "path": {json.dumps(source_path)} }},', "  },"]
        )

        lines.append('  "destinations": {')
        detected: list[str] = []
        for profile in self._profiles.profiles:
            default = profile.default_path(self._environment)
            if default is None:
                continue
            installed = default.is_dir()
            prefix = "" if installed else "// "
            note = "" if installed else "  // not found on this machine"
            name = json.dumps(profile.name)
            lines.append(f'    {prefix}{name}: {{ "profile": {name} }},{note}')
            if installed:
                detected.append(profile.name)
        lines.append("  },")

        targets = ", ".join(json.dumps(name) for name in detected)
        route = "" if detected else "// "
        optional = "      // " if detected else "    //   "
        lines.extend(
            [
                '  "routes": [',
                f"    {route}{{",
                f'    {route}  "sources": ["library"],',
                f'    {route}  "destinations": [{targets}],',
                f'{optional}"include": ["skills/*", "instructions/*"],',
                f'{optional}"exclude": ["skills/experimental-*"],',
                f'{optional}"allow_lossy": false,  // true accepts conversions'
                " that drop fields",
                f"    {route}}},",
                "  ],",
                "}",
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
