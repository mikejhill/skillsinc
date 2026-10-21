"""Drive the real ``skillsinc`` process against a throwaway home directory.

These tests start the command as a separate process, the way a user or a
scheduler does, so they cover the console entry point, argument parsing,
stdout and stderr, exit codes, and the platform's real home and XDG lookup.

``SKILLSINC_E2E_COMMAND`` names the executable to test, for example the
``skillsinc`` script of a freshly installed wheel. Without it the tests run
``python -m skillsinc`` from the current environment.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

TIMEOUT_SECONDS = 60

# Variables that would point the process at the real user's files
HOME_VARIABLES = (
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
)


@dataclass(frozen=True, slots=True)
class Result:
    """One finished process: exit status and decoded output streams."""

    status: int
    stdout: str
    stderr: str

    def json(self) -> Any:  # noqa: ANN401 - report shape is checked by each test
        """Decode stdout as the JSON report."""
        return json.loads(self.stdout)


class Cli:
    """Run ``skillsinc`` in a child process whose home is ``home``."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.command = self._command()
        self.environment = self._environment()

    @property
    def config_file(self) -> Path:
        """Return the default configuration path the process resolves."""
        return self.home / ".config" / "skillsinc" / "config.toml"

    @property
    def state_directory(self) -> Path:
        """Return the default state directory the process resolves."""
        return self.home / ".local" / "state" / "skillsinc"

    def run(self, *argv: str) -> Result:
        """Run one command to completion and capture its output."""
        completed = subprocess.run(  # noqa: S603 - argv is built by the test
            [*self.command, *argv],
            capture_output=True,
            check=False,
            cwd=self.home,
            encoding="utf-8",
            env=self.environment,
            timeout=TIMEOUT_SECONDS,
        )
        return Result(completed.returncode, completed.stdout, completed.stderr)

    @staticmethod
    def _command() -> list[str]:
        """Return the installed executable, else this interpreter's module."""
        configured = os.environ.get("SKILLSINC_E2E_COMMAND", "")
        if configured:
            return shlex.split(configured, posix=sys.platform != "win32")
        return [sys.executable, "-m", "skillsinc"]

    def _environment(self) -> dict[str, str]:
        """Return the process environment with every home lookup in ``home``."""
        variables = {
            key: value
            for key, value in os.environ.items()
            if key.upper() not in HOME_VARIABLES
        }
        variables |= {
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "APPDATA": str(self.home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(self.home / "AppData" / "Local"),
            "PYTHONUTF8": "1",
            "NO_COLOR": "1",
        }
        return variables


class Harnesses:
    """Create the folders that make ``init`` detect each harness."""

    @staticmethod
    def vscode_prompts(home: Path) -> Path:
        """Return VS Code's ``User/prompts`` folder for the running platform."""
        if sys.platform == "win32":
            base = home / "AppData" / "Roaming"
        elif sys.platform == "darwin":
            base = home / "Library" / "Application Support"
        else:
            base = home / ".config"
        return base / "Code" / "User" / "prompts"

    @classmethod
    def install(cls, home: Path) -> None:
        """Create every harness's home folder."""
        for name in (".claude", ".copilot", ".codex", ".agents"):
            (home / name).mkdir()
        cls.vscode_prompts(home).mkdir(parents=True)


def write_library(root: Path) -> Path:
    """Write a library holding one item of every kind the harnesses share."""
    files = {
        "skills/review/SKILL.md": (
            "---\nname: review\ndescription: Review a change\n---\n"
            "Read references/checklist.md first.\n"
        ),
        "skills/review/references/checklist.md": "- Tests pass\n",
        "instructions/python.instructions.md": (
            "---\napplyTo: '**/*.py'\n---\nUse type hints.\n"
        ),
        "agents/reviewer.agent.md": (
            "---\ndescription: Reviews code\n---\nReview carefully.\n"
        ),
        "prompts/explain.prompt.md": (
            "---\ndescription: Explain code\n---\nExplain the selection.\n"
        ),
        "mcp/github.json": '{"type": "local", "command": "gh-mcp"}\n',
        "INTENT.md": "Never distributed.\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    return root


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """Provide an empty home directory for the child process."""
    root = tmp_path / "home"
    root.mkdir()
    return root


@pytest.fixture
def cli(home: Path) -> Cli:
    """Provide a process driver bound to the throwaway home."""
    return Cli(home)


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """Provide a library with one item of every shared kind."""
    return write_library(tmp_path / "library")
