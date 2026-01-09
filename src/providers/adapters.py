"""
Adapter classes for existing scrapers.

These adapters wrap the existing scraper implementations to provide
a unified interface compatible with the new framework, while preserving
all the proven scraping logic.
"""

import sys
import os
import logging
from typing import Dict, List, Optional, Any
from dataclasses import asdict

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.core.schema import (
    LeaseOffer,
    Provider,
    Country,
    Currency,
    PriceMatrix,
    FuelType,
    Transmission,
    VehicleCondition,
    fuel_type_from_string,
    transmission_from_string,
)
from src.core.base_scraper import BaseScraper
from src.core.registry import ScraperRegistry, register_scraper
from src.core.config import get_provider_config

logger = logging.getLogger(__name__)


class BaseScraperAdapter(BaseScraper):
    """
    Base adapter class for wrapping existing scrapers.

    Provides common functionality for converting legacy scraper output
    to the unified LeaseOffer schema. Inherits from BaseScraper to be
    compatible with the registry system.
    """

    PROVIDER: Provider = None
    COUNTRY: Country = Country.NL
    CURRENCY: Currency = Currency.EUR

    def __init__(self, headless: bool = True, **kwargs):
        super().__init__(headless=headless)
        self._legacy_scraper = None  # The underlying legacy scraper
        self._provider_config = None

    @property
    def provider_config(self):
        """Get provider configuration."""
        if self._provider_config is None and self.PROVIDER:
            self._provider_config = get_provider_config(self.PROVIDER.value)
        return self._provider_config

    def close(self):
        """Clean up resources."""
        if self._legacy_scraper:
            self._legacy_scraper.close()
            self._legacy_scraper = None
        super().close()

    def _to_lease_offer(self, data: Dict[str, Any], brand: str) -> LeaseOffer:
        """Convert legacy scraper output to LeaseOffer."""
        raise NotImplementedError

    # Required abstract methods from BaseScraper
    def discover_vehicles(self) -> List[Dict[str, Any]]:
        """Not used in adapters - legacy scrapers handle discovery internally."""
        return []

    def scrape_vehicle_prices(self, vehicle: Dict[str, Any]) -> Optional[LeaseOffer]:
        """Not used in adapters - legacy scrapers handle this internally."""
        return None

    def scrape_all(self, **kwargs) -> List[LeaseOffer]:
        """Scrape all offers and return as LeaseOffer objects."""
        raise NotImplementedError

    def scrape_model(self, model: str) -> List[LeaseOffer]:
        """Scrape specific model and return as LeaseOffer objects."""
        raise NotImplementedError

    def to_legacy_format(self, offers: List[LeaseOffer]) -> List[Dict[str, Any]]:
        """Convert LeaseOffer objects back to legacy format."""
        return [offer.to_legacy_dict() for offer in offers]


# Note: ToyotaScraperAdapter is kept for backward compatibility but
# ToyotaNLScraper in toyota.py is the primary implementation
class ToyotaScraperAdapter(BaseScraperAdapter):
    """Adapter for Toyota.nl scraper (legacy - use ToyotaNLScraper instead)."""

    PROVIDER = Provider.TOYOTA_NL
    BRAND = "Toyota"

    @property
    def legacy_scraper(self):
        """Lazy initialization of underlying scraper."""
        if self._legacy_scraper is None:
            from toyota_scraper import ToyotaScraper
            self._legacy_scraper = ToyotaScraper(headless=self.headless)
        return self._legacy_scraper

    def _to_lease_offer(self, edition_dict: Dict[str, Any], brand: str = "Toyota") -> LeaseOffer:
        """Convert ToyotaEdition dict to LeaseOffer."""
        return LeaseOffer(
            provider=self.PROVIDER,
            country=self.COUNTRY,
            currency=self.CURRENCY,
            brand=brand,
            model=edition_dict.get('model', ''),
            edition_name=edition_dict.get('edition_name', ''),
            variant=edition_dict.get('edition_slug', ''),
            fuel_type=fuel_type_from_string(edition_dict.get('fuel_type', '')),
            transmission=transmission_from_string(edition_dict.get('transmission', '')),
            power=edition_dict.get('power'),
            condition=VehicleCondition.NEW,
            price_matrix=PriceMatrix(prices=edition_dict.get('price_matrix', {})),
            source_url=edition_dict.get('configurator_url') or edition_dict.get('base_url'),
            raw_data=edition_dict,
        )

    def scrape_all(self, use_cache: bool = False, **kwargs) -> List[LeaseOffer]:
        """Scrape all Toyota editions."""
        try:
            editions = self.legacy_scraper.scrape_all(use_cache=use_cache)
            return [self._to_lease_offer(asdict(e)) for e in editions]
        finally:
            self.close()

    def scrape_model(self, model: str) -> List[LeaseOffer]:
        """Scrape specific Toyota model."""
        try:
            editions = self.legacy_scraper.scrape_model(model)
            return [self._to_lease_offer(asdict(e)) for e in editions]
        finally:
            self.close()


