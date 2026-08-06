"""Tests for the citation index builder (PROD-05) and packaged index."""

from __future__ import annotations

import json

from leggie.infrastructure.resources import ResourceLocator
from tools.build_citation_index import build, identifier_count


class TestCitationIndexBuilder:
    def test_build_returns_valid_index(self):
        index = build()
        assert index["identifier_count"] == identifier_count() > 0
        assert "version" in index
        assert "build_date" in index
        assert len(index["identifiers"]) == index["identifier_count"]

    def test_build_has_expected_categories(self):
        index = build()
        assert index["categories"]["constitution"] >= 100  # Σύνταγμα 1-120
        assert index["categories"]["celex"] > 0
        assert index["categories"]["fek"] > 0


class TestPackagedCitationIndex:
    def test_packaged_index_resolves_and_is_valid(self):
        loc = ResourceLocator()
        path = loc.package_resource("leggie.data", "citation_index.json")
        assert path.exists(), f"Packaged index missing at {path}"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["identifier_count"] > 0
        assert "identifiers" in data
