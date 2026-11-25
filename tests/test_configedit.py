"""Editing config.json element by element without losing comments."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from skillsinc.config import Config, ConfigLoader, ConfigurationError
from skillsinc.configedit import ConfigDocument, ConfigEditError, Validator, describe
from skillsinc.jsonc import Jsonc
from skillsinc.paths import Environment
from skillsinc.profiles.registry import ProfileRegistry

TEXT = """\
{
  // shared library
  "version": 1,
  "sources": {
    // my library
    "library": { "path": "library" },
    "team": { "github": "acme/skills" },
  },
  "destinations": {
    "claude": {},
    "codex": { "enabled": false },
  },
  "profiles": { "work": { "description": "Work" } },
  "routes": [
    {
      "sources": ["library", "team"],
      "destinations": ["claude"],
      "profiles": ["work"],
    },
  ],
}
"""


def _plain(text: str) -> Any:  # noqa: ANN401 - tests index freely
    return json.loads(Jsonc.blank_trailing_commas(Jsonc.blank_comments(text)))


@pytest.fixture
def config(tmp_path: Path) -> Path:
    """Write the sample configuration and its library folder."""
    path = tmp_path / "config.json"
    path.write_text(TEXT, encoding="utf-8")
    (tmp_path / "library").mkdir()
    return path


def _loader(environment: Environment) -> Validator:
    return ConfigLoader(ProfileRegistry(), environment).load


def _accept(path: Path, text: str) -> Config:
    """Accept any text: validation is not under test."""
    del text
    return Config(path, {}, {}, ())


class TestEditing:
    """Each kind of edit changes only its own text."""

    def test_unchanged_document_renders_the_original(self, config: Path) -> None:
        """Opening and rendering without edits changes nothing."""
        document = ConfigDocument.open(config)

        assert document.render() == TEXT
        assert not document.changed
        assert document.path == config

    def test_changed_source_keeps_other_comments(self, config: Path) -> None:
        """Changing one source rewrites only that value."""
        document = ConfigDocument.open(config)

        document.put("sources", "team", {"github": "acme/other", "ref": "main"})
        text = document.render()

        assert "// my library" in text
        assert "// shared library" in text
        assert _plain(text)["sources"] == {
            "library": {"path": "library"},
            "team": {"github": "acme/other", "ref": "main"},
        }

    def test_added_and_removed_entries(self, config: Path) -> None:
        """New entries are inserted, removed ones deleted with their line."""
        document = ConfigDocument.open(config)

        document.put("destinations", "agents", {"enabled": True})
        document.remove("destinations", "codex")
        document.remove("destinations", "missing")
        text = document.render()

        assert _plain(text)["destinations"] == {
            "claude": {},
            "agents": {"enabled": True},
        }
        assert "codex" not in text

    def test_rename_updates_routes(self, config: Path) -> None:
        """Renaming a source renames it in every route."""
        document = ConfigDocument.open(config)

        document.put("sources", "mine", {"path": "library"}, old_key="library")
        text = document.render()

        data = _plain(text)
        assert set(data["sources"]) == {"mine", "team"}
        assert data["routes"][0]["sources"] == ["mine", "team"]

    def test_routes_are_replaced_when_changed(self, config: Path) -> None:
        """Route edits replace the routes array."""
        document = ConfigDocument.open(config)
        routes = document.routes()
        routes.append({"sources": ["team"], "destinations": ["codex"]})

        document.set_routes(routes)

        assert len(_plain(document.render())["routes"]) == 2
        assert "// my library" in document.render()

    def test_missing_sections_are_created(self, tmp_path: Path) -> None:
        """A configuration without a section gets one."""
        path = tmp_path / "config.json"
        path.write_text('{"version": 1, "routes": 3}', encoding="utf-8")
        document = ConfigDocument.open(path)

        assert document.section("sources") == {}
        assert document.routes() == []
        document.put("sources", "library", {"path": "x"})

        assert _plain(document.render())["sources"] == {"library": {"path": "x"}}

    def test_a_section_that_is_not_an_object_is_replaced(self, tmp_path: Path) -> None:
        """A malformed section is replaced whole by the edit."""
        path = tmp_path / "config.json"
        path.write_text('{"sources": [], "profiles": {"a": 1}}', encoding="utf-8")
        document = ConfigDocument.open(path)

        assert document.section("profiles") == {"a": {}}
        document.put("sources", "library", {"path": "x"})

        assert _plain(document.render())["sources"] == {"library": {"path": "x"}}

    def test_order_alone_changes_nothing(self, config: Path) -> None:
        """Key order carries no meaning, so re-adding an entry leaves the text."""
        document = ConfigDocument.open(config)
        document.remove("sources", "library")
        document.put("sources", "library", {"path": "library"})

        assert document.render() == TEXT

    @pytest.mark.parametrize("routes", [3, ["odd", {}, {"sources": "a"}]])
    def test_rename_without_route_lists(self, tmp_path: Path, routes: object) -> None:
        """Routes that are not objects with a list of names are left as they are."""
        path = tmp_path / "config.json"
        data = {"sources": {"a": {}}, "routes": routes}
        path.write_text(json.dumps(data), encoding="utf-8")
        document = ConfigDocument.open(path)

        document.put("sources", "b", {}, old_key="a")

        assert _plain(document.render()) == {"sources": {"b": {}}, "routes": routes}

    def test_describe_is_compact_json(self) -> None:
        """Cells show values as one-line JSON."""
        assert describe({"a": [1, "é"]}) == '{"a": [1, "é"]}'


class TestReadingAndSaving:
    """Bad files are reported; saves are validated and refuse lost updates."""

    def test_invalid_jsonc_is_an_edit_error(self, tmp_path: Path) -> None:
        """Text that is not a JSONC object cannot be edited."""
        path = tmp_path / "config.json"
        path.write_text("[1]", encoding="utf-8")

        with pytest.raises(ConfigEditError, match="invalid JSONC"):
            ConfigDocument.open(path)

    def test_unreadable_file_is_an_edit_error(self, tmp_path: Path) -> None:
        """A missing file names itself."""
        with pytest.raises(ConfigEditError, match="cannot read"):
            ConfigDocument.open(tmp_path / "missing.json")

    def test_text_that_cannot_be_spliced_is_an_edit_error(self, tmp_path: Path) -> None:
        """Valid JSON that the editor cannot splice into is reported."""
        path = tmp_path / "config.json"
        path.write_text('{"sources": {}}', encoding="utf-8")
        document = ConfigDocument(path, '{"sources": {}}')
        document._original = "not an object"
        document.put("sources", "x", {})

        with pytest.raises(ConfigEditError, match="cannot edit in place"):
            document.render()

    def test_save_validates_writes_and_keeps_the_bom(
        self, config: Path, environment: Environment
    ) -> None:
        """A valid edit is written; the byte order mark stays."""
        config.write_bytes(b"\xef\xbb\xbf" + TEXT.encode())
        document = ConfigDocument.open(config)
        document.put("destinations", "codex", {"enabled": True})

        assert document.save(_loader(environment))

        raw = config.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")
        assert _plain(raw.decode("utf-8-sig"))["destinations"]["codex"] == {
            "enabled": True
        }
        assert not document.changed
        assert not document.save(_accept)

    def test_invalid_edit_is_not_written(
        self, config: Path, environment: Environment
    ) -> None:
        """The loader's error is raised and the file stays as it was."""
        document = ConfigDocument.open(config)
        document.put("destinations", "nowhere", {"profile": "generic"})

        with pytest.raises(ConfigurationError, match="nowhere"):
            document.save(_loader(environment))
        with pytest.raises(ConfigurationError):
            document.validate(_loader(environment))
        assert config.read_text(encoding="utf-8") == TEXT

    def test_file_removed_since_reading_is_reported(self, config: Path) -> None:
        """A configuration that disappeared cannot be checked or saved."""
        document = ConfigDocument.open(config)
        config.unlink()

        with pytest.raises(ConfigEditError, match="cannot read"):
            document.plan(lambda _path, _text: None)  # ty: ignore[invalid-argument-type]

    def test_file_edited_since_reading_is_not_overwritten(self, config: Path) -> None:
        """Someone else's edit wins; the window is told to reopen."""
        document = ConfigDocument.open(config)
        document.put("destinations", "codex", {"enabled": True})
        config.write_text(TEXT.replace("Work", "Office"), encoding="utf-8")

        with pytest.raises(ConfigEditError, match="changed since it was read"):
            document.save(_accept)
        assert "Office" in config.read_text(encoding="utf-8")


class TestDeletePath:
    """Jsonc.delete_path removes one nested member."""

    def test_removes_a_nested_member(self) -> None:
        """Only the member goes."""
        text = '{"a": {"b": 1, "c": 2}}'

        assert json.loads(Jsonc.delete_path(text, ["a", "b"])) == {"a": {"c": 2}}

    @pytest.mark.parametrize("path", [["x", "b"], ["a", "x"], ["n", "b"]])
    def test_missing_members_leave_the_text(self, path: list[str]) -> None:
        """A path that is not there changes nothing."""
        text = '{"a": {"b": 1}, "n": 1}'

        assert Jsonc.delete_path(text, path) == text

    def test_empty_path_is_an_error(self) -> None:
        """There is always a member to name."""
        with pytest.raises(ValueError, match="non-empty"):
            Jsonc.delete_path("{}", [])


def test_loader_reads_text_in_place_of_the_file(
    config: Path, environment: Environment
) -> None:
    """Validation reads candidate text as if it were the file."""
    loaded = ConfigLoader(ProfileRegistry(), environment).load(
        config, TEXT.replace('"enabled": false', '"enabled": true')
    )

    assert loaded.destinations["codex"].enabled
