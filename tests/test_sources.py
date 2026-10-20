"""Test complete source discovery, package validation, and exclusion rules."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from tests.conftest import Builder

from skillsinc.exceptions import SourceError
from skillsinc.models import Kind
from skillsinc.sources import SourceReader


class TestSourceReader:
    """Source libraries must load completely or fail without partial results."""

    @pytest.mark.parametrize("description", ["[]", "{bad: value}"])
    def test_invalid_description_type_raises_source_error(
        self, tmp_path: Path, description: str
    ) -> None:
        """Reject nontext descriptions through the documented source-error boundary."""
        package = tmp_path / "skills/sample"
        package.mkdir(parents=True)
        (package / "SKILL.md").write_text(
            f"---\ndescription: {description}\n---\nBody", encoding="utf-8"
        )

        with pytest.raises(SourceError, match="description"):
            SourceReader().read(Builder.source(tmp_path))

    def test_reads_all_kinds_sorted_with_assets(self, library: Path) -> None:
        """Discover suffix and plain Markdown documents plus nested skill assets."""
        items = SourceReader().read(Builder.source(library))

        assert [item.selector for item in items] == [
            "agents/reviewer",
            "instructions/global",
            "instructions/scoped",
            "prompts/ask",
            "skills/sample",
        ], f"Expected five sorted selectors, got {items!r}"
        assert [(asset.path, asset.content) for asset in items[-1].assets] == [
            ("references/guide.md", b"Reference.\n"),
            ("scripts/run.sh", b"#!/bin/sh\nexit 0\n"),
        ], f"Expected reference and script assets, got {items[-1].assets!r}"

    def test_empty_library_is_valid(self, tmp_path: Path) -> None:
        """Missing kind directories represent an empty library."""
        result = SourceReader().read(Builder.source(tmp_path))

        assert result == (), f"Expected no items, got {result!r}"

    def test_missing_root_fails(self, tmp_path: Path) -> None:
        """Reject missing sources so they cannot be mistaken for deletions."""
        with pytest.raises(SourceError, match="directory not found"):
            SourceReader().read(Builder.source(tmp_path / "absent"))

    @pytest.mark.parametrize(
        ("content", "message"),
        [
            (None, "no SKILL.md"),
            ("---\nname: other\ndescription: okay\n---\n", "differs"),
            ("Body", "need a description"),
            ("---\nx: [\n---\n", "invalid YAML"),
        ],
    )
    def test_invalid_skill_fails_entire_source(
        self, tmp_path: Path, content: str | None, message: str
    ) -> None:
        """Reject incomplete, mismatched, undiscoverable, and malformed skills."""
        package = tmp_path / "skills/sample"
        package.mkdir(parents=True)
        if content is not None:
            (package / "SKILL.md").write_text(content, encoding="utf-8")

        with pytest.raises(SourceError, match=message):
            SourceReader().read(Builder.source(tmp_path))

    @pytest.mark.parametrize(
        ("name", "message"),
        [
            ("bad name.md", "invalid customization name"),
            ("sample.instructions.md", "two instructions"),
        ],
    )
    def test_invalid_or_duplicate_document_names_fail(
        self, library: Path, name: str, message: str
    ) -> None:
        """Reject unsafe names and ambiguous suffix aliases."""
        directory = library / "instructions"
        (directory / "sample.md").write_text("Sample", encoding="utf-8")
        (directory / name).write_text("Other", encoding="utf-8")

        with pytest.raises(SourceError, match=message):
            SourceReader().read(Builder.source(library))

    def test_excludes_packages_documents_and_nested_assets(self, library: Path) -> None:
        """Apply exclusions to top-level entries and nested package directories."""
        (library / "skills/sample/.hidden").mkdir()
        (library / "skills/sample/.hidden/secret.md").write_text(
            "Secret", encoding="utf-8"
        )
        (library / "skills/sample/INTENT.md").write_text("Intent", encoding="utf-8")
        (library / "instructions/ignored.txt").write_text("Ignore", encoding="utf-8")
        (library / "instructions/folder").mkdir()
        (library / "skills/ignored.txt").write_text("Ignore", encoding="utf-8")
        source = replace(
            Builder.source(library),
            exclude=(".*", "INTENT.md", "global.md", "reviewer*"),
        )

        items = SourceReader().read(source)

        assert [item.selector for item in items] == [
            "instructions/scoped",
            "prompts/ask",
            "skills/sample",
        ], f"Expected excluded documents absent, got {items!r}"
        assert len(items[-1].assets) == 2, (
            f"Expected only two visible assets, got {items[-1].assets!r}"
        )

    def test_custom_layout_reads_renamed_kind_directory(self, library: Path) -> None:
        """Honor source layout mappings rather than hardcoding directory names."""
        (library / "prompts").rename(library / "commands")
        source = Builder.source(library)
        layout = dict(source.layout)
        layout[Kind.PROMPT] = "commands"

        items = SourceReader().read(replace(source, layout=layout))

        assert "prompts/ask" in [item.selector for item in items], (
            f"Expected renamed prompt discovered, got {items!r}"
        )


class TestMcpServers:
    """MCP server definitions are strict JSON objects with one launch method."""

    @staticmethod
    def write(root: Path, name: str, text: str) -> Path:
        """Write ``mcp/<name>`` under ``root``."""
        path = root / "mcp" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_reads_server_definitions_and_ignores_other_files(
        self, tmp_path: Path
    ) -> None:
        """Each ``<name>.json`` becomes one ``mcp/<name>`` item."""
        self.write(tmp_path, "github.json", '{"type": "http", "url": "https://x"}')
        self.write(tmp_path, "README.md", "Notes.\n")

        items = SourceReader().read(Builder.source(tmp_path))

        assert [(item.selector, item.document.frontmatter) for item in items] == [
            ("mcp/github", {"type": "http", "url": "https://x"})
        ], f"Expected one server, got {items!r}"

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("{", "invalid MCP server definition"),
            ('{"command": "a", "command": "b"}', "duplicate key"),
            ("[]", "JSON object"),
            ('{"args": []}', "either a 'command' or a 'url'"),
            ('{"command": "a", "url": "https://x"}', "either a 'command' or a 'url'"),
        ],
    )
    def test_invalid_definition_fails_the_source(
        self, tmp_path: Path, text: str, message: str
    ) -> None:
        """Ambiguous or incomplete servers fail the whole source."""
        self.write(tmp_path, "bad.json", text)

        with pytest.raises(SourceError, match=message):
            SourceReader().read(Builder.source(tmp_path))
