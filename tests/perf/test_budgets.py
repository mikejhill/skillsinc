"""Time budgets for planning and syncing a large library into every harness.

Budgets are several times the time measured on a GitHub-hosted runner, so
they catch an accidental quadratic step or a per-file slowdown, not noise.
Each measurement takes the best of a few runs. Run with ``uv run poe perf``.
"""

from __future__ import annotations

import io
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest
from tests.conftest import Builder

from skillsinc.__main__ import Application
from skillsinc.paths import Environment

PROFILES: Final = ("claude", "copilot", "codex", "vscode", "agents")
# Items of each kind: well beyond a large personal library
SIZE: Final = 100
REPEATS: Final = 3

# Seconds, for SIZE items of each kind into every profile
FIRST_SYNC_BUDGET: Final = 5.0
CONVERGED_PLAN_BUDGET: Final = 3.0
# A converged plan of twice the items may take at most this many times as long
SCALING_LIMIT: Final = 3.0
# A first sync of four times the items may take at most this many times as
# long. Linear growth measures about 4-5x; the quadratic first sync of
# issue #25 measured about 10x.
FIRST_SYNC_GROWTH: Final = 4
FIRST_SYNC_SCALING_LIMIT: Final = 7.0


def _write_library(root: Path, size: int) -> Path:
    """Write ``size`` items of each kind, skills with supporting files."""
    for index in range(size):
        files = {
            f"skills/skill-{index}/SKILL.md": (
                f"---\nname: skill-{index}\ndescription: Skill {index}\n---\n"
                "Read references/guide.md.\n"
            ),
            f"skills/skill-{index}/references/guide.md": f"Guide {index}.\n" * 20,
            f"skills/skill-{index}/scripts/run.py": "print('ok')\n",
            f"instructions/rule-{index}.instructions.md": (
                f"---\napplyTo: '**/*.py'\n---\nRule {index}.\n"
            ),
            f"agents/agent-{index}.agent.md": (
                f"---\ndescription: Agent {index}\n---\nAct {index}.\n"
            ),
            f"prompts/prompt-{index}.prompt.md": (
                f"---\ndescription: Prompt {index}\n---\nAsk {index}.\n"
            ),
            f"mcp/server-{index}.json": f'{{"type": "local", "command": "s{index}"}}',
        }
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    return root


def _application(root: Path, size: int, run: int = 0) -> Callable[[str], int]:
    """Configure ``size`` items per kind into every profile; return a runner."""
    base = root / f"size-{size}-{run}"
    environment = Environment(
        base / "home",
        {
            "XDG_CONFIG_HOME": str(base / "config"),
            "XDG_STATE_HOME": str(base / "state"),
        },
        sys.platform,
    )
    environment.home.mkdir(parents=True)
    library = _write_library(base / "library", size)
    destinations = tuple((name, name, environment.home / name) for name in PROFILES)
    config = base / "config.json"
    Builder.write_config(config, library, destinations, allow_lossy=True)

    def run(command: str) -> int:
        out = io.StringIO()
        argv = [command, "--config", str(config)]
        return Application(environment, out=out).run(argv)

    return run


def _time(action: Callable[[], int]) -> float:
    """Return how long one successful run takes, in seconds."""
    start = time.perf_counter()
    status = action()
    elapsed = time.perf_counter() - start
    assert status == 0, f"Expected exit 0 while timing, got {status}"
    return elapsed


def _best(action: Callable[[], int]) -> float:
    """Return the fastest of ``REPEATS`` successful runs, in seconds."""
    return min(_time(action) for _ in range(REPEATS))


def _best_first_sync(root: Path, size: int) -> float:
    """Return the fastest of ``REPEATS`` first syncs, each into empty folders."""
    runs = [_application(root, size, index) for index in range(REPEATS)]
    return min(_time(lambda run=run: run("sync")) for run in runs)


def test_first_sync_fits_the_budget(tmp_path: Path) -> None:
    """Writing every item into every profile stays within budget."""
    run = _application(tmp_path, SIZE)

    start = time.perf_counter()
    status = run("sync")
    elapsed = time.perf_counter() - start

    assert status == 0, f"Expected the first sync to succeed, got {status}"
    assert elapsed < FIRST_SYNC_BUDGET, (
        f"Expected first sync under {FIRST_SYNC_BUDGET}s, took {elapsed:.2f}s"
    )


def test_first_sync_scales_linearly(tmp_path: Path) -> None:
    """Growing the library grows a first sync in proportion (issue #25)."""
    small = _best_first_sync(tmp_path, SIZE)
    large = _best_first_sync(tmp_path, SIZE * FIRST_SYNC_GROWTH)

    ratio = large / small

    assert ratio < FIRST_SYNC_SCALING_LIMIT, (
        f"Expected under {FIRST_SYNC_SCALING_LIMIT}x time for"
        f" {FIRST_SYNC_GROWTH}x items, got {ratio:.2f}x"
        f" ({small:.2f}s to {large:.2f}s)"
    )


def test_converged_plan_fits_the_budget(tmp_path: Path) -> None:
    """Planning an up-to-date library (the scheduled case) stays within budget."""
    run = _application(tmp_path, SIZE)
    assert run("sync") == 0, "Expected the first sync to succeed"

    elapsed = _best(lambda: run("plan"))

    assert elapsed < CONVERGED_PLAN_BUDGET, (
        f"Expected a converged plan under {CONVERGED_PLAN_BUDGET}s, took {elapsed:.2f}s"
    )


@pytest.mark.parametrize("size", [SIZE])
def test_converged_plan_scales_linearly(tmp_path: Path, size: int) -> None:
    """Doubling the library at most roughly doubles a converged plan's time."""
    small = _application(tmp_path, size)
    large = _application(tmp_path, size * 2)
    assert (small("sync"), large("sync")) == (0, 0), "Expected both syncs to succeed"

    ratio = _best(lambda: large("plan")) / _best(lambda: small("plan"))

    assert ratio < SCALING_LIMIT, (
        f"Expected under {SCALING_LIMIT}x time for 2x items, got {ratio:.2f}x"
    )
