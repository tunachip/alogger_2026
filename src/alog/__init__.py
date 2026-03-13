"""Compatibility package that re-exports the current log package."""

from log import IngesterConfig, IngesterService

__all__ = ["IngesterConfig", "IngesterService"]
