"""Test shared skill homes and harness-neutral copy fidelity."""

from __future__ import annotations

import pytest
from tests.conftest import Builder

from skillsinc.models import Asset, Frontmatter, Kind
from skillsinc.paths import Environment
from skillsinc.profiles.base import Profile
from skillsinc.profiles.shared import AgentsProfile, GenericProfile


class TestAgentsProfile:
    """The shared agents home supports only skill packages."""

    def test_skill_preserves_assets_and_executable_flag(self) -> None:
        """Copy nested skill assets without losing their executable metadata."""
        item = Builder.item(
            assets=(Asset(path="scripts/run.sh", content=b"run", executable=True),)
        )

        result = AgentsProfile().render(item)

        assert [
            (output.path, output.content, output.executable) for output in result.files
        ] == [
            ("skills/sample/SKILL.md", item.document.raw, False),
            ("skills/sample/scripts/run.sh", b"run", True),
        ], f"Expected complete skill package, got {result!r}"

    @pytest.mark.parametrize("kind", [Kind.INSTRUCTION, Kind.AGENT, Kind.PROMPT])
    def test_non_skills_are_refused(self, kind: Kind) -> None:
        """Refuse kinds outside the shared Agent Skills format."""
        result = AgentsProfile().render(Builder.item(kind))

        assert result.unsupported == f"the agents profile has no location for {kind}", (
            f"Expected kind refusal, got {result!r}"
        )

    def test_default_path_uses_shared_home(self, environment: Environment) -> None:
        """Use the injected shared agents directory."""
        result = AgentsProfile().default_path(environment)

        assert result == environment.home / ".agents", (
            f"Expected shared home, got {result}"
        )


class TestGenericProfile:
    """Generic copies use authored file names without conversion."""

    @pytest.mark.parametrize("kind", list(Kind))
    def test_copies_each_kind_verbatim(self, kind: Kind) -> None:
        """Preserve bytes and original names for every customization kind."""
        item = Builder.item(kind)
        expected = (
            "skills/sample/SKILL.md" if kind is Kind.SKILL else f"{kind}/sample.md"
        )

        result = GenericProfile().render(item)

        assert (result.files[0].path, result.files[0].content, result.losses) == (
            expected,
            item.document.raw,
            (),
        ), f"Expected exact copy at {expected}, got {result!r}"

    def test_generic_requires_explicit_root(self, environment: Environment) -> None:
        """No generic root is inferred from the user environment."""
        result = GenericProfile().default_path(environment)

        assert result is None, f"Expected no default generic path, got {result}"


class TestProfile:
    """Common glob normalization handles global and empty scope values."""

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ({}, None),
            ({"applyTo": ""}, None),
            ({"applyTo": ["**", "**/*", "*"]}, None),
            ({"applyTo": " *.py, ,*.js "}, ["*.py", "*.js"]),
        ],
    )
    def test_normalizes_apply_to(
        self, header: Frontmatter, expected: list[str] | None
    ) -> None:
        """Return scoped globs only when the scope is more specific than global."""
        result = Profile.apply_to(header)

        assert result == expected, f"Expected globs {expected!r}, got {result!r}"

    def test_body_without_variables_has_no_losses(self) -> None:
        """Plain prompt bodies require no variable conversion warning."""
        result = Profile.variable_losses("Plain body")

        assert result == [], f"Expected no variable loss, got {result!r}"
