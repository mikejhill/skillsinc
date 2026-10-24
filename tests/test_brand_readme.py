"""Keep the brand visible when GitHub's themed README is published to PyPI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "docs/brand/wordmark-light.png",
            "https://raw.githubusercontent.com/mikejhill/skillsinc/main/"
            "docs/brand/wordmark-light.png",
        ),
        ("https://example.org/logo.png", "https://example.org/logo.png"),
    ],
)
def test_pypi_uses_absolute_fallback_image(
    tmp_path: Path, source: str, expected: str
) -> None:
    """GitHub's theme sources become one portable fallback image on PyPI."""
    readme = (
        '# SkillSinc\n\n<picture>\n<source srcset="dark.svg">\n'
        f'<img src="{source}" alt="SkillSinc" width="320">\n</picture>\n'
    )

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "pypi_readme.py"
    original = Path(__file__).parents[1] / "scripts" / "pypi_readme.py"
    script.write_bytes(original.read_bytes())
    (tmp_path / "README.md").write_text(readme, "utf-8")

    subprocess.run(  # noqa: S603 - trusted repository script, no shell
        [sys.executable, str(script)], check=True, capture_output=True
    )
    result = (tmp_path / "README.pypi.md").read_text("utf-8")

    assert f"![SkillSinc]({expected})" in result, (
        f"Expected an absolute fallback image, got {result}"
    )
    assert "<picture>" not in result, f"Unsupported picture markup remains: {result}"
    assert "dark.svg" not in result, f"Unsupported theme source remains: {result}"
