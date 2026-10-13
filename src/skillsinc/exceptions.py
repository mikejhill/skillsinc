"""Exception hierarchy for SkillSinc."""

from __future__ import annotations


class AppError(Exception):
    """Base class for all expected SkillSinc errors."""


class ConfigurationError(AppError):
    """The configuration file is missing, malformed, or inconsistent."""


class SourceError(AppError):
    """A source library could not be read completely."""


class FrontmatterError(AppError):
    """A document's frontmatter is not a YAML mapping of plain values."""


class ConflictError(AppError):
    """A destination change would overwrite content SkillSinc does not own."""


class LockError(AppError):
    """Another SkillSinc run holds the state lock."""
