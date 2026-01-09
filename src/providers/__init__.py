"""
Provider implementations for the scraping framework.

This module provides both:
1. Adapter classes that wrap existing scrapers for backward compatibility
2. New framework-native implementations using BaseScraper

The new implementations use:
- BrowserManager for browser operations
- ProviderConfig for configuration
- LeaseOffer for output
"""

# New framework-native implementations
from .toyota import ToyotaNLScraper
from .leasys import LeasysNLScraper

# Legacy adapters (for backward compatibility)
from .adapters import (
    ToyotaScraperAdapter,
    SuzukiScraperAdapter,
    AyvensScraperAdapter,
    LeasysScraperAdapter,
    register_all_providers,
)

__all__ = [
    # New implementations
    "ToyotaNLScraper",
    "LeasysNLScraper",
    # Legacy adapters
    "ToyotaScraperAdapter",
    "SuzukiScraperAdapter",
    "AyvensScraperAdapter",
    "LeasysScraperAdapter",
    "register_all_providers",
]
