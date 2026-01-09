"""
Provider registry for scraper management.

This module provides a registry pattern for discovering and instantiating
scrapers by provider name or type.
"""

import logging
from typing import Dict, Type, Optional, List, Any
from .base_scraper import BaseScraper
from .schema import Provider

logger = logging.getLogger(__name__)


class ScraperRegistry:
    """
    Registry for managing scraper classes.

    Provides centralized registration and lookup of scraper implementations.
    Scrapers can be registered manually or auto-discovered.

    Usage:
        # Register a scraper
        registry = ScraperRegistry()
        registry.register(Provider.TOYOTA_NL, ToyotaScraper)

        # Get scraper instance
        scraper = registry.get_scraper(Provider.TOYOTA_NL)

        # Or by string name
        scraper = registry.get_scraper('toyota_nl')
    """

    _instance: Optional['ScraperRegistry'] = None
    _scrapers: Dict[Provider, Type[BaseScraper]] = {}

    def __new__(cls):
        """Singleton pattern - only one registry instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._scrapers = {}
        return cls._instance

    @classmethod
    def register(cls, provider: Provider, scraper_class: Type[BaseScraper]) -> None:
        """
        Register a scraper class for a provider.

        Args:
            provider: Provider enum value
            scraper_class: Scraper class (not instance)
        """
        if not issubclass(scraper_class, BaseScraper):
            raise TypeError(f"{scraper_class} must be a subclass of BaseScraper")

        cls._scrapers[provider] = scraper_class
        logger.debug(f"Registered scraper for {provider.value}: {scraper_class.__name__}")

    @classmethod
    def get_scraper_class(cls, provider: Provider | str) -> Optional[Type[BaseScraper]]:
        """
        Get scraper class for a provider.

        Args:
            provider: Provider enum or string name

        Returns:
            Scraper class or None if not found
        """
        if isinstance(provider, str):
            try:
                provider = Provider(provider)
            except ValueError:
                # Try case-insensitive match
                provider_lower = provider.lower()
                for p in Provider:
                    if p.value.lower() == provider_lower:
                        provider = p
                        break
                else:
                    logger.warning(f"Unknown provider: {provider}")
                    return None

        return cls._scrapers.get(provider)

    @classmethod
    def get_scraper(
        cls,
        provider: Provider | str,
        headless: bool = True,
        **kwargs
    ) -> Optional[BaseScraper]:
        """
        Get scraper instance for a provider.

        Args:
            provider: Provider enum or string name
            headless: Run browser in headless mode
            **kwargs: Additional arguments passed to scraper constructor

        Returns:
            Scraper instance or None if not found
        """
        scraper_class = cls.get_scraper_class(provider)
        if scraper_class is None:
            return None

        return scraper_class(headless=headless, **kwargs)

    @classmethod
    def list_providers(cls) -> List[Provider]:
        """Get list of registered providers."""
        return list(cls._scrapers.keys())

    @classmethod
    def list_provider_names(cls) -> List[str]:
        """Get list of registered provider names."""
        return [p.value for p in cls._scrapers.keys()]

    @classmethod
    def is_registered(cls, provider: Provider | str) -> bool:
        """Check if a provider is registered."""
        return cls.get_scraper_class(provider) is not None

    @classmethod
    def clear(cls) -> None:
        """Clear all registered scrapers (mainly for testing)."""
        cls._scrapers.clear()


def register_scraper(provider: Provider):
    """
    Decorator to register a scraper class.

    Usage:
        @register_scraper(Provider.TOYOTA_NL)
        class ToyotaScraper(BaseScraper):
            ...
    """
    def decorator(cls: Type[BaseScraper]) -> Type[BaseScraper]:
        ScraperRegistry.register(provider, cls)
        return cls
    return decorator


# Convenience functions
def get_scraper(provider: Provider | str, **kwargs) -> Optional[BaseScraper]:
    """Get scraper instance by provider."""
    return ScraperRegistry.get_scraper(provider, **kwargs)


def list_providers() -> List[str]:
    """List all registered provider names."""
    return ScraperRegistry.list_provider_names()


def scrape_provider(
    provider: Provider | str,
    model: Optional[str] = None,
    brand: Optional[str] = None,
    headless: bool = True,
    **kwargs
) -> List[Any]:
    """
    Convenience function to scrape a provider.

    Args:
        provider: Provider enum or string name
        model: Optional model filter
        brand: Optional brand filter
        headless: Run browser in headless mode
        **kwargs: Additional scraper arguments

    Returns:
        List of LeaseOffer objects
    """
    scraper = get_scraper(provider, headless=headless, **kwargs)
    if scraper is None:
        raise ValueError(f"No scraper registered for provider: {provider}")

    with scraper:
        return scraper.scrape_all(model=model, brand=brand)
