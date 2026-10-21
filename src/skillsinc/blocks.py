"""Splice a SkillSinc-managed block into a Markdown file without touching the rest."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from skillsinc.exceptions import ConflictError

BEGIN: Final = "<!-- skillsinc:begin -->"
END: Final = "<!-- skillsinc:end -->"
NOTICE: Final = (
    "<!-- Managed by SkillSinc. Edit the source library instead;"
    " changes here are overwritten on the next sync. -->"
)


class ManagedBlock:
    """Build, find, replace, and remove the single managed block in a file."""

    @staticmethod
    def build(sections: Sequence[tuple[str, str]]) -> str:
        """Return the block text for ``(label, text)`` sections, in order.

        Section text is normalized to LF line endings, as ``extract``
        returns it, so a CRLF source does not read as a local edit.
        """
        parts = [BEGIN, NOTICE]
        for label, text in sections:
            body = text.replace("\r\n", "\n").strip()
            parts.extend(["", f"<!-- skillsinc: {label} -->", "", body])
        parts.extend(["", END])
        return "\n".join(parts)

    def extract(self, content: str) -> str | None:
        """Return the existing block (markers included), or None if absent."""
        span = self._span(content)
        if span is None:
            return None
        start, end = span
        return content[start:end].replace("\r\n", "\n")

    def splice(self, content: str | None, block: str | None) -> str:
        """Return ``content`` with its block replaced by ``block`` (None removes it).

        Raises:
            ConflictError: If the file's markers are unbalanced or duplicated.
        """
        existing = content or ""
        newline = "\r\n" if "\r\n" in existing else "\n"
        rendered = block.replace("\n", newline) if block is not None else None
        span = self._span(existing)

        # No existing block
        if span is None:
            if rendered is None:
                return existing
            if not existing.strip():
                return rendered + newline
            separator = newline if existing.endswith(newline) else newline * 2
            if existing.endswith(newline * 2):
                separator = ""
            return existing + separator + rendered + newline

        # Replace or remove in place
        start, end = span
        if rendered is not None:
            return existing[:start] + rendered + existing[end:]
        before = existing[:start].rstrip("\r\n")
        after = existing[end:].lstrip("\r\n")
        if before and after:
            return before + newline * 2 + after
        return (before or after) + (newline if (before or after) else "")

    # Internals

    @staticmethod
    def _span(content: str) -> tuple[int, int] | None:
        """Return the block's ``(start, end)`` offsets, validating the markers."""
        begins = content.count(BEGIN)
        ends = content.count(END)
        if begins == 0 and ends == 0:
            return None
        start = content.find(BEGIN)
        end = content.find(END)
        if begins != 1 or ends != 1 or end < start:
            msg = (
                "the file's SkillSinc markers are damaged (expected exactly one"
                f" '{BEGIN}' followed by one '{END}'); repair them by hand"
            )
            raise ConflictError(msg)
        return start, end + len(END)