@register_scraper(Provider.SUZUKI_NL)
class SuzukiScraperAdapter(BaseScraperAdapter):
    """Adapter for Suzuki.nl scraper."""

    PROVIDER = Provider.SUZUKI_NL
    BRAND = "Suzuki"

    @property
    def legacy_scraper(self):
        """Lazy initialization of underlying scraper."""
        if self._legacy_scraper is None:
            from suzuki_scraper import SuzukiScraper
            self._legacy_scraper = SuzukiScraper(headless=self.headless)
        return self._legacy_scraper

    def _to_lease_offer(self, edition_dict: Dict[str, Any], brand: str = "Suzuki") -> LeaseOffer:
        """Convert SuzukiEdition dict to LeaseOffer."""
        return LeaseOffer(
            provider=self.PROVIDER,
            country=self.COUNTRY,
            currency=self.CURRENCY,
            brand=brand,
            model=edition_dict.get('model', ''),
            edition_name=edition_dict.get('edition_name', ''),
            variant=edition_dict.get('edition_slug', ''),
            fuel_type=fuel_type_from_string(edition_dict.get('fuel_type', '')),
            transmission=transmission_from_string(edition_dict.get('transmission', '')),
            power=edition_dict.get('power'),
            condition=VehicleCondition.NEW,
            price_matrix=PriceMatrix(prices=edition_dict.get('price_matrix', {})),
            source_url=edition_dict.get('configurator_url') or edition_dict.get('base_url'),
            raw_data=edition_dict,
        )

    def scrape_all(self, use_cache: bool = False, **kwargs) -> List[LeaseOffer]:
        """Scrape all Suzuki editions."""
        try:
            editions = self.legacy_scraper.scrape_all(use_cache=use_cache)
            return [self._to_lease_offer(asdict(e)) for e in editions]
        finally:
            self.close()

    def scrape_model(self, model: str) -> List[LeaseOffer]:
        """Scrape specific Suzuki model."""
        try:
            editions = self.legacy_scraper.scrape_model(model)
            return [self._to_lease_offer(asdict(e)) for e in editions]
        finally:
            self.close()


@register_scraper(Provider.AYVENS_NL)
class AyvensScraperAdapter(BaseScraperAdapter):
    """Adapter for Ayvens.com scraper."""

    PROVIDER = Provider.AYVENS_NL

    def __init__(self, headless: bool = True, brand: Optional[str] = None, **kwargs):
        super().__init__(headless=headless, **kwargs)
        self.brand_filter = brand

    @property
    def legacy_scraper(self):
        """Lazy initialization of underlying scraper."""
        if self._legacy_scraper is None:
            from ayvens_scraper import AyvensScraper
            self._legacy_scraper = AyvensScraper(headless=self.headless)
        return self._legacy_scraper

    def _to_lease_offer(self, offer_dict: Dict[str, Any], brand: str = "Toyota") -> LeaseOffer:
        """Convert AyvensOffer dict to LeaseOffer."""
        is_new = offer_dict.get('is_new', True)
        return LeaseOffer(
            provider=self.PROVIDER,
            country=self.COUNTRY,
            currency=self.CURRENCY,
            brand=offer_dict.get('brand', brand),
            model=offer_dict.get('model', ''),
            edition_name=offer_dict.get('edition_name', ''),
            variant=offer_dict.get('variant', ''),
            fuel_type=fuel_type_from_string(offer_dict.get('fuel_type', '')),
            transmission=transmission_from_string(offer_dict.get('transmission', '')),
            power=offer_dict.get('power'),
            condition=VehicleCondition.NEW if is_new else VehicleCondition.USED,
            price_matrix=PriceMatrix(prices=offer_dict.get('price_matrix', {})),
            source_url=offer_dict.get('offer_url'),
            vehicle_id=offer_dict.get('vehicle_id'),
            raw_data=offer_dict,
        )

    def scrape_all(self, brand: Optional[str] = None, **kwargs) -> List[LeaseOffer]:
        """Scrape all Ayvens offers for a brand."""
        brand = brand or self.brand_filter or "Toyota"
        try:
            offers = self.legacy_scraper.scrape_brand(brand)
            return [self._to_lease_offer(asdict(o), brand) for o in offers]
        finally:
            self.close()

    def scrape_brand(self, brand: str) -> List[LeaseOffer]:
        """Scrape specific brand from Ayvens."""
        return self.scrape_all(brand=brand)

    def scrape_model(self, model: str, brand: str = "Toyota") -> List[LeaseOffer]:
        """Scrape specific model from Ayvens (scrapes all then filters)."""
        try:
            all_offers = self.legacy_scraper.scrape_brand(brand)
            model_lower = model.lower()
            filtered = [o for o in all_offers if model_lower in o.model.lower()]
            return [self._to_lease_offer(asdict(o), brand) for o in filtered]
        finally:
            self.close()


