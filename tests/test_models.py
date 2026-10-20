"""Test stable digest contracts used to track ownership and detect drift."""

from __future__ import annotations

import pytest

from skillsinc.models import Digest, LossPolicy


class TestDigest:
    """Ownership hashes use the documented SHA-256 content representation."""

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            (b"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
            (
                b"abc",
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            ),
        ],
        ids=["empty-content", "known-ascii-vector"],
    )
    def test_sha256_matches_known_vectors(self, content: bytes, expected: str) -> None:
        """Match known digest vectors so ownership records remain interoperable."""
        result = Digest.of(content)

        assert result == expected, f"Expected SHA-256 {expected}, got {result}"


class TestLossPolicy:
    """Conversion policies distinguish retained metadata from ignored metadata."""

    def test_reports_only_unknown_fields_in_authored_order(self) -> None:
        """Exclude both carried and intentionally ignored fields from losses."""
        policy = LossPolicy(frozenset({"carried"}), frozenset({"ignored"}))

        result = policy.losses({"carried": "yes", "ignored": "yes", "z": 1, "a": 2})

        assert result == ["drops frontmatter: z, a"], (
            f"Expected only unknown fields lost, got {result!r}"
        )
