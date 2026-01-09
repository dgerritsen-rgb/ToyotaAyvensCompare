"""
Leasys.com scraper using the new framework.

This is a proper implementation using the framework's base classes,
BrowserManager, and configuration system.
"""

import re
import time
import logging
from typing import Dict, List, Optional, Any
from urllib.parse import unquote

from bs4 import BeautifulSoup
from tqdm import tqdm

from src.core.base_scraper import MultiBrandScraper
from src.core.schema import (
    LeaseOffer,
    Provider,
    Country,
    Currency,
    PriceMatrix,
    FuelType,
    Transmission,
    fuel_type_from_string,
    transmission_from_string,
    VehicleCondition,
)
from src.core.config import get_provider_config
from src.core.registry import register_scraper

logger = logging.getLogger(__name__)


@register_scraper(Provider.LEASYS_NL)
class LeasysNLScraper(MultiBrandScraper):
    """
    Leasys Netherlands scraper using the new framework.

    Uses BrowserManager for browser operations and outputs LeaseOffer objects.
    """

    PROVIDER = Provider.LEASYS_NL
    COUNTRY = Country.NL
    CURRENCY = Currency.EUR
    BASE_URL = "https://store.leasys.com"
    REQUEST_DELAY = 2.0

    # Known Toyota models on Leasys
    KNOWN_TOYOTA_MODELS = [
        {"slug": "AYGO%20X", "name": "Aygo X"},
        {"slug": "Yaris", "name": "Yaris"},
        {"slug": "Corolla%20Cross", "name": "Corolla Cross"},
    ]

    # Known Suzuki models on Leasys
    KNOWN_SUZUKI_MODELS = [
        {"slug": "Swift", "name": "Swift"},
        {"slug": "Vitara", "name": "Vitara"},
        {"slug": "S-Cross", "name": "S-Cross"},
        {"slug": "Swace", "name": "Swace"},
        {"slug": "Across", "name": "Across"},
        {"slug": "e-Vitara", "name": "e-Vitara"},
    ]

    def __init__(self, headless: bool = True, brand: Optional[str] = None):
        super().__init__(headless=headless)
        self._config = get_provider_config('leasys_nl')
        self.brand_filter = brand

    def discover_brands(self) -> List[str]:
        """Return supported brands."""
        return ["Toyota", "Suzuki"]

    def _get_brand_models(self, brand: str) -> List[Dict[str, Any]]:
        """Get known models for a specific brand."""
        if brand.lower() == "toyota":
            return [
                {
                    'model_slug': m['slug'],
                    'model_name': m['name'],
                    'brand': brand,
                    'url': f"{self.BASE_URL}/nl/private/brands/Toyota/{m['slug']}",
                }
                for m in self.KNOWN_TOYOTA_MODELS
            ]
        elif brand.lower() == "suzuki":
            return [
                {
                    'model_slug': m['slug'],
                    'model_name': m['name'],
                    'brand': brand,
                    'url': f"{self.BASE_URL}/nl/private/brands/Suzuki/{m['slug']}",
                }
                for m in self.KNOWN_SUZUKI_MODELS
            ]
        return []

    def discover_brand_vehicles(self, brand: str) -> List[Dict[str, Any]]:
        """Discover all vehicles for a specific brand."""
        all_vehicles = []
        models = self._get_brand_models(brand)

        logger.info(f"Discovering {brand} models from Leasys...")

        for model in models:
            editions = self._discover_editions(model)
            for edition in editions:
                all_vehicles.append({
                    **edition,
                    'brand': brand,
                    'model_name': model['model_name'],
                })

        return all_vehicles

    def _discover_editions(self, model: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Discover available editions/trims for a model."""
        brand = model.get('brand', 'Toyota')
        logger.info(f"  Discovering editions for {brand} {model['model_name']}...")
        editions = []

        try:
            self.browser.get(model['url'])
            self.browser.handle_cookie_consent()
            time.sleep(2)

            soup = BeautifulSoup(self.browser.page_source, 'lxml')

            # Normalize brand and model slug for URL matching
            brand_slug = brand.lower().replace(' ', '-').replace('&', '-')
            model_slug_normalized = model['model_name'].lower().replace(' ', '-').replace('%20', '-')

            # Find edition links
            links = soup.find_all('a', href=True)
            seen = set()

            for link in links:
                href = link.get('href', '')
                edition_slug = None

                # Pattern 1: /nl/private/{brand-slug}/{model-slug}/{edition}/...
                pattern1 = rf'/nl/private/{re.escape(brand_slug)}/{re.escape(model_slug_normalized)}/([a-z0-9-]+)/'
                match = re.search(pattern1, href, re.IGNORECASE)
                if match:
                    edition_slug = match.group(1).lower()

                # Pattern 2: /nl/private/brands/{Brand}/{Model}/{edition}/...
                if not edition_slug:
                    model_slug_url = re.escape(model.get('model_slug', model['model_name']))
                    pattern2 = rf'/nl/private/brands/[^/]+/{model_slug_url}/([a-z0-9-]+)/'
                    match = re.search(pattern2, href, re.IGNORECASE)
                    if match:
                        edition_slug = match.group(1).lower()

                if edition_slug and edition_slug not in seen:
                    # Skip if it's not a new car (factory)
                    if '/factory/' not in href:
                        continue

                    seen.add(edition_slug)
                    full_url = self.BASE_URL + href if href.startswith('/') else href
                    edition_name = edition_slug.replace('-', ' ').title()

                    editions.append({
                        'edition_slug': edition_slug,
                        'edition_name': edition_name,
                        'url': full_url,
                        'model_name': model['model_name'],
                        'model_slug': model_slug_normalized,
                        'brand': brand,
                    })

            logger.info(f"    Found {len(editions)} editions")
            return editions

        except Exception as e:
            logger.error(f"Error discovering editions: {e}")
            return []

    def _scrape_edition_prices(self, edition: Dict[str, Any]) -> Dict[str, float]:
        """Scrape all price combinations for an edition using URL params."""
        price_matrix = {}
        base_url = edition['url']

        # Remove existing query params
        if '?' in base_url:
            base_url = base_url.split('?')[0]

        durations = self._config.price_matrix.durations if self._config else [24, 36, 48, 60, 72]
        mileages = self._config.price_matrix.mileages if self._config else [5000, 10000, 15000, 20000]

        combos = [(d, m) for d in durations for m in mileages]

        brand = edition.get('brand', 'Unknown')
        model = edition.get('model_name', 'Unknown')
        edition_name = edition.get('edition_name', 'Unknown')
        desc = f"Leasys | {brand} | {model} | {edition_name}"

        with tqdm(combos, unit="price", leave=False,
                  bar_format='{desc} {n_fmt}/{total_fmt} {bar}') as pbar:
            for duration, mileage in pbar:
                pbar.set_description(f"{desc} | {duration}mo/{mileage:,}km")

                # Build URL with specific duration and mileage
                url = f"{base_url}?annualMileage={mileage}&term={duration}"

                self.browser.get(url)
                time.sleep(2)

                price = self._get_current_price()
                if price:
                    key = f"{duration}_{mileage}"
                    price_matrix[key] = price

        return price_matrix

    def _get_current_price(self) -> Optional[float]:
        """Get current displayed price from page."""
        try:
            soup = BeautifulSoup(self.browser.page_source, 'lxml')

            # Try specific Leasys price selectors
            price_selectors = [
                '[class*="StyledPriceInteger"]',
                '[class*="StyledPrice"]',
                '[class*="Price__Styled"]',
                '[class*="price"]',
                '[class*="Price"]',
            ]

            for selector in price_selectors:
                elements = soup.select(selector)
                for elem in elements:
                    text = elem.get_text(strip=True)
                    # Look for 3-digit price
                    match = re.search(r'€?\s*(\d{3,4})(?:\s|$|€)', text)
                    if match:
                        price = float(match.group(1))
                        if 200 <= price <= 1500:
                            return price

            # Fallback: search entire page
            all_text = soup.get_text(' ', strip=True)
            prices = re.findall(r'€\s*(\d{3,4})(?:\s|$)', all_text)
            for price_str in prices:
                price = float(price_str)
                if 200 <= price <= 1500:
                    return price

        except Exception as e:
            logger.debug(f"Error getting price: {e}")
        return None

    def _guess_fuel_type(self, brand: str, model: str, edition: str) -> FuelType:
        """Guess fuel type based on model/edition name."""
        combined = f"{model} {edition}".lower()
        if 'electric' in combined or 'ev' in combined or 'bz' in combined:
            return FuelType.ELECTRIC
        if 'plug-in' in combined or 'phev' in combined:
            return FuelType.PLUG_IN_HYBRID
        if brand.lower() == 'toyota':
            return FuelType.HYBRID
        if brand.lower() == 'suzuki':
            if 'swace' in model.lower() or 'across' in model.lower():
                return FuelType.HYBRID
            return FuelType.PETROL
        return FuelType.UNKNOWN

    def scrape_vehicle_prices(self, vehicle: Dict[str, Any]) -> Optional[LeaseOffer]:
        """Scrape prices for a vehicle and return LeaseOffer."""
        price_matrix = self._scrape_edition_prices(vehicle)

        if not price_matrix:
            return None

        fuel_type = self._guess_fuel_type(
            vehicle.get('brand', ''),
            vehicle.get('model_name', ''),
            vehicle.get('edition_name', '')
        )

        return LeaseOffer(
            provider=self.PROVIDER,
            country=self.COUNTRY,
            currency=self.CURRENCY,
            brand=vehicle.get('brand', ''),
            model=vehicle.get('model_name', ''),
            edition_name=vehicle.get('edition_name', ''),
            variant=vehicle.get('edition_slug', ''),
            fuel_type=fuel_type,
            transmission=Transmission.AUTOMATIC,  # Leasys mostly has automatics
            condition=VehicleCondition.NEW,
            price_matrix=PriceMatrix(prices=price_matrix),
            source_url=vehicle.get('url'),
        )

    def scrape_all(self, brand: Optional[str] = None, **kwargs) -> List[LeaseOffer]:
        """Scrape all Leasys offers for a brand."""
        brand = brand or self.brand_filter or "Toyota"

        try:
            logger.info(f"Starting Leasys {brand} scrape...")
            self.brand_filter = brand

            vehicles = self.discover_vehicles()
            logger.info(f"Found {len(vehicles)} editions to scrape")

            offers = []
            for vehicle in tqdm(vehicles, desc=f"Leasys | {brand}", unit="edition"):
                offer = self.scrape_vehicle_prices(vehicle)
                if offer:
                    offers.append(offer)

            logger.info(f"Scraped {len(offers)} offers for {brand}")
            return offers

        finally:
            self.close()

    def scrape_brand(self, brand: str) -> List[LeaseOffer]:
        """Scrape specific brand from Leasys."""
        return self.scrape_all(brand=brand)

    def scrape_model(self, model: str, brand: str = "Toyota") -> List[LeaseOffer]:
        """Scrape specific model from Leasys."""
        self.brand_filter = brand
        vehicles = self.discover_vehicles()
        model_lower = model.lower()
        filtered = [v for v in vehicles if model_lower in v.get('model_name', '').lower()]

        offers = []
        for vehicle in filtered:
            offer = self.scrape_vehicle_prices(vehicle)
            if offer:
                offers.append(offer)

        return offers