# Note: LeasysNLScraper in leasys.py is the primary implementation
class LeasysScraperAdapter(BaseScraperAdapter):
    """Adapter for Leasys.com scraper."""

    PROVIDER = Provider.LEASYS_NL

    def __init__(self, headless: bool = True, brand: Optional[str] = None, **kwargs):
        super().__init__(headless=headless, **kwargs)
        self.brand_filter = brand

    @property
    def legacy_scraper(self):
        """Lazy initialization of underlying scraper."""
        if self._legacy_scraper is None:
            from leasys_scraper import LeasysScraper
            self._legacy_scraper = LeasysScraper(headless=self.headless)
        return self._legacy_scraper

    def _to_lease_offer(self, offer_dict: Dict[str, Any], brand: str = "Toyota") -> LeaseOffer:
        """Convert LeasysOffer dict to LeaseOffer."""
        return LeaseOffer(
            provider=self.PROVIDER,
            country=self.COUNTRY,
            currency=self.CURRENCY,
            brand=offer_dict.get('brand', brand),
            model=offer_dict.get('model', ''),
            edition_name=offer_dict.get('edition_name', ''),
            variant=offer_dict.get('variant', ''),
            fuel_type=fuel_type_from_string(offer_dict.get('fuel_type', '')),
            transmission=transmission_from_string(offer_dict.get('transmission', '')),
            power=None,  # Leasys doesn't provide power info
            condition=VehicleCondition.NEW,
            price_matrix=PriceMatrix(prices=offer_dict.get('price_matrix', {})),
            source_url=offer_dict.get('offer_url'),
            raw_data=offer_dict,
        )

    def scrape_all(self, brand: Optional[str] = None, **kwargs) -> List[LeaseOffer]:
        """Scrape all Leasys offers for a brand."""
        brand = brand or self.brand_filter or "Toyota"
        try:
            offers = self.legacy_scraper.scrape_brand(brand)
            return [self._to_lease_offer(asdict(o), brand) for o in offers]
        finally:
            self.close()

    def scrape_brand(self, brand: str) -> List[LeaseOffer]:
        """Scrape specific brand from Leasys."""
        return self.scrape_all(brand=brand)

    def scrape_model(self, model: str, brand: str = "Toyota") -> List[LeaseOffer]:
        """Scrape specific model from Leasys."""
        try:
            offers = self.legacy_scraper.scrape_model(model)
            return [self._to_lease_offer(asdict(o), brand) for o in offers]
        finally:
            self.close()


def register_all_providers():
    """
    Ensure all provider adapters are registered.

    This function is called to trigger the @register_scraper decorators
    and populate the ScraperRegistry.
    """
    # The decorators have already registered the adapters when this module
    # was imported. This function just ensures the module is imported.
    providers = ScraperRegistry.list_provider_names()
    logger.info(f"Registered {len(providers)} providers: {providers}")
    return providers


# Convenience functions for direct usage
def scrape_toyota(model: Optional[str] = None, headless: bool = True) -> List[LeaseOffer]:
    """Scrape Toyota.nl offers."""
    with ToyotaScraperAdapter(headless=headless) as scraper:
        if model:
            return scraper.scrape_model(model)
        return scraper.scrape_all()


def scrape_suzuki(model: Optional[str] = None, headless: bool = True) -> List[LeaseOffer]:
    """Scrape Suzuki.nl offers."""
    with SuzukiScraperAdapter(headless=headless) as scraper:
        if model:
            return scraper.scrape_model(model)
        return scraper.scrape_all()


def scrape_ayvens(brand: str = "Toyota", headless: bool = True) -> List[LeaseOffer]:
    """Scrape Ayvens offers for a brand."""
    with AyvensScraperAdapter(headless=headless, brand=brand) as scraper:
        return scraper.scrape_all()


def scrape_leasys(brand: str = "Toyota", headless: bool = True) -> List[LeaseOffer]:
    """Scrape Leasys offers for a brand."""
    with LeasysScraperAdapter(headless=headless, brand=brand) as scraper:
        return scraper.scrape_all()
