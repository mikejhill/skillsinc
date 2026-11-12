"""Source include filters and Terraform-like reconciliation of managed state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import Sandbox

from skillsinc.exceptions import ConfigurationError
from skillsinc.manifest import ManifestStore

pytestmark = pytest.mark.integration

SKILL = "---\nname: {name}\ndescription: {name}\n---\nUse {name}.\n"


class StateCase:
    """Write explicit configurations around the sandbox library."""

    @staticmethod
    def add_skills(sandbox: Sandbox, *names: str) -> None:
        """Add plain skills to the synthetic library."""
        for name in names:
            path = sandbox.library / "skills" / name / "SKILL.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(SKILL.format(name=name), encoding="utf-8")

    @staticmethod
    def configure(
        sandbox: Sandbox,
        destinations: tuple[str, ...] = ("target",),
        *,
        include: list[str] | None = None,
        disabled: list[str] | None = None,
        config: Path | None = None,
    ) -> Path:
        """Route the library to generic destinations under the sandbox home."""
        source: dict[str, object] = {"path": sandbox.library.as_posix()}
        if include is not None:
            source["include"] = include
        if disabled is not None:
            source["disabled"] = disabled
        data = {
            "version": 1,
            "sources": {"library": source},
            "destinations": {
                name: {"profile": "generic", "path": StateCase.root(sandbox, name)}
                for name in destinations
            },
            "routes": [
                {
                    "sources": ["library"],
                    "destinations": list(destinations),
                    "allow_lossy": True,
                }
            ]
            if destinations
            else [],
        }
        path = config or sandbox.config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    @staticmethod
    def root(sandbox: Sandbox, name: str) -> str:
        """Return the posix path of destination ``name``."""
        return (sandbox.environment.home / "dest" / name).as_posix()

    @staticmethod
    def skills(sandbox: Sandbox, name: str = "target") -> list[str]:
        """Return the skill directories present in destination ``name``."""
        skills = Path(StateCase.root(sandbox, name)) / "skills"
        return sorted(path.name for path in skills.iterdir()) if skills.is_dir() else []


class TestSourceInclude:
    """``sources.<name>.include`` narrows what a source offers."""

    def test_include_selects_named_resources_only(self, sandbox: Sandbox) -> None:
        """A list of names syncs exactly those skills and nothing else."""
        StateCase.add_skills(sandbox, "alpha", "beta", "gamma")
        StateCase.configure(sandbox, include=["skills/alpha", "skills/gamma"])

        status, output = sandbox.run("sync")

        assert status == 0, output
        assert StateCase.skills(sandbox) == ["alpha", "gamma"]
        root = Path(StateCase.root(sandbox, "target"))
        assert not (root / "agents").exists(), "Expected other kinds left out"

    def test_include_glob_selects_a_set(self, sandbox: Sandbox) -> None:
        """Glob patterns pick a set of resources."""
        StateCase.add_skills(sandbox, "alpha", "beta")
        StateCase.configure(sandbox, include=["skills/*", "prompts/ask"])

        assert sandbox.run("sync")[0] == 0
        root = Path(StateCase.root(sandbox, "target"))
        assert StateCase.skills(sandbox) == ["alpha", "beta", "sample"]
        assert (root / "prompts" / "ask.prompt.md").is_file()
        assert not (root / "agents").exists()

    def test_narrowing_include_removes_and_widening_restores(
        self, sandbox: Sandbox
    ) -> None:
        """Items left out of the filter are destroyed like deleted resources."""
        StateCase.add_skills(sandbox, "alpha", "beta")
        StateCase.configure(sandbox, include=["skills/alpha", "skills/beta"])
        assert sandbox.run("sync")[0] == 0

        StateCase.configure(sandbox, include=["skills/alpha"])
        status, output = sandbox.run("plan")
        assert status == 0, output
        assert "delete skills/beta/" in " ".join(output.split()), output
        assert sandbox.run("sync")[0] == 0
        assert StateCase.skills(sandbox) == ["alpha"]

        StateCase.configure(sandbox, include=["skills/*"])
        assert sandbox.run("sync")[0] == 0
        assert StateCase.skills(sandbox) == ["alpha", "beta", "sample"]

    def test_unmatched_selector_warns(self, sandbox: Sandbox) -> None:
        """A selector that matches nothing is reported, not silently ignored."""
        StateCase.configure(sandbox, include=["skills/sample", "skills/missing"])

        status, output = sandbox.run("sync", "--json")

        data = json.loads(output)
        assert status == 0, output
        assert data["warnings"] == [
            "sources.library.include: 'skills/missing' matches no item in the source"
        ]

    def test_disabled_overrides_include(self, sandbox: Sandbox) -> None:
        """A disabled selector wins over an include that names the same item."""
        StateCase.add_skills(sandbox, "alpha")
        StateCase.configure(sandbox, include=["skills/*"], disabled=["skills/alpha"])

        status, output = sandbox.run("plan", "--verbose")

        assert status == 0, output
        assert "skipped library:skills/alpha: disabled by" in output
        assert sandbox.run("sync")[0] == 0
        assert StateCase.skills(sandbox) == ["sample"]

    @pytest.mark.parametrize(
        ("include", "message"),
        [
            ([], "list at least one selector"),
            (["alpha"], "invalid selector 'alpha'"),
            (["widgets/alpha"], "invalid selector 'widgets/alpha'"),
            (["skills/../x"], "invalid selector"),
        ],
    )
    def test_invalid_include_is_refused(
        self, sandbox: Sandbox, include: list[str], message: str
    ) -> None:
        """Malformed selectors are configuration errors."""
        StateCase.configure(sandbox, include=include)

        with pytest.raises(
            ConfigurationError, match=r"sources\.library\.include"
        ) as err:
            sandbox.load()
        assert message in str(err.value)
        status, output = sandbox.run("plan")
        assert status == 2, output


class TestRemovedDestinations:
    """Destinations removed from the configuration are destroyed like resources."""

    def test_removed_destination_is_destroyed_and_forgotten(
        self, sandbox: Sandbox
    ) -> None:
        """Unedited output is deleted, edited output kept, and the state dropped."""
        StateCase.configure(sandbox, ("target", "old"))
        assert sandbox.run("sync")[0] == 0
        old = Path(StateCase.root(sandbox, "old"))
        edited = old / "prompts" / "ask.prompt.md"
        edited.write_text("Mine now.\n", encoding="utf-8")
        (old / "notes.txt").write_text("Unmanaged.\n", encoding="utf-8")
        store = ManifestStore(sandbox.environment.state_directory)

        StateCase.configure(sandbox, ("target",))
        status, output = sandbox.run("plan")
        assert status == 0, output
        assert "old (removed from configuration)" in output
        assert store.path("old").is_file(), "Expected plan to keep the state"

        status, output = sandbox.run("sync", "--json")

        assert status == 0, output
        retired = next(
            d for d in json.loads(output)["destinations"] if d["name"] == "old"
        )
        assert retired["retired"] is True
        assert retired["profile"] is None
        assert not (old / "skills").exists(), "Expected managed skill deleted"
        assert edited.read_text(encoding="utf-8") == "Mine now.\n"
        assert (old / "notes.txt").is_file(), "Expected unmanaged file kept"
        assert not store.path("old").exists(), "Expected state forgotten"
        assert StateCase.skills(sandbox) == ["sample"]
        status, output = sandbox.run("sync")
        assert status == 0, output
        assert "old" not in output

    def test_destination_with_no_routes_is_emptied(self, sandbox: Sandbox) -> None:
        """A configured destination nothing routes to keeps no managed output."""
        StateCase.configure(sandbox, ("target",))
        assert sandbox.run("sync")[0] == 0
        data = json.loads(sandbox.config_path.read_text(encoding="utf-8"))
        data["routes"] = []
        sandbox.config_path.write_text(json.dumps(data), encoding="utf-8")

        assert sandbox.run("sync")[0] == 0

        assert StateCase.skills(sandbox) == []

    def test_renamed_destination_blocks_until_state_is_moved(
        self, sandbox: Sandbox
    ) -> None:
        """A removed destination sharing a configured path is never destroyed."""
        StateCase.configure(sandbox, ("old",))
        assert sandbox.run("sync")[0] == 0
        data = json.loads(sandbox.config_path.read_text(encoding="utf-8"))
        data["destinations"] = {"new": data["destinations"]["old"]}
        data["routes"][0]["destinations"] = ["new"]
        sandbox.config_path.write_text(json.dumps(data), encoding="utf-8")
        store = ManifestStore(sandbox.environment.state_directory)

        status, output = sandbox.run("sync")

        assert status == 1, output
        assert "removed destination 'old' owns files" in output
        assert StateCase.skills(sandbox, "old") == ["sample"]
        store.path("old").rename(store.path("new"))
        status, output = sandbox.run("sync")
        assert status == 0, output
        assert StateCase.skills(sandbox, "old") == ["sample"]

    def test_only_does_not_destroy_removed_destinations(self, sandbox: Sandbox) -> None:
        """``--destination`` limits the run to the named destinations."""
        StateCase.configure(sandbox, ("target", "old"))
        assert sandbox.run("sync")[0] == 0
        StateCase.configure(sandbox, ("target",))

        assert sandbox.run("sync", "--destination", "target")[0] == 0

        assert StateCase.skills(sandbox, "old") == ["sample"]

    def test_another_configurations_state_is_left_alone(self, sandbox: Sandbox) -> None:
        """Manifests recorded by a different, existing configuration are not ours."""
        other = sandbox.environment.home / "other.json"
        StateCase.configure(sandbox, ("old",), config=other)
        assert sandbox.run("sync", "--config", str(other))[0] == 0
        StateCase.configure(sandbox, ("target",))

        status, output = sandbox.run("sync")

        assert status == 0, output
        assert "old" not in output
        assert StateCase.skills(sandbox, "old") == ["sample"]

    def test_unowned_state_warns_without_touching_files(self, sandbox: Sandbox) -> None:
        """State from before ownership was recorded, or a lost config, only warns."""
        StateCase.configure(sandbox, ("old",))
        assert sandbox.run("sync")[0] == 0
        store = ManifestStore(sandbox.environment.state_directory)
        data = json.loads(store.path("old").read_text(encoding="utf-8"))
        del data["config"]
        store.path("old").write_text(json.dumps(data), encoding="utf-8")
        StateCase.configure(sandbox, ("target",))

        status, output = sandbox.run("sync")

        assert status == 0, output
        assert "state for destination 'old'" in output
        assert "belongs to no known configuration" in output
        assert StateCase.skills(sandbox, "old") == ["sample"]

    def test_unreadable_removed_state_warns(self, sandbox: Sandbox) -> None:
        """A corrupt manifest of a removed destination is reported, not guessed at."""
        StateCase.configure(sandbox, ("target",))
        store = ManifestStore(sandbox.environment.state_directory)
        store.path("old").parent.mkdir(parents=True, exist_ok=True)
        store.path("old").write_text("{", encoding="utf-8")

        status, output = sandbox.run("sync")

        assert status == 0, output
        assert "ownership manifest is unreadable" in output

    def test_up_to_date_destination_records_its_configuration(
        self, sandbox: Sandbox
    ) -> None:
        """Older manifests are claimed by the next sync even with no changes."""
        StateCase.configure(sandbox, ("target",))
        assert sandbox.run("sync")[0] == 0
        store = ManifestStore(sandbox.environment.state_directory)
        data = json.loads(store.path("target").read_text(encoding="utf-8"))
        del data["config"]
        store.path("target").write_text(json.dumps(data), encoding="utf-8")

        status, output = sandbox.run("sync")

        assert status == 0, output
        recorded = json.loads(store.path("target").read_text(encoding="utf-8"))
        assert recorded["config"] == str(sandbox.config_path.resolve())

    def test_removed_destination_with_nothing_owned_is_forgotten(
        self, sandbox: Sandbox
    ) -> None:
        """An empty manifest of a removed destination is simply dropped."""
        StateCase.configure(sandbox, ("target",))
        store = ManifestStore(sandbox.environment.state_directory)
        store.path("old").parent.mkdir(parents=True, exist_ok=True)
        store.path("old").write_text(
            json.dumps(
                {
                    "version": 2,
                    "destination": "old",
                    "root": StateCase.root(sandbox, "old"),
                    "config": str(sandbox.config_path.resolve()),
                    "files": {},
                    "blocks": {},
                }
            ),
            encoding="utf-8",
        )

        status, output = sandbox.run("sync")

        assert status == 0, output
        assert not store.path("old").exists()

    def test_removed_claude_destination_releases_entries_beside_its_root(
        self, sandbox: Sandbox
    ) -> None:
        """Entries owned in ``~/.claude.json`` go with a removed destination."""
        home = sandbox.environment.home
        claude_json = home / ".claude.json"
        claude_json.write_text('{"numStartups": 3}', encoding="utf-8")
        server = sandbox.library / "mcp" / "github.json"
        server.parent.mkdir()
        server.write_text('{"command": "gh"}', encoding="utf-8")
        sandbox.root = home / ".claude"
        sandbox.configure("claude", allow_lossy=True)
        assert sandbox.run("sync")[0] == 0
        assert "github" in claude_json.read_text(encoding="utf-8")
        StateCase.configure(sandbox, ("kept",))

        status, output = sandbox.run("sync")

        assert status == 0, output
        remaining = json.loads(claude_json.read_text(encoding="utf-8"))
        assert remaining["numStartups"] == 3
        assert "github" not in remaining.get("mcpServers", {})
        assert not (home / ".claude" / "skills").exists()

    def test_nothing_is_recorded_for_a_destination_never_written(
        self, sandbox: Sandbox
    ) -> None:
        """An up-to-date destination without state gets no manifest."""
        StateCase.configure(sandbox, ("target",))
        data = json.loads(sandbox.config_path.read_text(encoding="utf-8"))
        data["routes"] = []
        sandbox.config_path.write_text(json.dumps(data), encoding="utf-8")

        assert sandbox.run("sync")[0] == 0

        store = ManifestStore(sandbox.environment.state_directory)
        assert store.names() == []

    def test_manifest_with_a_malformed_owner_is_unreadable(
        self, sandbox: Sandbox
    ) -> None:
        """A non-string configuration path in a manifest is corruption."""
        StateCase.configure(sandbox, ("target",))
        assert sandbox.run("sync")[0] == 0
        store = ManifestStore(sandbox.environment.state_directory)
        data = json.loads(store.path("target").read_text(encoding="utf-8"))
        data["config"] = 7
        store.path("target").write_text(json.dumps(data), encoding="utf-8")

        status, output = sandbox.run("sync")

        assert status == 1, output
        assert "config must be a string" in output
