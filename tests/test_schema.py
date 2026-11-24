"""The bundled JSON Schema matches the loader, and sync keeps a copy beside the file."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from tests.conftest import Builder, Sandbox

from skillsinc.config import ConfigLoader
from skillsinc.entries import JsonDocument
from skillsinc.exceptions import ConfigurationError
from skillsinc.jsonc import Jsonc
from skillsinc.models import Kind
from skillsinc.paths import Environment
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.schema import SCHEMA_FILE, ConfigSchema, SchemaState
from skillsinc.starter import StarterConfig
from skillsinc.upgrades import ConfigMaintenance, version_key

GUIDE = Path(__file__).parent.parent / "docs" / "guides" / "configuration.md"


def _schema() -> dict[str, Any]:
    """Return the bundled schema."""
    return cast("dict[str, Any]", json.loads(ConfigSchema.text()))


def _parse(text: str) -> object:
    """Parse JSONC the way the loader does."""
    plain = Jsonc.blank_trailing_commas(Jsonc.blank_comments(text))
    return dict(JsonDocument.load(plain.encode()))


def _errors(document: object) -> list[str]:
    """Return schema validation messages for ``document``."""
    return [
        error.message for error in Draft202012Validator(_schema()).iter_errors(document)
    ]


class TestBundledSchema:
    """The schema is valid, allows comments, and names what the loader accepts."""

    def test_schema_is_valid_and_allows_comments(self) -> None:
        """VS Code reads allowComments and allowTrailingCommas from the schema."""
        schema = _schema()
        Draft202012Validator.check_schema(schema)
        assert schema["allowComments"] is True, "Expected comments allowed"
        assert schema["allowTrailingCommas"] is True, "Expected trailing commas"

    def test_profiles_and_layout_match_the_code(self) -> None:
        """Profile names and layout keys follow the registry and kinds."""
        definitions = _schema()["$defs"]
        profiles = definitions["destination"]["properties"]["profile"]["enum"]
        layout = definitions["source"]["properties"]["layout"]["properties"]
        expected = [profile.name for profile in ProfileRegistry().profiles]
        assert profiles == expected, f"Expected {expected}, got {profiles}"
        kinds = {kind.value for kind in Kind} | {"plugins"}
        assert set(layout) == kinds, f"Expected {kinds}, got {set(layout)}"

    def test_guide_example_validates(self) -> None:
        """The configuration guide's example passes the schema."""
        guide = GUIDE.read_text(encoding="utf-8")
        match = re.search(r"## Example\n\n```jsonc\n(.*?)```", guide, re.DOTALL)
        assert match is not None, "Expected an example block in the guide"
        errors = _errors(_parse(match.group(1)))
        assert not errors, f"Expected the guide example to validate, got {errors}"

    @pytest.mark.parametrize(
        "document",
        [
            {"version": 2},
            {"version": 1, "sourcse": {}},
            {"version": 1, "sources": {"a": {"path": "x", "layuot": {}}}},
            {"version": 1, "sources": {"a": {"github": "not a repo"}}},
            {"version": 1, "sources": {"a": {"path": "x", "include": ["skills"]}}},
            {"version": 1, "sources": {"a": {"path": "x", "refresh": "soon"}}},
            {"version": 1, "destinations": {"a": {"profile": "cursor"}}},
            {"version": 1, "destinations": {"a": {"enabled": "no"}}},
            {"version": 1, "routes": [{"sources": ["a"]}]},
            {"version": 1, "routes": [{"sources": [], "destinations": ["b"]}]},
            {"version": 1, "project": None},
            {"version": 1, "sources": {"a": {}}},
            {"version": 1, "sources": {"a": {"path": "x", "github": "o/r"}}},
            {"version": 1, "sources": {"a": {"path": "x", "ref": "main"}}},
            {"version": 1, "sources": {"a": {"path": "x", "include": ["skill/x"]}}},
            {"version": 1, "sources": {"a": {"path": "x", "disabled": ["skills/.."]}}},
            {"version": 1, "sources": {"a": {"path": "x", "include": ["skills/ "]}}},
            {"version": 1, "destinations": {"a": {"profile": "generic"}}},
            {"version": 1, "destinations": {"generic": {}}},
        ],
    )
    def test_schema_flags_what_the_loader_refuses(
        self, tmp_path: Path, environment: Environment, document: object
    ) -> None:
        """An editor warns about mistakes that would fail `plan`."""
        path = tmp_path / "config.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(ConfigurationError):
            ConfigLoader(ProfileRegistry(), environment).load(path)
        assert _errors(document), f"Expected the schema to reject {document}"

    @pytest.mark.parametrize(
        "document",
        [
            {"version": 1, "sources": {"a": {"github": "o/r", "refresh": "1H"}}},
            {"version": 1, "sources": {"a": {"github": "o/r", "refresh": " 15m "}}},
            {"version": 1, "sources": {"a": {"git": "https://h/r.git", "ref": "v1"}}},
            {"version": 1, "sources": {"a": {"github": "o/r", "refresh": "0:15"}}},
            {
                "version": 1,
                "sources": {
                    "a": {"path": "x", "include": ["*/*", "plugins/kit/skills/*"]}
                },
            },
            {"version": 1, "destinations": {"generic": {"path": "out"}}},
            {"version": 1, "destinations": {"generic": {"profile": "claude"}}},
        ],
    )
    def test_schema_accepts_what_the_loader_accepts(
        self, tmp_path: Path, environment: Environment, document: dict[str, Any]
    ) -> None:
        """Spellings the loader normalizes are not flagged."""
        path = tmp_path / "config.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        ConfigLoader(ProfileRegistry(), environment).load(path)
        assert not _errors(document), f"Expected {document} to validate"


