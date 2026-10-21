"""Shared isolated libraries, environments, and typed test builders."""

from __future__ import annotations

import io
import json
import logging
import stat
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from skillsinc.__main__ import Application
from skillsinc.config import Config, ConfigLoader, SourceConfig
from skillsinc.frontmatter import FrontmatterCodec
from skillsinc.models import Asset, Customization, Frontmatter, Kind
from skillsinc.paths import Environment
from skillsinc.profiles.registry import ProfileRegistry


class Builder:
    """Valid customization and configuration defaults with explicit overrides."""

    @staticmethod
    def item(  # noqa: PLR0913 - explicit builder overrides
        kind: Kind = Kind.SKILL,
        *,
        name: str = "sample",
        source: str = "library",
        frontmatter: Frontmatter | None = None,
        body: str = "Body.\n",
        assets: tuple[Asset, ...] = (),
        plugin: str | None = None,
    ) -> Customization:
        """Build an authored item using the production Markdown codec."""
        header = frontmatter if frontmatter is not None else {"description": "Sample"}
        codec = FrontmatterCodec()
        document = codec.parse(codec.render(header, body))
        file_name = "SKILL.md" if kind is Kind.SKILL else f"{name}.md"
        return Customization(
            kind, name, source, document, file_name, assets, plugin=plugin
        )

    @staticmethod
    def source(path: Path, *, name: str = "library") -> SourceConfig:
        """Build a source using standard kind directories."""
        return SourceConfig(name, path, {kind: kind.value for kind in Kind})

    @staticmethod
    def write_config(
        path: Path,
        library: Path,
        destinations: tuple[tuple[str, str, Path], ...],
        *,
        allow_lossy: bool = False,
        include: tuple[str, ...] = ("*/*",),
    ) -> Path:
        """Write a JSONC route from a library to explicitly named destinations."""
        config = {
            "version": 1,
            "sources": {"library": {"path": library.as_posix()}},
            "destinations": {
                name: {"profile": profile, "path": root.as_posix()}
                for name, profile, root in destinations
            },
            "routes": [
                {
                    "sources": ["library"],
                    "destinations": [name for name, _, _ in destinations],
                    "include": list(include),
                    "allow_lossy": allow_lossy,
                }
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "// written by the test suite\n" + json.dumps(config, indent=2) + "\n",
            encoding="utf-8",
        )
        return path


class Sandbox:
    """Application journey driver bound to temporary config and state roots."""

    def __init__(self, environment: Environment, library: Path) -> None:
        """Store the isolated environment and authored library."""
        self.environment = environment
        self.library = library
        self.config_path = environment.default_config_file
        self.root = environment.home / "destination"

    def configure(
        self, profile: str = "generic", *, allow_lossy: bool = False
    ) -> Config:
        """Write and load a single-destination route."""
        Builder.write_config(
            self.config_path,
            self.library,
            (("target", profile, self.root),),
            allow_lossy=allow_lossy,
        )
        return self.load()

    def load(self) -> Config:
        """Load the sandbox configuration using built-in profiles."""
        return ConfigLoader(ProfileRegistry(), self.environment).load(self.config_path)

    def run(self, *argv: str) -> tuple[int, str]:
        """Execute one command and capture its public stdout."""
        out = io.StringIO()
        status = Application(self.environment, out=out).run(list(argv))
        return status, out.getvalue()


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """Build all four customization kinds, nested assets, and an executable script."""
    root = tmp_path / "library"
    files = {
        "skills/sample/SKILL.md": (
            "---\nname: sample\ndescription: Sample skill\n--"
            "-\nUse references/guide.md.\n"
        ),
        "skills/sample/references/guide.md": "Reference.\n",
        "skills/sample/scripts/run.sh": "#!/bin/sh\nexit 0\n",
        "instructions/scoped.instructions.md": (
            "---\napplyTo: '**/*.py'\n---\nUse types.\n"
        ),
        "instructions/global.md": "Global guidance.\n",
        "agents/reviewer.agent.md": (
            "---\ndescription: Review code\ntools: [read, se"
            "arch]\nmodel: chosen\n---\nReview.\n"
        ),
        "prompts/ask.prompt.md": (
            "---\ndescription: Ask a question\n---\nAnswer ${input:x}.\n"
        ),
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    script = root / "skills/sample/scripts/run.sh"
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return root


@pytest.fixture
def environment(tmp_path: Path) -> Environment:
    """Provide a fake home and absolute XDG roots without process variables."""
    home = tmp_path / "home"
    home.mkdir()
    return Environment(
        home,
        {
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
        },
        sys.platform,
    )


@pytest.fixture
def sandbox(environment: Environment, library: Path) -> Sandbox:
    """Provide an application driver isolated from the user's home."""
    return Sandbox(environment, library)


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Restore logging after Application reconfigures the process boundary."""
    root = logging.getLogger()
    handlers = root.handlers[:]
    level = root.level
    yield
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers = handlers
    root.setLevel(level)
