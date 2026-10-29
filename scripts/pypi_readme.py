# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Write README.pypi.md: README.md as PyPI can render it.

PyPI renders neither Mermaid nor links relative to the repository, so the
PyPI readme swaps each Mermaid block for its pre-rendered SVG and points
relative links and images at GitHub.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPOSITORY = "https://github.com/mikejhill/skillsinc"
RAW = "https://raw.githubusercontent.com/mikejhill/skillsinc/main"
NOTICE = "<!-- Generated from README.md by scripts/pypi_readme.py; do not edit. -->"


class PypiReadme:
    """Convert the GitHub README into the readme published to PyPI."""

    # A fenced Mermaid block, and a Markdown link or image target
    MERMAID = re.compile(r"^```mermaid\n(?P<body>.*?)^```\n", re.MULTILINE | re.DOTALL)
    TARGET = re.compile(r"(?P<prefix>\]\()(?P<target>[^)\s]+)\)")
    PICTURE = re.compile(
        r'<picture>.*?<img src="(?P<src>[^"]+)" alt="(?P<alt>[^"]+)"[^>]*>'
        r".*?</picture>",
        re.DOTALL,
    )
    ABSOLUTE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|#|/)", re.IGNORECASE)

    def __init__(self, root: Path) -> None:
        self._root = root

    @classmethod
    def main(cls, argv: Sequence[str] | None = None) -> None:
        """Write README.pypi.md, or with --check fail if it is stale."""
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument(
            "--check", action="store_true", help="fail if README.pypi.md is stale"
        )
        args = parser.parse_args(argv)
        sys.exit(cls(ROOT).run(check=bool(args.check)))

    def run(self, *, check: bool) -> int:
        """Write or check the PyPI readme; return the exit code."""
        try:
            expected = self.convert((self._root / "README.md").read_text("utf-8"))
        except ValueError as error:
            sys.stderr.write(f"{error}\n")
            return 1
        target = self._root / "README.pypi.md"
        if not check:
            target.write_text(expected, "utf-8", newline="\n")
            return 0
        if target.is_file() and target.read_text("utf-8") == expected:
            return 0
        sys.stderr.write(
            "README.pypi.md is stale; run: uv run scripts/pypi_readme.py\n"
        )
        return 1

    def convert(self, readme: str) -> str:
        """Return the PyPI readme for the text of README.md."""
        text = self.PICTURE.sub(self._picture, readme)
        text = self.MERMAID.sub(self._diagram, text)
        text = self.TARGET.sub(self._absolute, text)
        title, _, rest = text.partition("\n")
        return f"{title}\n\n{NOTICE}\n{rest}"

    def _diagram(self, match: re.Match[str]) -> str:
        """Return the image for a Mermaid block, checking its rendered source."""
        body = match.group("body")
        for source in sorted((self._root / "docs" / "assets").glob("*.mmd")):
            if source.read_text("utf-8") == body:
                image = source.with_suffix(".svg").relative_to(self._root)
                alt = source.stem.replace("-", " ").capitalize()
                return f"![{alt}]({RAW}/{image.as_posix()})\n"
        raise ValueError(
            "A Mermaid block in README.md has no matching docs/assets/*.mmd; "
            "save it there and render the SVG (see docs/guides/development.md)"
        )

    def _picture(self, match: re.Match[str]) -> str:
        """Use the fallback image on PyPI, which does not support picture sources."""
        source = match.group("src")
        if not self.ABSOLUTE.match(source):
            source = f"{RAW}/{source}"
        return f"![{match.group('alt')}]({source})"

    def _absolute(self, match: re.Match[str]) -> str:
        """Return a link or image target made absolute against GitHub."""
        target = match.group("target")
        if not self.ABSOLUTE.match(target):
            target = f"{REPOSITORY}/blob/main/{target}"
        return f"{match.group('prefix')}{target})"


if __name__ == "__main__":
    PypiReadme.main()
