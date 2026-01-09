"""
Abstract base class for all lease price scrapers.

This module defines the interface that all provider-specific scrapers
must implement, ensuring consistent behavior across the framework.
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from datetime import datetime

from .browser import BrowserManager
from .schema import (
    LeaseOffer,
    Provider,
    Country,
    Currency,
    PriceMatrix,
)

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """
    Abstract base class for lease price scrapers.

    All provider-specific scrapers should extend this class and implement
    the abstract methods. Common functionality like browser management,
    rate limiting, and data conversion is provided by the base class.

    Class Attributes:
        PROVIDER: Provider enum value for this scraper
        COUNTRY: Country enum value
        CURRENCY: Currency for prices
        BASE_URL: Main website URL
        REQUEST_DELAY: Seconds between requests

    Example:
        class MyScraper(BaseScraper):
            PROVIDER = Provider.MY_PROVIDER
            COUNTRY = Country.NL
            BASE_URL = "https://example.com"

            def discover_vehicles(self) -> List[Dict]:
                # Implementation...

            def scrape_vehicle_prices(self, vehicle: Dict) -> LeaseOffer:
                # Implementation...
    """

    # Override these in subclasses
    PROVIDER: Provider = None
    COUNTRY: Country = Country.NL
    CURRENCY: Currency = Currency.EUR
    BASE_URL: str = ""
    REQUEST_DELAY: float = 2.0

    # Price matrix dimensions (can be overridden per provider)
    DURATIONS: List[int] = [24, 36, 48, 60, 72]
    MILEAGES: List[int] = [5000, 10000, 15000, 20000, 25000, 30000]

    def __init__(self, headless: bool = True):
        """
        Initialize the scraper.

        Args:
            headless: Run browser in headless mode
        """
        self.headless = headless
        self._browser: Optional[BrowserManager] = None
        self._scrape_timestamp: Optional[datetime] = None

    @property
    def browser(self) -> BrowserManager:
        """Lazy initialization of browser manager."""
        if self._browser is None:
            self._browser = BrowserManager(
                headless=self.headless,
                request_delay=self.REQUEST_DELAY,
            )
        return self._browser

    def close(self):
        """Clean up resources."""
        if self._browser:
            self._browser.close()
            self._browser = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False

    # === Abstract methods - must be implemented by subclasses ===

    @abstractmethod
    def discover_vehicles(self) -> List[Dict[str, Any]]:
        """
        Discover all available vehicles/editions from the provider.

        Returns:
            List of dictionaries containing vehicle information needed
            to scrape individual prices. The exact structure depends on
            the provider but should include enough info to identify and
            fetch price details.

        Example return:
            [
                {'model': 'Yaris', 'edition': 'Active', 'url': '...'},
                {'model': 'Yaris', 'edition': 'Style', 'url': '...'},
            ]
        """
        pass

    @abstractmethod
    def scrape_vehicle_prices(self, vehicle: Dict[str, Any]) -> Optional[LeaseOffer]:
        """
        Scrape full price matrix for a single vehicle.

        Args:
            vehicle: Vehicle info dict from discover_vehicles()

        Returns:
            LeaseOffer with complete price matrix, or None if scraping failed
        """
        pass

    # === Optional methods - can be overridden ===

    def filter_vehicles(
        self,
        vehicles: List[Dict[str, Any]],
        model: Optional[str] = None,
        brand: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Filter discovered vehicles by model or brand.

        Args:
            vehicles: List of vehicle dicts from discover_vehicles()
            model: Filter by model name (case-insensitive substring)
            brand: Filter by brand name (case-insensitive substring)

        Returns:
            Filtered list of vehicles
        """
        filtered = vehicles

        if model:
            model_lower = model.lower()
            filtered = [
                v for v in filtered
                if model_lower in v.get('model', '').lower()
            ]

        if brand:
            brand_lower = brand.lower()
            filtered = [
                v for v in filtered
                if brand_lower in v.get('brand', '').lower()
            ]

        return filtered

    def pre_scrape_hook(self):
        """
        Called before starting a scrape session.

        Override to perform setup like accepting cookies, logging in, etc.
        """
        pass

    def post_scrape_hook(self, offers: List[LeaseOffer]):
        """
        Called after completing a scrape session.

        Override to perform cleanup or post-processing.

        Args:
            offers: List of scraped offers
        """
        pass

    # === Main scraping methods ===

    def scrape_all(
        self,
        model: Optional[str] = None,
        brand: Optional[str] = None,
    ) -> List[LeaseOffer]:
        """
        Scrape all vehicles from the provider.

        Args:
            model: Optional model name filter
            brand: Optional brand name filter

        Returns:
            List of LeaseOffer objects with complete price matrices
        """
        self._scrape_timestamp = datetime.utcnow()
        offers = []

        try:
            logger.info(f"Starting scrape for {self.PROVIDER.value if self.PROVIDER else 'unknown'}")

            # Pre-scrape setup
            self.pre_scrape_hook()

            # Discover vehicles
            logger.info("Discovering vehicles...")
            vehicles = self.discover_vehicles()
            logger.info(f"Found {len(vehicles)} vehicles")

            # Apply filters
            if model or brand:
                vehicles = self.filter_vehicles(vehicles, model=model, brand=brand)
                logger.info(f"Filtered to {len(vehicles)} vehicles")

            # Scrape each vehicle
            for i, vehicle in enumerate(vehicles, 1):
                vehicle_name = vehicle.get('model', 'Unknown')
                edition = vehicle.get('edition', vehicle.get('edition_name', ''))
                if edition:
                    vehicle_name = f"{vehicle_name} {edition}"

                logger.info(f"Scraping {i}/{len(vehicles)}: {vehicle_name}")

                try:
                    offer = self.scrape_vehicle_prices(vehicle)
                    if offer:
                        offers.append(offer)
                except Exception as e:
                    logger.error(f"Error scraping {vehicle_name}: {e}")
                    continue

            # Post-scrape hook
            self.post_scrape_hook(offers)

            logger.info(f"Completed scraping {len(offers)} offers")

        except Exception as e:
            logger.error(f"Scrape failed: {e}")
            raise
        finally:
            self.close()

        return offers

    def scrape_model(self, model: str) -> List[LeaseOffer]:
        """
        Scrape all editions of a specific model.

        Args:
            model: Model name to scrape

        Returns:
            List of LeaseOffer objects
        """
        return self.scrape_all(model=model)

    # === Utility methods ===

    def create_offer(
        self,
        brand: str,
        model: str,
        edition_name: str = "",
        variant: str = "",
        fuel_type: str = "",
        transmission: str = "",
        power: Optional[str] = None,
        price_matrix: Optional[Dict[str, float]] = None,
        source_url: Optional[str] = None,
        vehicle_id: Optional[str] = None,
        is_new: bool = True,
        **kwargs
    ) -> LeaseOffer:
        """
        Create a LeaseOffer with provider defaults filled in.

        This helper method ensures consistent offer creation with
        provider-specific defaults.

        Args:
            brand: Vehicle brand
            model: Vehicle model
            edition_name: Edition/trim name
            variant: Full variant description
            fuel_type: Fuel type string
            transmission: Transmission string
            power: Engine power string
            price_matrix: Price matrix dict
            source_url: URL to offer on provider site
            vehicle_id: Provider-specific ID
            is_new: True for new vehicles, False for used
            **kwargs: Additional fields passed to LeaseOffer

        Returns:
            LeaseOffer instance
        """
        from .schema import (
            fuel_type_from_string,
            transmission_from_string,
            VehicleCondition,
        )

        return LeaseOffer(
            provider=self.PROVIDER,
            country=self.COUNTRY,
            currency=self.CURRENCY,
            brand=brand,
            model=model,
            edition_name=edition_name,
            variant=variant,
            fuel_type=fuel_type_from_string(fuel_type),
            transmission=transmission_from_string(transmission),
            power=power,
            condition=VehicleCondition.NEW if is_new else VehicleCondition.USED,
            price_matrix=PriceMatrix(prices=price_matrix or {}),
            source_url=source_url,
            vehicle_id=vehicle_id,
            scraped_at=self._scrape_timestamp or datetime.utcnow(),
            **kwargs
        )

    def to_legacy_format(self, offers: List[LeaseOffer]) -> List[Dict[str, Any]]:
        """
        Convert offers to legacy JSON format for backward compatibility.

        Args:
            offers: List of LeaseOffer objects

        Returns:
            List of dictionaries in legacy format
        """
        return [offer.to_legacy_dict() for offer in offers]


