"""The bundled JSON Schema matches the loader, and sync keeps a copy beside the file."""

from __future__ import annotations

import json
import re
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
from skillsinc.schema import SCHEMA_FILE, ConfigSchema
from skillsinc.starter import StarterConfig

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


class TestStarter:
    """`init` names the schema and writes it beside the file."""

    @pytest.mark.parametrize("project", [False, True])
    @pytest.mark.parametrize("harness", [None, "claude"])
    def test_starter_validates_and_writes_schema(
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
        written = (path.parent / SCHEMA_FILE).read_text(encoding="utf-8")
        assert written == ConfigSchema.text(), "Expected the bundled schema"


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

    @pytest.mark.parametrize("reference", [None, "https://example.com/other.json"])
    def test_sync_leaves_other_references_alone(
        self, sandbox: Sandbox, reference: str | None
    ) -> None:
        """Without a local reference, no schema file appears."""
        schema = self._configure(sandbox, reference)

        assert sandbox.run("sync")[0] == 0, "Expected sync to succeed"
        assert not schema.exists(), "Expected no schema file"

    def test_sync_replaces_outdated_schema(self, sandbox: Sandbox) -> None:
        """An older SkillSinc schema is replaced with this version's."""
        schema = self._configure(sandbox, f"./{SCHEMA_FILE}")
        old = {"$id": ConfigSchema.identifier(), "title": "older"}
        schema.write_text(json.dumps(old), encoding="utf-8")

        assert sandbox.run("sync")[0] == 0, "Expected sync to succeed"
        assert schema.read_text(encoding="utf-8") == ConfigSchema.text()

    @pytest.mark.parametrize("content", ['{"title": "mine"}', "not json", "\udcff"])
    def test_sync_keeps_foreign_schema(self, sandbox: Sandbox, content: str) -> None:
        """A file of the same name that is not SkillSinc's is never overwritten."""
        schema = self._configure(sandbox, f"./{SCHEMA_FILE}")
        data = content.encode("utf-8", "surrogateescape")
        schema.write_bytes(data)

        assert sandbox.run("sync")[0] == 0, "Expected sync to succeed"
        assert schema.read_bytes() == data, "Expected the file untouched"

    def test_unwritable_schema_warns(
        self, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed schema write is a warning; the sync result stands."""
        self._configure(sandbox, f"./{SCHEMA_FILE}")

        def fail(_config: Path) -> Path:
            """Simulate a read-only configuration folder."""
            raise PermissionError("synthetic read-only folder")

        monkeypatch.setattr(ConfigSchema, "refresh", staticmethod(fail))
        status, output = sandbox.run("sync")

        assert status == 0, f"Expected sync to succeed, got {output}"
        assert "warning: could not update the configuration schema" in output


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
