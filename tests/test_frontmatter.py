"""Test Markdown parsing, typed YAML coercion, and rendering boundaries."""

from __future__ import annotations

import pytest

from skillsinc.exceptions import FrontmatterError
from skillsinc.frontmatter import FrontmatterCodec


class TestFrontmatterCodec:
    """Document codec behavior across plain Markdown and YAML headers."""

    @pytest.mark.parametrize(
        ("raw", "header", "body"),
        [
            (b"plain\n", {}, "plain\n"),
            (b"\xef\xbb\xbfplain", {}, "plain"),
            (b"---\r\nname: hello\r\n---\r\nBody", {"name": "hello"}, "Body"),
            (b"---\n---\n", {}, ""),
            (b"---\nname: x", {}, "---\nname: x"),
        ],
    )
    def test_parse_preserves_body_and_raw(
        self, raw: bytes, header: dict[str, str], body: str
    ) -> None:
        """Recognize complete headers while retaining the authored bytes."""
        result = FrontmatterCodec().parse(raw)

        assert (dict(result.frontmatter), result.body, result.raw) == (
            header,
            body,
            raw,
        ), f"Expected {(header, body, raw)!r}, got {result!r}"

    def test_nested_values_and_dates_are_coerced(self) -> None:
        """Convert YAML dates and nested mapping keys into supported values."""
        raw = (
            b"---\ndate: 2026-01-01\nitems: [null, true, 3, 1"
            b".5]\nmap: {1: value}\n---\n"
        )

        result = FrontmatterCodec().parse(raw)

        expected = {
            "date": "2026-01-01",
            "items": [None, True, 3, 1.5],
            "map": {"1": "value"},
        }
        assert result.frontmatter == expected, (
            f"Expected {expected!r}, got {result.frontmatter!r}"
        )

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            (b"\xff", "UTF-8"),
            (b"---\nx: [\n---\n", "invalid YAML"),
            (b"---\n- item\n---\n", "mapping"),
            (b"---\nx: !!set {a: null}\n---\n", "unsupported frontmatter"),
        ],
    )
    def test_invalid_document_raises_contextual_error(
        self, raw: bytes, message: str
    ) -> None:
        """Reject invalid encodings, YAML syntax, shapes, and value types."""
        with pytest.raises(FrontmatterError, match=message):
            FrontmatterCodec().parse(raw)

    @pytest.mark.parametrize("header", [{}, {"description": "日本語", "enabled": True}])
    def test_render_round_trips(self, header: dict[str, str | bool]) -> None:
        """Preserve Unicode frontmatter and body through rendering."""
        codec = FrontmatterCodec()

        result = codec.parse(codec.render(header, "Body\n"))

        assert (result.frontmatter, result.body) == (header, "Body\n"), (
            f"Expected {(header, 'Body')!r}, got {result!r}"
        )
