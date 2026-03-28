"""Tests for source registry."""

from __future__ import annotations

from ingestion.web_sources.base import BaseWebSource
from ingestion.web_sources.models import WebCandidate
from ingestion.web_sources.registry import SourceRegistry
from shared.firecrawl_client import FirecrawlClient


class _DummySource(BaseWebSource):
    name = "dummy"
    signal_type = "test"
    frequency = "daily"

    def scrape(self, client: FirecrawlClient) -> list[WebCandidate]:
        return []


class _WeeklySource(BaseWebSource):
    name = "weekly_dummy"
    signal_type = "test"
    frequency = "weekly"

    def scrape(self, client: FirecrawlClient) -> list[WebCandidate]:
        return []


class TestSourceRegistry:
    def test_register_and_get(self):
        reg = SourceRegistry()
        src = _DummySource()
        reg.register(src)
        assert reg.get("dummy") is src
        assert len(reg) == 1

    def test_get_unknown_returns_none(self):
        reg = SourceRegistry()
        assert reg.get("nonexistent") is None

    def test_get_enabled(self):
        reg = SourceRegistry()
        s1 = _DummySource()
        s2 = _WeeklySource()
        reg.register(s1)
        reg.register(s2)
        assert len(reg.get_enabled()) == 2

    def test_get_enabled_by_frequency(self):
        reg = SourceRegistry()
        reg.register(_DummySource())
        reg.register(_WeeklySource())
        daily = reg.get_enabled(frequency="daily")
        assert len(daily) == 1
        assert daily[0].name == "dummy"

    def test_disable(self):
        reg = SourceRegistry()
        reg.register(_DummySource())
        reg.disable("dummy")
        assert len(reg.get_enabled()) == 0
        assert len(reg.get_all()) == 1

    def test_enable(self):
        reg = SourceRegistry()
        src = _DummySource()
        src.enabled = False
        reg.register(src)
        assert len(reg.get_enabled()) == 0
        reg.enable("dummy")
        assert len(reg.get_enabled()) == 1

    def test_contains(self):
        reg = SourceRegistry()
        reg.register(_DummySource())
        assert "dummy" in reg
        assert "nope" not in reg

    def test_overwrite(self):
        reg = SourceRegistry()
        reg.register(_DummySource())
        new = _DummySource()
        new.signal_type = "override"
        reg.register(new)
        assert len(reg) == 1
        assert reg.get("dummy").signal_type == "override"
