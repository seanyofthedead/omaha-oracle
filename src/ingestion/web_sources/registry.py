"""Source registry — central catalogue of all web scraping sources."""

from __future__ import annotations

from shared.logger import get_logger

from .base import BaseWebSource

_log = get_logger(__name__)


class SourceRegistry:
    """Manages registration and lookup of web scraping sources.

    Adding a new source requires only:
        1. Define a ``SchemaWebSource`` subclass with class attributes.
        2. Call ``registry.register(MySource())``.
    """

    def __init__(self) -> None:
        self._sources: dict[str, BaseWebSource] = {}

    def register(self, source: BaseWebSource) -> None:
        """Register a source.  Overwrites if the name already exists."""
        self._sources[source.name] = source
        _log.debug("Registered web source", extra={"name": source.name})

    def get(self, name: str) -> BaseWebSource | None:
        """Look up a source by name."""
        return self._sources.get(name)

    def get_enabled(self, frequency: str | None = None) -> list[BaseWebSource]:
        """Return enabled sources, optionally filtered by frequency."""
        sources = [s for s in self._sources.values() if s.enabled]
        if frequency:
            sources = [s for s in sources if s.frequency == frequency]
        return sources

    def get_all(self) -> list[BaseWebSource]:
        """Return all registered sources regardless of state."""
        return list(self._sources.values())

    def enable(self, name: str) -> None:
        """Enable a source by name."""
        if src := self._sources.get(name):
            src.enabled = True

    def disable(self, name: str) -> None:
        """Disable a source by name."""
        if src := self._sources.get(name):
            src.enabled = False

    def __len__(self) -> int:
        return len(self._sources)

    def __contains__(self, name: str) -> bool:
        return name in self._sources
