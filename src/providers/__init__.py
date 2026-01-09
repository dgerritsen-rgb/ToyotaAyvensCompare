"""
Provider adapters for the scraping framework.

This module provides adapter classes that wrap the existing scrapers
to make them compatible with the new framework while preserving the
proven scraping logic.
"""

from .adapters import (
    ToyotaScraperAdapter,
    SuzukiScraperAdapter,
    AyvensScraperAdapter,
    LeasysScraperAdapter,
    register_all_providers,
)

__all__ = [
    "ToyotaScraperAdapter",
    "SuzukiScraperAdapter",
    "AyvensScraperAdapter",
    "LeasysScraperAdapter",
    "register_all_providers",
]
