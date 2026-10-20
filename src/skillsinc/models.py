"""Value objects shared by sources, profiles, planning, and reporting."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

type FrontmatterValue = (
    str
    | int
    | float
    | bool
    | list[FrontmatterValue]
    | dict[str, FrontmatterValue]
    | None
)
type Frontmatter = Mapping[str, FrontmatterValue]
type JsonValue = FrontmatterValue


class Kind(StrEnum):
    """Customization kinds, named after their library directories."""

    SKILL = "skills"
    INSTRUCTION = "instructions"
    AGENT = "agents"
    PROMPT = "prompts"
    MCP = "mcp"
    RESOURCE = "resources"
    HOOK = "hooks"


class Support(StrEnum):
    """How faithfully a profile can represent a kind."""

    EXACT = "exact"
    CONVERTED = "converted"
    UNSUPPORTED = "unsupported"


class Digest:
    """Content hashing used for ownership and drift detection."""

    @staticmethod
    def of(content: bytes) -> str:
        """Return the SHA-256 hex digest of ``content``."""
        return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True, slots=True)
class Asset:
    """A supporting file inside a skill directory."""

    path: str
    content: bytes
    executable: bool = False


@dataclass(frozen=True, slots=True)
class Document:
    """A Markdown file split into YAML frontmatter and body.

    MCP server and hook definitions are JSON files: ``frontmatter`` holds the
    parsed object and ``body`` is empty. A resource is copied as written, so
    only ``raw`` is set.
    """

    frontmatter: Frontmatter
    body: str
    raw: bytes


@dataclass(frozen=True, slots=True)
class Customization:
    """One authored customization read from a source library."""

    kind: Kind
    name: str
    source: str
    document: Document
    file_name: str
    assets: tuple[Asset, ...] = ()

    @property
    def selector(self) -> str:
        """Return the ``kind/name`` identity used by route filters."""
        return f"{self.kind}/{self.name}"


@dataclass(frozen=True, slots=True)
class FileOutput:
    """A complete file a profile wants at a destination-relative path."""

    path: str
    content: bytes
    executable: bool = False


@dataclass(frozen=True, slots=True)
class BlockOutput:
    """A section a profile wants inside a shared Markdown file's managed block."""

    path: str
    text: str


@dataclass(frozen=True, slots=True)
class EntryOutput:
    """A keyed value a profile wants inside a container object of a shared JSON file.

    With ``merge``, the value is a list that other items may extend: the planner
    concatenates every item's list for the key instead of reporting a clash.
    """

    path: str
    container: str
    key: str
    value: JsonValue
    merge: bool = False

    @property
    def entry(self) -> str:
        """Return the ``container/key`` identity recorded in the manifest."""
        return f"{self.container}/{self.key}"


@dataclass(frozen=True, slots=True)
class Rendering:
    """A profile's translation of one customization."""

    files: tuple[FileOutput, ...] = ()
    blocks: tuple[BlockOutput, ...] = ()
    entries: tuple[EntryOutput, ...] = ()
    losses: tuple[str, ...] = ()
    unsupported: str | None = None

    @classmethod
    def refuse(cls, reason: str) -> Rendering:
        """Return a rendering stating the profile cannot represent the item."""
        return cls(unsupported=reason)


@dataclass(frozen=True, slots=True)
class LossPolicy:
    """Frontmatter fields a converter carries, ignores, or reports as lost."""

    carried: frozenset[str]
    ignored: frozenset[str] = field(default_factory=frozenset)

    def losses(self, frontmatter: Frontmatter) -> list[str]:
        """Return a loss message naming fields that are neither carried nor ignored."""
        known = self.carried | self.ignored
        dropped = [key for key in frontmatter if key not in known]
        if not dropped:
            return []
        return [f"drops frontmatter: {', '.join(dropped)}"]
