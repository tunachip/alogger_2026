"""Alogger ingester service package."""

from .config import IngesterConfig
from .service import IngesterService

__all__ = ["IngesterConfig", "IngesterService"]