class MultiModelScraper(BaseScraper):
    """
    Extended base class for scrapers that handle multiple models.

    Provides additional utilities for model-centric scraping where
    the flow is: discover models -> discover editions -> scrape prices.
    """

    # Map of known models with metadata
    KNOWN_MODELS: Dict[str, Dict[str, Any]] = {}

    @abstractmethod
    def discover_models(self) -> List[Dict[str, Any]]:
        """
        Discover all available models.

        Returns:
            List of model info dictionaries
        """
        pass

    @abstractmethod
    def discover_model_editions(self, model: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Discover all editions for a specific model.

        Args:
            model: Model info dict from discover_models()

        Returns:
            List of edition info dictionaries
        """
        pass

    def discover_vehicles(self) -> List[Dict[str, Any]]:
        """
        Discover all vehicles by iterating models and editions.

        Returns:
            List of vehicle (edition) dictionaries
        """
        all_vehicles = []

        models = self.discover_models()
        logger.info(f"Found {len(models)} models")

        for model in models:
            model_name = model.get('name', model.get('model', 'Unknown'))
            logger.info(f"Discovering editions for {model_name}...")

            try:
                editions = self.discover_model_editions(model)
                for edition in editions:
                    # Merge model info into edition
                    vehicle = {**model, **edition}
                    all_vehicles.append(vehicle)
            except Exception as e:
                logger.error(f"Error discovering editions for {model_name}: {e}")
                continue

        return all_vehicles


class MultiBrandScraper(BaseScraper):
    """
    Extended base class for scrapers that handle multiple brands.

    Used for aggregator sites like Ayvens and Leasys that list
    vehicles from multiple manufacturers.
    """

    # Brands supported by this scraper
    SUPPORTED_BRANDS: List[str] = []

    def __init__(self, headless: bool = True, brand: Optional[str] = None):
        """
        Initialize multi-brand scraper.

        Args:
            headless: Run browser in headless mode
            brand: Optional brand filter
        """
        super().__init__(headless=headless)
        self.brand_filter = brand

    def filter_vehicles(
        self,
        vehicles: List[Dict[str, Any]],
        model: Optional[str] = None,
        brand: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Filter with brand filter from init if set."""
        brand = brand or self.brand_filter
        return super().filter_vehicles(vehicles, model=model, brand=brand)

    @abstractmethod
    def discover_brands(self) -> List[str]:
        """
        Discover all available brands.

        Returns:
            List of brand names
        """
        pass

    @abstractmethod
    def discover_brand_vehicles(self, brand: str) -> List[Dict[str, Any]]:
        """
        Discover all vehicles for a specific brand.

        Args:
            brand: Brand name

        Returns:
            List of vehicle dictionaries
        """
        pass

    def discover_vehicles(self) -> List[Dict[str, Any]]:
        """
        Discover all vehicles across brands.

        Returns:
            List of vehicle dictionaries
        """
        all_vehicles = []

        # Use brand filter or discover all brands
        if self.brand_filter:
            brands = [self.brand_filter]
        else:
            brands = self.discover_brands()

        logger.info(f"Scraping {len(brands)} brands: {brands}")

        for brand in brands:
            logger.info(f"Discovering vehicles for {brand}...")
            try:
                vehicles = self.discover_brand_vehicles(brand)
                # Ensure brand is set on each vehicle
                for v in vehicles:
                    v['brand'] = brand
                all_vehicles.extend(vehicles)
            except Exception as e:
                logger.error(f"Error discovering {brand} vehicles: {e}")
                continue

        return all_vehicles
