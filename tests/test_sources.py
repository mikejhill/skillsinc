"""Test complete source discovery, package validation, and exclusion rules."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from tests.conftest import Builder

from skillsinc.exceptions import SourceError
from skillsinc.models import Customization, Kind
from skillsinc.sources import SourceReader


def assert_item_error(items: tuple[Customization, ...], message: str) -> None:
    """Assert that one item carries an error matching ``message``."""
    errors = [item.error for item in items if item.error is not None]
    assert any(re.search(message, error) for error in errors), (
        f"Expected an item error matching {message!r}, got {errors!r}"
    )


class TestSourceReader:
    """Source libraries must load completely or fail without partial results."""

    @pytest.mark.parametrize("description", ["[]", "{bad: value}"])
    def test_invalid_description_type_marks_the_item(
        self, tmp_path: Path, description: str
    ) -> None:
        """Reject nontext descriptions on the item, not the whole source."""
        package = tmp_path / "skills/sample"
        package.mkdir(parents=True)
        (package / "SKILL.md").write_text(
            f"---\ndescription: {description}\n---\nBody", encoding="utf-8"
        )

        assert_item_error(SourceReader().read(Builder.source(tmp_path)), "description")

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

    @pytest.mark.parametrize("relative", ["", "references"])
    def test_unreadable_asset_directory_fails_entire_source(
        self, library: Path, monkeypatch: pytest.MonkeyPatch, relative: str
    ) -> None:
        """Directory scan failures must not turn omitted assets into deletions."""
        unreadable = library / "skills/sample" / relative
        original = os.scandir

        def fail(path: str) -> Iterator[os.DirEntry[str]]:
            """Simulate a scan error without changing filesystem permissions."""
            if Path(path) == unreadable:
                raise PermissionError("synthetic directory scan failure")
            return original(path)

        monkeypatch.setattr(os, "scandir", fail)

        with pytest.raises(SourceError, match="synthetic directory scan failure"):
            SourceReader().read(Builder.source(library))

    @pytest.mark.parametrize(
        ("content", "message"),
        [
            (None, "no SKILL.md"),
            ("Body", "need a description"),
            ("---\nx: [\n---\n", "invalid YAML"),
        ],
    )
    def test_invalid_skill_marks_only_that_item(
        self, tmp_path: Path, content: str | None, message: str
    ) -> None:
        """Mark incomplete, undiscoverable, and malformed skills, keeping the rest."""
        package = tmp_path / "skills/sample"
        package.mkdir(parents=True)
        if content is not None:
            (package / "SKILL.md").write_text(content, encoding="utf-8")

        assert_item_error(SourceReader().read(Builder.source(tmp_path)), message)

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

        assert_item_error(SourceReader().read(Builder.source(library)), message)

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
            ('{"command": "a", "enabled": "no"}', "'enabled' must be true or false"),
            ('{"command": "a", "toolTimeoutSec": 0}', "'toolTimeoutSec' must be"),
            ('{"command": "a", "startupTimeoutSec": true}', "'startupTimeoutSec'"),
            ('{"command": "a", "profiles": {"codex": 1}}', "'profiles' must map"),
            ('{"command": "a", "profiles": []}', "'profiles' must map"),
        ],
    )
    def test_invalid_definition_fails_the_source(
        self, tmp_path: Path, text: str, message: str
    ) -> None:
        """Ambiguous or incomplete servers fail the whole source."""
        self.write(tmp_path, "bad.json", text)

        assert_item_error(SourceReader().read(Builder.source(tmp_path)), message)


class TestResources:
    """Resources are Markdown reference files read without parsing."""

    def test_reads_markdown_verbatim_and_ignores_other_files(
        self, tmp_path: Path
    ) -> None:
        """Each ``<name>.md`` becomes one ``resources/<name>`` item, bytes intact."""
        directory = tmp_path / "resources"
        directory.mkdir()
        raw = b"---\nnot: [valid yaml\n---\nReference.\r\n"
        (directory / "style.md").write_bytes(raw)
        (directory / "notes.txt").write_text("Ignored.\n", encoding="utf-8")
        (directory / "nested").mkdir()

        items = SourceReader().read(Builder.source(tmp_path))

        assert [(item.selector, item.document.raw) for item in items] == [
            ("resources/style", raw)
        ], f"Expected one verbatim resource, got {items!r}"

    def test_invalid_name_fails_the_source(self, tmp_path: Path) -> None:
        """A name unsafe as a path segment fails the whole source."""
        directory = tmp_path / "resources"
        directory.mkdir()
        (directory / "-bad.md").write_text("Reference.\n", encoding="utf-8")

        assert_item_error(
            SourceReader().read(Builder.source(tmp_path)), "invalid customization name"
        )


class TestPluginExpansion:
    """Plugins expand into existing kinds and name every part they cannot carry."""

    @staticmethod
    def plugin(root: Path, files: dict[str, str], *, name: str = "kit") -> Path:
        """Write a plugin directory under ``root/plugins``."""
        directory = root / "plugins" / name
        for relative, content in files.items():
            path = directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        return directory

    def test_expands_components_into_kinds(self, tmp_path: Path) -> None:
        """Skills, agents, commands, and .mcp.json servers become plugin items."""
        self.plugin(
            tmp_path,
            {
                ".claude-plugin/plugin.json": '{"name": "kit", "version": "1.0.0"}',
                ".claude-plugin/marketplace.json": "{}",
                "skills/deploy/SKILL.md": "---\ndescription: Deploy\n---\nGo.\n",
                "agents/reviewer.md": "---\ndescription: Review\n---\nReview.\n",
                "commands/status.md": "---\ndescription: Status\n---\nShow.\n",
                ".mcp.json": '{"mcpServers": {"api": {"command": "api"}}}',
                "README.md": "Docs.\n",
                "LICENSE": "MIT\n",
            },
        )

        items = SourceReader().read(Builder.source(tmp_path))

        assert [
            (item.selector, item.plugin, item.losses, item.refusal) for item in items
        ] == [
            ("agents/reviewer", "kit", (), None),
            ("mcp/api", "kit", (), None),
            ("prompts/status", "kit", (), None),
            ("skills/deploy", "kit", (), None),
        ], f"Expected four lossless plugin items, got {items!r}"
        assert items[1].document.frontmatter == {"command": "api"}, (
            f"Expected server object, got {items[1].document!r}"
        )

    def test_bare_server_map_and_custom_layout(self, tmp_path: Path) -> None:
        """Read a bare ``.mcp.json`` map from a renamed plugins directory."""
        directory = tmp_path / "bundles" / "kit"
        directory.mkdir(parents=True)
        (directory / ".mcp.json").write_text('{"api": {"url": "https://x"}}')
        source = replace(Builder.source(tmp_path), plugins="bundles")

        items = SourceReader().read(source)

        assert [item.selectors for item in items] == [
            ("mcp/api", "plugins/kit/mcp/api")
        ], f"Expected one server with plugin selector, got {items!r}"

    def test_unsynced_parts_are_losses_on_every_item(self, tmp_path: Path) -> None:
        """Hooks, LSP, scripts, nested folders, and runtime keys are named."""
        self.plugin(
            tmp_path,
            {
                ".claude-plugin/plugin.json": '{"userConfig": {}}',
                "agents/reviewer.md": "---\ndescription: Review\n---\nReview.\n",
                "agents/nested/deep.md": "Deep.\n",
                "hooks/hooks.json": "{}",
                ".lsp.json": "{}",
                ".git/HEAD": "ref\n",
                "SKILL.md": "---\ndescription: Root\n---\n",
                "settings.json": "{}",
            },
        )

        items = SourceReader().read(Builder.source(tmp_path))

        assert [item.losses for item in items] == [
            (
                "plugin 'kit' also has manifest key userConfig, .lsp.json, SKILL.md,"
                " hooks/, settings.json, agents/nested/, which is not synced",
            )
        ], f"Expected one item carrying every unsynced part, got {items!r}"

    def test_plugin_variables_refuse_the_item(self, tmp_path: Path) -> None:
        """Items that need an installed plugin's variables are refused, not broken."""
        self.plugin(
            tmp_path,
            {
                ".mcp.json": (
                    '{"api": {"command": "${CLAUDE_PLUGIN_ROOT}/api",'
                    ' "env": {"T": "${user_config.token}"}}}'
                ),
                "commands/run.md": "Run $CLAUDE_PLUGIN_DATA/x.\n",
                "agents/plain.md": "---\ndescription: Plain\n---\nCost: $5.\n",
                "skills/fmt/SKILL.md": "---\ndescription: Format\n---\nRun go.sh.\n",
                "skills/fmt/go.sh": 'exec "${CLAUDE_PLUGIN_ROOT}/bin/fmt"\n',
            },
        )

        items = SourceReader().read(Builder.source(tmp_path))

        assert [(item.selector, item.refusal) for item in items] == [
            ("agents/plain", None),
            (
                "mcp/api",
                "refers to ${CLAUDE_PLUGIN_ROOT}, ${user_config.token}, which only"
                " an installed plugin resolves",
            ),
            (
                "prompts/run",
                "refers to $CLAUDE_PLUGIN_DATA, which only an installed plugin"
                " resolves",
            ),
            (
                "skills/fmt",
                "refers to ${CLAUDE_PLUGIN_ROOT}, which only an installed plugin"
                " resolves",
            ),
        ], f"Expected refusals for plugin variables only, got {items!r}"

    def test_plugin_with_nothing_to_sync_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A hooks-only plugin yields no items and says so."""
        self.plugin(tmp_path, {"hooks/hooks.json": "{}"})
        (tmp_path / "plugins/notes.txt").write_text("ignored")

        items = SourceReader().read(Builder.source(tmp_path))

        assert items == (), f"Expected no items, got {items!r}"
        assert "nothing SkillSinc can sync" in caplog.text, (
            f"Expected a warning, got {caplog.text!r}"
        )

    @pytest.mark.parametrize(
        ("files", "message"),
        [
            ({".claude-plugin/plugin.json": "{"}, "invalid plugin manifest"),
            ({".claude-plugin/plugin.json": '{"name": "other"}'}, "differs"),
            (
                {".claude-plugin/plugin.json": '{"commands": "./cmd", "skills": "."}'},
                "move skills, commands into their default folders",
            ),
            ({".mcp.json": "["}, "invalid MCP server file"),
            ({".mcp.json": '{"mcpServers": []}'}, "must be a JSON object"),
            ({".mcp.json": '{"api": "x"}'}, "'api' must be a JSON object"),
            ({".mcp.json": '{"api": {}}'}, "either a 'command' or a 'url'"),
            ({".mcp.json": '{"bad name": {"command": "x"}}'}, "invalid customization"),
        ],
    )
    def test_ambiguous_plugin_fails_the_source(
        self, tmp_path: Path, files: dict[str, str], message: str
    ) -> None:
        """Relocated components and malformed files fail closed."""
        self.plugin(tmp_path, files)

        with pytest.raises(SourceError, match=message):
            SourceReader().read(Builder.source(tmp_path))

    def test_name_shared_with_library_marks_the_item(self, tmp_path: Path) -> None:
        """A plugin item may not shadow a library item of the same kind and name."""
        self.plugin(tmp_path, {"agents/reviewer.md": "Plugin.\n"})
        (tmp_path / "agents").mkdir()
        (tmp_path / "agents/reviewer.agent.md").write_text("Library.\n")

        assert_item_error(
            SourceReader().read(Builder.source(tmp_path)),
            "agents/reviewer is defined by plugin 'kit' and the",
        )


class TestHookFiles:
    """Hook files are Copilot CLI hook configurations, checked before any sync."""

    @staticmethod
    def write(root: Path, name: str, text: str) -> Path:
        """Write ``hooks/<name>`` under ``root``."""
        path = root / "hooks" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_reads_hook_files(self, tmp_path: Path) -> None:
        """Each ``<name>.json`` becomes one ``hooks/<name>`` item."""
        text = '{"version": 1, "hooks": {"sessionStart": [{"bash": "a.sh"}]}}'
        self.write(tmp_path, "start.json", text)

        items = SourceReader().read(Builder.source(tmp_path))

        assert [(item.selector, item.document.raw) for item in items] == [
            ("hooks/start", text.encode())
        ], f"Expected one hook file, got {items!r}"

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("{", "invalid hook file"),
            ('{"hooks": {}}', "version"),
            ('{"version": true, "hooks": {}}', "version"),
            ('{"version": 1, "hooks": []}', "version"),
            ('{"version": 1, "hooks": {"x": {}}}', "event 'x' must be an array"),
            ('{"version": 1, "hooks": {"x": [1]}}', "event 'x' must be an array"),
        ],
    )
    def test_invalid_hook_file_fails_the_source(
        self, tmp_path: Path, text: str, message: str
    ) -> None:
        """Malformed hook files fail the whole source."""
        self.write(tmp_path, "bad.json", text)

        assert_item_error(SourceReader().read(Builder.source(tmp_path)), message)


class TestItemNames:
    """Skill names follow their folder; renames give items new identities."""

    @staticmethod
    def skill(root: Path, folder: str, header: str) -> None:
        """Write ``skills/<folder>/SKILL.md`` with ``header`` as its frontmatter."""
        package = root / "skills" / folder
        package.mkdir(parents=True)
        (package / "SKILL.md").write_text(
            f"---\n{header}\n---\nBody.\n", encoding="utf-8", newline="\n"
        )

    def test_mismatched_skill_name_follows_the_folder(self, tmp_path: Path) -> None:
        """A differing name is corrected in the copy, keeping comments, with a note."""
        self.skill(tmp_path, "review", "# keep\nname: Code Review\ndescription: Hi")

        (item,) = SourceReader().read(Builder.source(tmp_path))

        assert item.error is None, f"Expected a usable skill, got {item!r}"
        assert item.document.raw == (
            b"---\n# keep\nname: review\ndescription: Hi\n---\nBody.\n"
        ), f"Expected only the name line to change, got {item.document.raw!r}"
        assert item.notes == (
            "frontmatter name 'Code Review' differs from directory name 'review',"
            " so the copy is named 'review'",
        ), f"Expected a correction note, got {item.notes!r}"

    def test_name_that_reads_as_another_type_is_quoted(self, tmp_path: Path) -> None:
        """A folder name YAML would read as a number is rendered as a string."""
        self.skill(tmp_path, "1.0", "name: other\ndescription: Hi")

        (item,) = SourceReader().read(Builder.source(tmp_path))

        assert item.document.frontmatter["name"] == "1.0", (
            f"Expected the string name '1.0', got {item.document.frontmatter!r}"
        )

    def test_one_invalid_item_keeps_the_rest(self, tmp_path: Path) -> None:
        """Every invalid item is marked, and valid ones are still read."""
        self.skill(tmp_path, "good", "description: Fine")
        self.skill(tmp_path, "bad", "name: bad")
        self.skill(tmp_path, "worse", "name: worse")

        items = SourceReader().read(Builder.source(tmp_path))

        assert {item.name: item.error is not None for item in items} == {
            "bad": True,
            "good": False,
            "worse": True,
        }, f"Expected both invalid skills marked, got {items!r}"

    def test_rename_changes_identity_paths_and_name(self, tmp_path: Path) -> None:
        """A renamed agent gets a new selector, file name, and frontmatter name."""
        (tmp_path / "agents").mkdir()
        (tmp_path / "agents/review.agent.md").write_text(
            "---\nname: review\ndescription: Hi\n---\nBody.\n", encoding="utf-8"
        )
        source = replace(
            Builder.source(tmp_path), rename={"agents/review": "team-review"}
        )

        (item,) = SourceReader().read(source)

        assert (item.selector, item.file_name, item.renamed_by) == (
            "agents/team-review",
            "team-review.agent.md",
            "agents/review",
        ), f"Expected the renamed identity, got {item!r}"
        assert item.document.frontmatter["name"] == "team-review", (
            f"Expected the frontmatter name to follow, got {item.document!r}"
        )

    def test_rename_resolves_a_plugin_collision(self, tmp_path: Path) -> None:
        """Renaming the plugin copy lets both items coexist."""
        (tmp_path / "plugins/kit/agents").mkdir(parents=True)
        (tmp_path / "plugins/kit/agents/reviewer.md").write_text("Plugin.\n")
        (tmp_path / "agents").mkdir()
        (tmp_path / "agents/reviewer.agent.md").write_text("Library.\n")
        source = replace(
            Builder.source(tmp_path),
            rename={"plugins/kit/agents/reviewer": "kit-reviewer"},
        )

        items = SourceReader().read(source)

        assert [(item.selector, item.error) for item in items] == [
            ("agents/kit-reviewer", None),
            ("agents/reviewer", None),
        ], f"Expected both agents without errors, got {items!r}"

    def test_rename_keeps_json_definitions_byte_for_byte(self, tmp_path: Path) -> None:
        """A renamed MCP server changes its key and file name, not its content."""
        (tmp_path / "mcp").mkdir()
        (tmp_path / "mcp/api.json").write_text('{"command": "x"}', encoding="utf-8")
        source = replace(Builder.source(tmp_path), rename={"mcp/api": "api-2"})

        (item,) = SourceReader().read(source)

        assert (item.name, item.file_name, item.document.raw) == (
            "api-2",
            "api-2.json",
            b'{"command": "x"}',
        ), f"Expected a renamed server with unchanged bytes, got {item!r}"

    def test_two_keys_for_one_item_mark_it(self, tmp_path: Path) -> None:
        """A plain and a plugin-qualified key for one item are ambiguous."""
        (tmp_path / "plugins/kit/agents").mkdir(parents=True)
        (tmp_path / "plugins/kit/agents/reviewer.md").write_text("Plugin.\n")
        source = replace(
            Builder.source(tmp_path),
            rename={"agents/reviewer": "a", "plugins/kit/agents/reviewer": "b"},
        )

        assert_item_error(SourceReader().read(source), "renamed by both")

    def test_rename_onto_an_existing_name_marks_the_item(self, tmp_path: Path) -> None:
        """A rename that collides with another item is reported, not merged."""
        self.skill(tmp_path, "one", "description: One")
        self.skill(tmp_path, "two", "description: Two")
        source = replace(Builder.source(tmp_path), rename={"skills/one": "two"})

        assert_item_error(
            SourceReader().read(source),
            r"skills/two is defined by the library and the library \(renamed by",
        )
