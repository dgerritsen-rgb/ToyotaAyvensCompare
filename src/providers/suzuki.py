"""
Suzuki.nl scraper using the new framework.

This is a proper implementation using the framework's base classes,
BrowserManager, and configuration system.
"""

import re
import time
import logging
from typing import Dict, List, Optional, Any

from bs4 import BeautifulSoup
from tqdm import tqdm

from src.core.base_scraper import MultiModelScraper
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


@register_scraper(Provider.SUZUKI_NL)
class SuzukiNLScraper(MultiModelScraper):
    """
    Suzuki Netherlands scraper using the new framework.

    Uses BrowserManager for browser operations and outputs LeaseOffer objects.
    """

    PROVIDER = Provider.SUZUKI_NL
    COUNTRY = Country.NL
    CURRENCY = Currency.EUR
    BASE_URL = "https://www.suzuki.nl"
    OVERVIEW_URL = "https://www.suzuki.nl/auto/private-lease/modellen"
    MODEL_URL_BASE = "https://www.suzuki.nl/auto/private-lease"
    REQUEST_DELAY = 2.0

    # Suzuki models available for private lease
    KNOWN_MODELS = {
        "swift": "Swift",
        "vitara": "Vitara",
        "s-cross": "S-Cross",
        "swace": "Swace",
        "across": "Across",
        "e-vitara": "e VITARA",
    }

    # Known edition/trim names
    KNOWN_EDITIONS = [
        'Active', 'Comfort', 'Select', 'Style', 'Select Pro',
        'Comfort+', 'Stijl', 'Two Tone', 'AllGrip', 'AllGrip-e',
    ]

    def __init__(self, headless: bool = True):
        super().__init__(headless=headless)
        self._config = get_provider_config('suzuki_nl')

    def discover_models(self) -> List[Dict[str, Any]]:
        """Discover available models."""
        return [
            {"slug": slug, "name": name}
            for slug, name in self.KNOWN_MODELS.items()
        ]

    def discover_model_editions(self, model: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Discover editions for a specific model."""
        # Not used - we scrape directly from model pages
        return []

    def discover_vehicles(self) -> List[Dict[str, Any]]:
        """Discover all vehicles by visiting model pages."""
        logger.info("Discovering Suzuki editions from model pages...")
        all_vehicles = []

        self.browser.get(self.OVERVIEW_URL)
        self.browser.handle_cookie_consent()

        for model_slug, model_name in self.KNOWN_MODELS.items():
            logger.info(f"Processing model: {model_name}")
            editions = self._scrape_model_page(model_slug, model_name)

            for edition in editions:
                all_vehicles.append(edition)

            logger.info(f"  Found {len(editions)} editions for {model_name}")

        logger.info(f"Total vehicles discovered: {len(all_vehicles)}")
        return all_vehicles

    def _scrape_model_page(self, model_slug: str, model_name: str) -> List[Dict[str, Any]]:
        """Scrape editions and prices from a model page."""
        editions = []

        model_url = f"{self.MODEL_URL_BASE}/{model_slug}/"
        logger.info(f"Scraping prices from model page: {model_url}")

        self.browser.get(model_url)
        self.browser.handle_cookie_consent()
        time.sleep(2)

        # Get initial edition info and count
        initial_prices = self._extract_prices_from_page()
        num_editions = len(initial_prices)
        logger.info(f"  Found {num_editions} editions on page")

        if num_editions == 0:
            # Try getting single price as fallback
            price = self._extract_single_price()
            if price:
                initial_prices = [{'price': price, 'edition_name': model_name}]
                num_editions = 1
            else:
                return []

        # Initialize price matrices
        edition_prices = {i: {} for i in range(num_editions)}

        # Scrape prices for all duration/mileage combinations
        durations = self._config.price_matrix.durations if self._config else [24, 36, 48, 60, 72]
        mileages = self._config.price_matrix.mileages if self._config else [5000, 10000, 15000, 20000, 25000, 30000]
        combos = [(d, k) for d in durations for k in mileages]

        with tqdm(combos, desc=f"Suzuki | {model_name}", unit="combo",
                  bar_format='{desc} {n_fmt}/{total_fmt} {bar} [{elapsed}<{remaining}]') as pbar:
            for duration, km in pbar:
                pbar.set_description(f"Suzuki | {model_name} | {duration}mo/{km:,}km")

                # Set dropdowns
                self._set_duration_km_dropdowns(duration, km)
                time.sleep(0.5)

                # Extract current prices
                current_prices = self._extract_prices_from_page()
                if not current_prices:
                    price = self._extract_single_price()
                    if price:
                        current_prices = [{'price': price}]

                # Store prices for each edition
                for idx, ep in enumerate(current_prices):
                    if idx < num_editions:
                        edition_prices[idx][f"{duration}_{km}"] = ep['price']

        # Create edition dictionaries
        fuel_type = self._get_fuel_type(model_name)
        for idx, ed_data in enumerate(initial_prices):
            edition_name = ed_data.get('edition_name', '')
            if not edition_name or self._is_price_text(edition_name):
                edition_name = f"Edition {idx+1}"

            edition_slug = f"suzuki-{model_slug}-{edition_name.lower().replace(' ', '-')}"

            if edition_prices.get(idx):
                editions.append({
                    'model': model_name,
                    'edition_name': edition_name,
                    'slug': edition_slug,
                    'fuel_type': fuel_type,
                    'transmission': 'Automatic',
                    'price_matrix': edition_prices[idx],
                    'source_url': model_url,
                })

        return editions

    def _extract_prices_from_page(self) -> List[Dict[str, Any]]:
        """Extract edition prices from current page."""
        soup = BeautifulSoup(self.browser.page_source, 'lxml')
        editions = []
        seen = set()

        # Find price elements
        price_selectors = [
            '[data-testid*="price"]',
            '[class*="price"]',
            '[class*="Price"]',
            '.lease-price',
            '.monthly-price',
        ]

        price_elements = []
        for selector in price_selectors:
            price_elements.extend(soup.select(selector))

        for elem in price_elements:
            price_text = elem.get_text(strip=True)
            match = re.search(r'€\s*(\d+)', price_text)
            if match:
                price = float(match.group(1))
                if 150 <= price <= 2000:
                    edition_name = self._extract_edition_name_from_element(elem)

                    if edition_name and self._is_price_text(edition_name):
                        edition_name = ""

                    key = edition_name if edition_name else f"edition_{len(editions)}"
                    if key in seen:
                        continue
                    seen.add(key)

                    editions.append({
                        'price': price,
                        'edition_name': edition_name,
                    })

        return editions

    def _extract_single_price(self) -> Optional[float]:
        """Extract a single price from the page."""
        soup = BeautifulSoup(self.browser.page_source, 'lxml')

        price_selectors = [
            '[data-testid*="price"]',
            '[class*="price"]',
            '[class*="Price"]',
        ]

        for selector in price_selectors:
            elements = soup.select(selector)
            for elem in elements:
                price_text = elem.get_text(strip=True)
                match = re.search(r'€\s*(\d+)', price_text)
                if match:
                    price = float(match.group(1))
                    if 150 <= price <= 2000:
                        return price
        return None

    def _extract_edition_name_from_element(self, elem) -> str:
        """Extract edition name from price element's context."""
        # Go up to find card container
        card = elem
        for _ in range(10):
            parent = card.find_parent()
            if not parent:
                break
            parent_class = ' '.join(parent.get('class', []))
            if any(k in parent_class.lower() for k in ['card', 'item', 'product', 'edition']):
                card = parent
                break
            card = parent

        text_content = card.get_text(' ', strip=True)

        # Check for known editions
        for edition in self.KNOWN_EDITIONS:
            if edition.lower() in text_content.lower():
                return edition

        # Look in headings
        for heading in card.find_all(['h2', 'h3', 'h4', 'h5']):
            heading_text = heading.get_text(strip=True)
            if self._is_price_text(heading_text):
                continue
            if re.match(r'^[\d\s.,]+$', heading_text):
                continue
            if 3 <= len(heading_text) <= 50:
                return heading_text

        return ""

    def _is_price_text(self, text: str) -> bool:
        """Check if text is a price rather than edition name."""
        if not text:
            return False
        price_patterns = [
            r'€', r'EUR', r'\d+,-', r'\d+,\d{2}',
            r'per\s*maand', r'p/m', r'incl\.?\s*btw',
            r'vanaf', r'^\d+$',
        ]
        return any(re.search(p, text, re.IGNORECASE) for p in price_patterns)

    def _set_duration_km_dropdowns(self, duration: int, km: int) -> bool:
        """Set duration and mileage dropdowns."""
        from selenium.webdriver.common.by import By

        try:
            selects = self.browser.driver.find_elements(By.CSS_SELECTOR, "select")
            duration_set = False
            km_set = False

            for select in selects:
                try:
                    options = select.find_elements(By.TAG_NAME, "option")
                    option_texts = [opt.text for opt in options]

                    # Duration dropdown
                    if any('maanden' in t or 'maand' in t for t in option_texts):
                        for opt in options:
                            if str(duration) in opt.text:
                                opt.click()
                                duration_set = True
                                break

                    # Mileage dropdown
                    elif any('km' in t.lower() for t in option_texts):
                        for opt in options:
                            opt_text = opt.text.replace(" ", "").replace(".", "")
                            if str(km) in opt_text:
                                opt.click()
                                km_set = True
                                break

                except Exception as e:
                    logger.debug(f"Error with select element: {e}")
                    continue

            if duration_set or km_set:
                time.sleep(0.5)

            return duration_set and km_set

        except Exception as e:
            logger.debug(f"Error setting dropdowns: {e}")
            return False

    def _get_fuel_type(self, model_name: str) -> str:
        """Get fuel type for a model."""
        model_lower = model_name.lower()
        if 'e-vitara' in model_lower or 'e vitara' in model_lower:
            return "Electric"
        elif 'swace' in model_lower or 'across' in model_lower:
            return "Hybrid"
        return "Hybrid"  # Most Suzuki models are mild hybrid

    def scrape_vehicle_prices(self, vehicle: Dict[str, Any]) -> Optional[LeaseOffer]:
        """Convert vehicle dict to LeaseOffer."""
        if not vehicle.get('price_matrix'):
            return None

        return LeaseOffer(
            provider=self.PROVIDER,
            country=self.COUNTRY,
            currency=self.CURRENCY,
            brand="Suzuki",
            model=vehicle.get('model', ''),
            edition_name=vehicle.get('edition_name', ''),
            variant=vehicle.get('slug', ''),
            fuel_type=fuel_type_from_string(vehicle.get('fuel_type', '')),
            transmission=transmission_from_string(vehicle.get('transmission', '')),
            condition=VehicleCondition.NEW,
            price_matrix=PriceMatrix(prices=vehicle.get('price_matrix', {})),
            source_url=vehicle.get('source_url'),
        )

    def scrape_all(self, model: Optional[str] = None, **kwargs) -> List[LeaseOffer]:
        """Scrape all Suzuki offers."""
        try:
            vehicles = self.discover_vehicles()

            if model:
                model_lower = model.lower()
                vehicles = [v for v in vehicles if model_lower in v.get('model', '').lower()]

            offers = []
            for vehicle in vehicles:
                offer = self.scrape_vehicle_prices(vehicle)
                if offer:
                    offers.append(offer)

            return offers
        finally:
            self.close()

    def scrape_model(self, model_name: str) -> List[LeaseOffer]:
        """Scrape a specific model."""
        return self.scrape_all(model=model_name)
