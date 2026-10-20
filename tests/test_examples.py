"""Scheduling examples stay parseable and run `skillsinc sync`."""

from __future__ import annotations

import configparser
import plistlib
from pathlib import Path

EXAMPLES = Path(__file__).parent.parent / "examples" / "scheduling"


def _unit(name: str) -> configparser.ConfigParser:
    """Parse a systemd unit file as INI; keys are lowercased."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string((EXAMPLES / name).read_text(encoding="utf-8"))
    return parser


def test_launchd_agent_runs_sync_from_substituted_home() -> None:
    """The plist parses, runs `sync`, and uses only the @HOME@ placeholder."""
    raw = (EXAMPLES / "io.github.mikejhill.skillsinc.plist").read_text(encoding="utf-8")
    agent = plistlib.loads(raw.replace("@HOME@", "/Users/example").encode())
    arguments = agent["ProgramArguments"]
    assert arguments == ["/Users/example/.local/bin/skillsinc", "sync"], (
        f"expected skillsinc sync, got {arguments}"
    )
    assert agent["Label"] == "io.github.mikejhill.skillsinc", (
        f"expected label to match the file name, got {agent['Label']}"
    )
    paths = [arguments[0], agent["StandardOutPath"], agent["StandardErrorPath"]]
    assert all(path.startswith("/Users/example/") for path in paths), (
        f"expected absolute paths because launchd does not expand ~, got {paths}"
    )


def test_systemd_timer_starts_oneshot_sync_service() -> None:
    """The service runs `sync` once and the timer is installable."""
    service = _unit("skillsinc.service")
    timer = _unit("skillsinc.timer")
    exec_start = service["Service"]["execstart"]
    assert exec_start == "%h/.local/bin/skillsinc sync", (
        f"expected skillsinc sync under %h, got {exec_start}"
    )
    assert service["Service"]["type"] == "oneshot", (
        f"expected oneshot, got {service['Service']['type']}"
    )
    assert timer["Install"]["wantedby"] == "timers.target", (
        f"expected timers.target, got {timer['Install']['wantedby']}"
    )


def test_task_scheduler_script_runs_sync_and_supports_what_if() -> None:
    """The registration script runs `sync` and previews with -WhatIf."""
    script = (EXAMPLES / "Register-SkillSincTask.ps1").read_text(encoding="utf-8")
    assert "SupportsShouldProcess" in script, "expected -WhatIf support"
    assert '`" sync $Arguments' in script, "expected the task to run skillsinc sync"