class TestStarter:
    """`init` names the schema and writes it beside the file."""

    @pytest.mark.parametrize("project", [False, True])
    @pytest.mark.parametrize("harness", [None, "claude"])
    def test_starter_validates(
        self,
        tmp_path: Path,
        environment: Environment,
        harness: str | None,
        *,
        project: bool,
    ) -> None:
        """Every starter passes the schema and loads with `$schema` set."""
        profiles = ProfileRegistry()
        if harness is not None:
            default = profiles.get(harness).default_path(environment)
            assert default is not None
            default.mkdir(parents=True)
        root = tmp_path / "repo"
        root.mkdir()
        path = root / ".config/skillsinc/config.json"

        text = StarterConfig(profiles, environment).write(
            path, None, force=False, project=root if project else None
        )

        errors = _errors(_parse(text))
        assert not errors, f"Expected the starter to validate, got {errors}"
        loaded = ConfigLoader(profiles, environment).load(path)
        assert loaded.schema == f"./{SCHEMA_FILE}", f"Got {loaded.schema!r}"

    def test_init_writes_schema(self, sandbox: Sandbox) -> None:
        """`init` writes the bundled schema beside the file."""
        status, output = sandbox.run("init")

        assert status == 0, f"Expected init to succeed, got {output}"
        schema = ConfigSchema.path(sandbox.config_path)
        assert schema.read_text(encoding="utf-8") == ConfigSchema.text()

    def test_init_schema_failure_warns(self, sandbox: Sandbox) -> None:
        """A blocked schema path is a warning; the configuration is still written."""
        ConfigSchema.path(sandbox.config_path).mkdir(parents=True)

        status, output = sandbox.run("init")

        assert status == 0, f"Expected init to succeed, got {output}"
        assert sandbox.config_path.is_file(), "Expected the configuration written"
        assert "warning: could not write" not in output, "Directory is foreign"

    def test_init_write_error_warns(
        self, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed schema write after the config is written is a warning."""

        def fail(_config: Path) -> bool:
            """Simulate a full disk."""
            raise OSError("synthetic full disk")

        monkeypatch.setattr(ConfigSchema, "refresh", staticmethod(fail))
        status, output = sandbox.run("init")

        assert status == 0, f"Expected init to succeed, got {output}"
        assert sandbox.config_path.is_file(), "Expected the configuration written"
        assert "warning: could not write" in output, output


class TestSyncRefresh:
    """`sync` keeps the local schema current only when the file names it."""

    @staticmethod
    def _configure(sandbox: Sandbox, reference: str | None) -> Path:
        """Write a sandbox config, optionally naming a schema."""
        path = Builder.write_config(
            sandbox.config_path, sandbox.library, (("target", "generic", sandbox.root),)
        )
        if reference is not None:
            text = path.read_text(encoding="utf-8")
            path.write_text(
                text.replace("{", f'{{\n  "$schema": {json.dumps(reference)},', 1),
                encoding="utf-8",
            )
        return path.parent / SCHEMA_FILE

    @pytest.mark.parametrize("reference", ["./config.schema.json", SCHEMA_FILE])
    def test_sync_writes_missing_schema(self, sandbox: Sandbox, reference: str) -> None:
        """A config naming the local schema gets it on the next sync, not plan."""
        schema = self._configure(sandbox, reference)

        assert sandbox.run("plan")[0] == 0, "Expected plan to succeed"
        assert not schema.exists(), "Expected plan to write nothing"
        status, output = sandbox.run("sync")

        assert status == 0, f"Expected sync to succeed, got {output}"
        assert schema.read_text(encoding="utf-8") == ConfigSchema.text()

    def test_sync_leaves_other_references_alone(self, sandbox: Sandbox) -> None:
        """A file naming another schema is not edited and gets no local copy."""
        schema = self._configure(sandbox, "https://example.com/other.json")
        before = sandbox.config_path.read_bytes()

        assert sandbox.run("sync")[0] == 0, "Expected sync to succeed"
        assert not schema.exists(), "Expected no schema file"
        assert sandbox.config_path.read_bytes() == before, "Expected no edit"

    def test_sync_adds_missing_reference_once(self, sandbox: Sandbox) -> None:
        """An existing file gains `$schema` on the first sync, and never again."""
        schema = self._configure(sandbox, None)
        before = sandbox.config_path.read_text(encoding="utf-8")

        status, output = sandbox.run("plan")
        assert status == 0, f"Expected plan to succeed, got {output}"
        assert 'add "$schema": "./config.schema.json"' in output, output
        assert f"create {schema}" in output, output
        assert sandbox.config_path.read_text(encoding="utf-8") == before
        assert not schema.exists(), "Expected plan to write nothing"
        status, output = sandbox.run("sync")

        assert status == 0, f"Expected sync to succeed, got {output}"
        assert "Configuration:" in output, f"Expected a section, got {output}"
        after = sandbox.config_path.read_text(encoding="utf-8")
        expected = before.replace("{", '{\n  "$schema": "./config.schema.json",', 1)
        assert after == expected, f"Expected one inserted line, got {after}"
        assert schema.read_text(encoding="utf-8") == ConfigSchema.text()
        record = ConfigMaintenance(sandbox.environment.state_directory)
        assert record.applied_version(sandbox.config_path) is not None

        # The user opts out by removing the key; later syncs respect that
        sandbox.config_path.write_text(before, encoding="utf-8")
        status, output = sandbox.run("sync")
        assert status == 0, f"Expected sync to succeed, got {output}"
        assert sandbox.config_path.read_text(encoding="utf-8") == before
        assert "Configuration:" not in output, output

    def test_upgrade_runs_after_an_older_record(self, sandbox: Sandbox) -> None:
        """A config last upgraded by 0.7.x gets the 0.8.0 edit, then never again."""
        self._configure(sandbox, None)
        state = sandbox.environment.state_directory
        key = sandbox.config_path.absolute().as_posix()
        record = {"version": 1, "configs": {key: "0.7.0"}}
        (state / "upgrades.json").parent.mkdir(parents=True, exist_ok=True)
        (state / "upgrades.json").write_text(json.dumps(record), encoding="utf-8")
        maintenance = ConfigMaintenance(state, installed="0.9.0")

        assert maintenance.plan(sandbox.config_path), "Expected the edit pending"
        maintenance.apply(sandbox.config_path)
        assert maintenance.applied_version(sandbox.config_path) == "0.9.0"
        sandbox.config_path.write_text('{"version": 1}', encoding="utf-8")
        assert not maintenance.plan(sandbox.config_path), "Expected no repeat"

    @pytest.mark.parametrize("json_output", [False, True])
    def test_failed_edit_warns_and_retries(
        self, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch, *, json_output: bool
    ) -> None:
        """A failed edit is a warning (in JSON too); the next sync tries again."""
        schema = self._configure(sandbox, None)

        def fail(_config: Path) -> bool:
            """Simulate a read-only configuration file."""
            raise PermissionError("synthetic read-only file")

        monkeypatch.setattr(ConfigSchema, "add_reference", staticmethod(fail))
        argv = ("sync", "--json") if json_output else ("sync",)
        status, output = sandbox.run(*argv)

        assert status == 0, f"Expected sync to succeed, got {output}"
        if json_output:
            report = json.loads(output)
            assert any("synthetic" in item for item in report["warnings"]), report
            assert report["configuration"] == [], report
        else:
            assert "warning: could not add" in output, output
        assert not schema.exists(), "Expected no schema without the reference"
        state = sandbox.environment.state_directory
        assert ConfigMaintenance(state).plan(sandbox.config_path), "Expected retry"

    def test_json_plan_lists_configuration_changes(self, sandbox: Sandbox) -> None:
        """`plan --json` reports the pending configuration changes."""
        schema = self._configure(sandbox, None)

        report = json.loads(sandbox.run("plan", "--json")[1])

        assert report["configuration"] == [
            {"action": "add-schema-reference", "path": str(sandbox.config_path)},
            {"action": "create-schema", "path": str(schema)},
        ], report

    def test_sync_replaces_outdated_schema(self, sandbox: Sandbox) -> None:
        """An older SkillSinc schema is replaced with this version's."""
        schema = self._configure(sandbox, f"./{SCHEMA_FILE}")
        old = {"$id": ConfigSchema.identifier(), "title": "older"}
        schema.write_text(json.dumps(old), encoding="utf-8")

        assert sandbox.run("sync")[0] == 0, "Expected sync to succeed"
        assert schema.read_text(encoding="utf-8") == ConfigSchema.text()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX links and FIFOs")
    @pytest.mark.parametrize("kind", ["dangling", "link", "fifo", "directory"])
    def test_sync_refuses_special_entries(
        self, sandbox: Sandbox, tmp_path: Path, kind: str
    ) -> None:
        """Links, FIFOs, and directories at the schema path are never written."""
        schema = self._configure(sandbox, f"./{SCHEMA_FILE}")
        outside = tmp_path / "outside.json"
        match kind:
            case "dangling":
                schema.symlink_to(outside)
            case "link":
                outside.write_text(ConfigSchema.text().replace("SkillSinc", "x"))
                schema.symlink_to(outside)
            case "fifo":
                os.mkfifo(schema)
            case _:
                schema.mkdir()
        before = outside.read_bytes() if outside.exists() else None

        status, output = sandbox.run("sync")

        assert status == 0, f"Expected sync to succeed, got {output}"
        assert (outside.read_bytes() if outside.exists() else None) == before
        assert ConfigSchema.state(sandbox.config_path) is SchemaState.FOREIGN

    @pytest.mark.parametrize("content", ['{"title": "mine"}', "not json", "\udcff"])
    def test_sync_keeps_foreign_schema(self, sandbox: Sandbox, content: str) -> None:
        """A file of the same name that is not SkillSinc's is never overwritten."""
        schema = self._configure(sandbox, f"./{SCHEMA_FILE}")
        data = content.encode("utf-8", "surrogateescape")
        schema.write_bytes(data)

        assert sandbox.run("sync")[0] == 0, "Expected sync to succeed"
        assert schema.read_bytes() == data, "Expected the file untouched"


class TestLoader:
    """The loader accepts `$schema` as a string and nothing else."""

    def test_non_string_schema_is_refused(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """`$schema` must be a non-empty string, as the schema says."""
        path = tmp_path / "config.json"
        path.write_text('{"$schema": 1, "version": 1}', encoding="utf-8")
        with pytest.raises(ConfigurationError, match=r"\$schema"):
            ConfigLoader(ProfileRegistry(), environment).load(path)


class TestAddReference:
    """Inserting `$schema` changes nothing but the inserted text."""

    @pytest.mark.parametrize(
        ("before", "after"),
        [
            (
                '// top\n{\n  // why\n  "version": 1,\n}\n',
                '// top\n{\n  // why\n  "$schema": "./config.schema.json",\n'
                '  "version": 1,\n}\n',
            ),
            (
                '{\r\n\t"version": 1\r\n}\r\n',
                '{\r\n\t"$schema": "./config.schema.json",\r\n\t"version": 1\r\n}\r\n',
            ),
            (
                '\ufeff{"version": 1}',
                '\ufeff{"$schema": "./config.schema.json", "version": 1}',
            ),
            (
                '{ /* c */ "version": 1 }',
                '{ /* c */ "$schema": "./config.schema.json", "version": 1 }',
            ),
        ],
    )
    def test_insertion_keeps_everything_else(
        self, tmp_path: Path, before: str, after: str
    ) -> None:
        """Comments, line endings, indentation, and a BOM survive."""
        path = tmp_path / "config.json"
        path.write_bytes(before.encode())

        assert ConfigSchema.add_reference(path) is True
        assert path.read_bytes().decode() == after

    @pytest.mark.parametrize("text", ['{"$schema": "x", "version": 1}', "{}"])
    def test_present_or_empty_is_left_alone(self, tmp_path: Path, text: str) -> None:
        """An existing `$schema` or an empty object is not edited."""
        path = tmp_path / "config.json"
        path.write_text(text, encoding="utf-8")

        assert ConfigSchema.add_reference(path) is False
        assert path.read_text(encoding="utf-8") == text

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX links and modes")
    def test_follows_links_and_keeps_mode(self, tmp_path: Path) -> None:
        """A linked config is edited in place, and its permissions kept."""
        real = tmp_path / "real.json"
        real.write_text('{"version": 1}', encoding="utf-8")
        real.chmod(0o640)
        link = tmp_path / "config.json"
        link.symlink_to(real)

        assert ConfigSchema.add_reference(link) is True
        assert link.is_symlink(), "Expected the link kept"
        assert '"$schema"' in real.read_text(encoding="utf-8")
        assert stat.S_IMODE(real.stat().st_mode) == 0o640
        assert not list(tmp_path.glob(".*.tmp")), "Expected no temporary file"


def test_version_key_reads_release_parts() -> None:
    """Pre-release and local suffixes compare as their release."""
    assert version_key("0.8.0rc1") == (0, 8, 0)
    assert version_key("0.10.1+local.5") == (0, 10, 1)
    assert version_key("0.7.0") < version_key("0.8.0") < version_key("0.10.0")


class TestEdgeCases:
    """Unreadable inputs and failed writes degrade to warnings or no-ops."""

    def test_unreachable_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A blocked or missing path is foreign, unreferenced, and needs nothing."""
        config = tmp_path / "config.json"
        missing = tmp_path / "missing.json"
        original = Path.lstat

        def blocked(self: Path) -> os.stat_result:
            """Refuse to stat the schema path only."""
            if self.name == SCHEMA_FILE:
                raise PermissionError("synthetic denied")
            return original(self)

        monkeypatch.setattr(Path, "lstat", blocked)
        assert ConfigSchema.state(config) is SchemaState.FOREIGN
        monkeypatch.undo()
        assert ConfigSchema.reference(missing) is None
        assert ConfigSchema.needs_reference(missing) is False

    def test_failed_replace_removes_staging(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An interrupted schema write leaves no temporary file behind."""
        config = tmp_path / "config.json"

        def fail(_self: Path, _target: Path) -> Path:
            """Simulate a failed rename."""
            raise OSError("synthetic rename failure")

        monkeypatch.setattr(Path, "replace", fail)
        with pytest.raises(OSError, match="synthetic"):
            ConfigSchema.refresh(config)
        assert list(tmp_path.iterdir()) == [], "Expected no leftovers"

    def test_failed_schema_write_still_records(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A schema write failure warns; the one-time upgrade is still recorded."""
        config = tmp_path / "config.json"
        config.write_text('{"$schema": "./config.schema.json", "version": 1}')

        def fail(_config: Path) -> bool:
            """Simulate a full disk."""
            raise OSError("synthetic full disk")

        monkeypatch.setattr(ConfigSchema, "refresh", staticmethod(fail))
        maintenance = ConfigMaintenance(tmp_path / "state", installed="0.8.0")
        result = maintenance.apply(config)

        assert any("synthetic full disk" in item for item in result.warnings)
        assert maintenance.applied_version(config) == "0.8.0"

    def test_unwritable_record_warns(self, tmp_path: Path) -> None:
        """A state directory that cannot be created is a warning."""
        config = tmp_path / "config.json"
        config.write_text('{"$schema": "https://example.com/s.json", "version": 1}')
        state = tmp_path / "state"
        state.write_text("not a directory", encoding="utf-8")

        result = ConfigMaintenance(state, installed="0.8.0").apply(config)

        assert any("could not record upgrades" in item for item in result.warnings)

    @pytest.mark.parametrize("record", ['{"configs": []}', "[]", "not json"])
    def test_malformed_record_counts_as_empty(
        self, tmp_path: Path, record: str
    ) -> None:
        """A damaged upgrades.json means no upgrades are recorded."""
        state = tmp_path / "state"
        state.mkdir()
        (state / "upgrades.json").write_text(record, encoding="utf-8")

        assert ConfigMaintenance(state).applied_version(tmp_path / "c.json") is None
