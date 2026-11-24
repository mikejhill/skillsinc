"""Parse and render Markdown files with YAML frontmatter."""

from __future__ import annotations

import datetime
import re
from typing import Final

import yaml

from skillsinc.exceptions import FrontmatterError
from skillsinc.models import Document, Frontmatter, FrontmatterValue

# YAML reads these as line breaks, so they must be written escaped
YAML_BREAKS: Final = re.compile("[\x85\u2028\u2029]")
NAME_LINE: Final = re.compile(r"^name[ \t]*:[^\r\n]*", re.MULTILINE)
FENCE: Final = re.compile(
    r"\A---[ \t]*\r?\n(?P<yaml>.*?)^---[ \t]*(?:\r?\n|\Z)", re.DOTALL | re.MULTILINE
)


class FrontmatterCodec:
    """Convert between raw Markdown bytes and ``Document`` values."""

    def parse(self, raw: bytes) -> Document:
        """Split ``raw`` into frontmatter and body.

        Raises:
            FrontmatterError: If the bytes are not UTF-8 or the YAML is invalid.
        """
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as err:
            msg = f"not valid UTF-8 ({err.reason})"
            raise FrontmatterError(msg) from err

        match = FENCE.match(text)
        if match is None:
            return Document(frontmatter={}, body=text, raw=raw)
        return Document(
            frontmatter=self._load(match.group("yaml")),
            body=text[match.end() :],
            raw=raw,
        )

    @staticmethod
    def render(frontmatter: Frontmatter, body: str) -> bytes:
        """Return Markdown bytes with ``frontmatter`` (omitted when empty)."""
        if not frontmatter:
            return body.encode("utf-8")
        header = yaml.safe_dump(
            dict(frontmatter), sort_keys=False, allow_unicode=True, width=1000
        )
        if YAML_BREAKS.search(header):
            header = yaml.safe_dump(dict(frontmatter), sort_keys=False, width=1000)
        return f"---\n{header}---\n{body}".encode()

    def rename(self, document: Document, name: str) -> Document:
        """Return ``document`` with its frontmatter ``name`` set to ``name``.

        Only the ``name`` line changes when that is enough, so comments and
        formatting survive; otherwise the frontmatter is rendered again. A
        document without a ``name`` field is returned unchanged.
        """
        if "name" not in document.frontmatter or document.frontmatter["name"] == name:
            return document
        wanted = {**document.frontmatter, "name": name}
        text = document.raw.decode("utf-8-sig")
        match = FENCE.match(text)
        header = match.group("yaml") if match is not None else ""
        line = yaml.safe_dump({"name": name}, allow_unicode=True, width=1000).strip()
        edited, count = NAME_LINE.subn(lambda _: line, header, count=2)
        if match is not None and count == 1:
            try:
                reparsed = self._load(edited)
            except FrontmatterError:
                reparsed = {}
            if reparsed == wanted:
                start, end = match.span("yaml")
                renamed = text[:start] + edited + text[end:]
                return self.parse(renamed.encode("utf-8"))
        return self.parse(self.render(wanted, document.body))

    # Internals

    def _load(self, source: str) -> Frontmatter:
        """Parse the YAML header into a typed mapping."""
        try:
            loaded: object = yaml.safe_load(source)
        except (yaml.YAMLError, ValueError) as err:
            # PyYAML raises ValueError for impossible timestamps such as 2026-02-31
            msg = f"invalid YAML frontmatter: {err}"
            raise FrontmatterError(msg) from err
        result: dict[str, FrontmatterValue] = {}
        if loaded is None:
            return result
        if not isinstance(loaded, dict):
            msg = "frontmatter must be a YAML mapping"
            raise FrontmatterError(msg)
        for key, value in loaded.items():
            result[str(key)] = self._coerce(value)
        return result

    def _coerce(self, value: object) -> FrontmatterValue:
        """Return ``value`` as a frontmatter value, converting YAML dates to text."""
        if value is None or isinstance(value, str | int | float | bool):
            return value
        if isinstance(value, datetime.date):
            return value.isoformat()
        if isinstance(value, list):
            return [self._coerce(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._coerce(item) for key, item in value.items()}
        msg = f"unsupported frontmatter value of type {type(value).__name__}"
        raise FrontmatterError(msg)
