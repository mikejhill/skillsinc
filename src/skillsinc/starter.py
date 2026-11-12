"""Write a starter configuration routing one library to the installed harnesses."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Final

from skillsinc.config import TOML_RETIRED
from skillsinc.exceptions import ConfigurationError
from skillsinc.guidance import GuidanceInstaller
from skillsinc.paths import PROJECT_CONFIG, Environment, absolute
from skillsinc.profiles.registry import ProfileRegistry

HEADER: Final = """\
// SkillSinc configuration (JSONC: comments and trailing commas are allowed).
// Reference: https://github.com/mikejhill/skillsinc/blob/main/docs/guides/configuration.md
// Preview with `skillsinc plan`; nothing is written until `skillsinc sync`.
{
  "version": 1,
"""
PROJECT_NOTE: Final = (
    "  // Project configuration: destinations without a path default to the harness's",
    "  // folder in this project (.claude, .github, .vscode, .codex, .agents),"
    " relative",
    "  // paths resolve against the project root, and state is kept in"
    " .config/skillsinc/state.",
)


class StarterConfig:
    """Generate the file written by ``skillsinc init``."""

    def __init__(self, profiles: ProfileRegistry, environment: Environment) -> None:
        self._profiles = profiles
        self._environment = environment

    def write(
        self,
        path: Path,
        source: Path | None,
        *,
        force: bool,
        with_guidance: bool = False,
        project: Path | None = None,
    ) -> str:
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
        text = self.render(source, project=project, path=path)
        library = source or self._environment.default_library
        guidance = GuidanceInstaller()
        pending = guidance.plan(library, path) if with_guidance else ()
        guidance.install(pending)
        path.parent.mkdir(parents=True, exist_ok=True)
        if source is None:
            self._environment.default_library.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return text

    def render(
        self,
        source: Path | None,
        *,
        project: Path | None = None,
        path: Path | None = None,
    ) -> str:
        """Return starter JSONC, enabling destinations whose harness folder exists.

        A ``project`` starter lists the harnesses that have a project folder;
        their destinations default to that folder in the project. Written
        anywhere but ``<project>/.config/skillsinc/``, it names the project
        root explicitly, so it never loads as a user configuration.
        """
        source_path = self._display(source or self._environment.default_library)
        lines = [HEADER.rstrip("\n")]
        if project is not None:
            lines.extend(PROJECT_NOTE)
            root = self._project_key(project, path)
            if root is not None:
                lines.append(f'  "project": {json.dumps(root)},')
        lines.append('  "sources": {')
        if source is None:
            lines.append(
                "    // Default library. Add skills/, instructions/, agents/,"
                " prompts/, mcp/, resources/, hooks/, plugins/"
            )
        lines.extend(
            [f'    "library": {{ "path": {json.dumps(source_path)} }},', "  },"]
        )

        lines.append('  "destinations": {')
        defaults = [
            (profile, profile.default_path(self._environment))
            for profile in self._profiles.profiles
        ]
        detected = [
            profile.name
            for profile, default in defaults
            if default is not None and default.is_dir()
        ]
        if {"codex", "vscode"}.intersection(detected) and "agents" not in detected:
            detected.append("agents")
        if project is not None and "vscode" in detected and "copilot" not in detected:
            # In a project VS Code reads instructions, agents, and prompts from .github
            detected.append("copilot")
        for profile, default in defaults:
            if default is None or (project is not None and not profile.project_folder):
                continue
            installed = profile.name in detected
            prefix = "" if installed else "// "
            note = "" if installed else "  // not found on this machine"
            if project is not None and installed:
                note = f"  // {profile.project_folder}/"
            name = json.dumps(profile.name)
            lines.append(f'    {prefix}{name}: {{ "profile": {name} }},{note}')
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

    @staticmethod
    def _project_key(project: Path, path: Path | None) -> str | None:
        """Return the ``project`` value for a file outside the usual location."""
        if path is None:
            return None
        config = absolute(path)
        root = absolute(project)
        if config.parent == root / PROJECT_CONFIG:
            return None
        return Path(os.path.relpath(root, config.parent)).as_posix()

    def _display(self, path: Path) -> str:
        """Return ``path`` with forward slashes and ``~`` for the home directory."""
        absolute = path.expanduser().absolute()
        if absolute.is_relative_to(self._environment.home):
            return "~/" + absolute.relative_to(self._environment.home).as_posix()
        return absolute.as_posix()
