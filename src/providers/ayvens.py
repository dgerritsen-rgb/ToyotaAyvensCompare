"""
Ayvens.com scraper using the new framework.

This is a proper implementation using the framework's base classes,
BrowserManager, and configuration system.
"""

import re
import time
import logging
from typing import Dict, List, Optional, Any, Tuple

from bs4 import BeautifulSoup
from tqdm import tqdm
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys

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


@register_scraper(Provider.AYVENS_NL)
class AyvensNLScraper(MultiBrandScraper):
    """
    Ayvens Netherlands scraper using the new framework.

    Uses BrowserManager for browser operations and outputs LeaseOffer objects.
    Handles multiple brands with slider-based price configuration.
    """

    PROVIDER = Provider.AYVENS_NL
    COUNTRY = Country.NL
    CURRENCY = Currency.EUR
    BASE_URL = "https://www.ayvens.com"
    REQUEST_DELAY = 1.5

    # BTO (Build-to-Order) variant URLs by brand
    BTO_VARIANT_URLS = {
        "Toyota": [
            "https://www.ayvens.com/nl-nl/private-lease-showroom/model/toyota/yaris-cross/suv/",
            "https://www.ayvens.com/nl-nl/private-lease-showroom/model/toyota/corolla-touring-sports/stationwagon/",
        ],
        "Suzuki": [
            "https://www.ayvens.com/nl-nl/private-lease-showroom/model/suzuki/swift/hatchback/",
            "https://www.ayvens.com/nl-nl/private-lease-showroom/model/suzuki/vitara/suv/",
            "https://www.ayvens.com/nl-nl/private-lease-showroom/model/suzuki/s-cross/suv/",
            "https://www.ayvens.com/nl-nl/private-lease-showroom/model/suzuki/swace/stationwagon/",
            "https://www.ayvens.com/nl-nl/private-lease-showroom/model/suzuki/across/suv/",
        ],
    }

    def __init__(self, headless: bool = True, brand: Optional[str] = None):
        super().__init__(headless=headless, brand=brand)
        self._config = get_provider_config('ayvens_nl')

    def discover_brands(self) -> List[str]:
        """Return supported brands."""
        return list(self.BTO_VARIANT_URLS.keys())

    def discover_brand_vehicles(self, brand: str) -> List[Dict[str, Any]]:
        """Discover all vehicles for a specific brand."""
        brand_title = brand.title()
        brand_lower = brand.lower()
        vehicles = []

        logger.info(f"Discovering {brand_title} vehicles from Ayvens showroom...")

        variant_urls = self.BTO_VARIANT_URLS.get(brand_title, [])
        if not variant_urls:
            logger.warning(f"No variant URLs for {brand_title}")
            return []

        for variant_url in variant_urls:
            logger.info(f"  Checking variant page: {variant_url}")
            self.browser.get(variant_url)
            self.browser.handle_cookie_consent()
            time.sleep(2)

            # Scroll to load all vehicles
            for _ in range(3):
                self.browser.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(0.5)

            soup = BeautifulSoup(self.browser.page_source, 'lxml')
            all_links = soup.find_all('a', href=True)

            for link in all_links:
                href = link.get('href', '')

                # Look for vehicle detail page links
                # Pattern: /private-lease-showroom/onze-autos/{id}/{brand}-{model}
                pattern = rf'/private-lease-showroom/onze-autos/(\d+)/{brand_lower}-([^/]+)'
                match = re.search(pattern, href)
                if match:
                    vehicle_id = match.group(1)
                    model_slug = match.group(2)

                    if href.startswith('/'):
                        full_url = self.BASE_URL + href
                    else:
                        full_url = href

                    # Parse model name from URL
                    model_name = model_slug.replace('-', ' ').title()

                    # Get link text for variant info
                    link_text = link.get_text(' ', strip=True)

                    # Get parent context
                    parent = link.find_parent(['div', 'article'])
                    parent_text = parent.get_text(' ', strip=True) if parent else ""

                    # Detect fuel type
                    fuel_type = self._detect_fuel_type(link_text, parent_text, model_name)

                    # Extract variant
                    variant = self._extract_variant(link_text)

                    # Check if new or used
                    is_new = not self._is_used_car(variant)

                    vehicles.append({
                        'vehicle_id': vehicle_id,
                        'model': model_name,
                        'model_slug': model_slug,
                        'variant': variant,
                        'url': full_url,
                        'fuel_type': fuel_type,
                        'brand': brand_title,
                        'is_new': is_new,
                    })

        # Deduplicate by vehicle_id
        seen_ids = set()
        unique_vehicles = []
        for v in vehicles:
            if v['vehicle_id'] not in seen_ids:
                seen_ids.add(v['vehicle_id'])
                unique_vehicles.append(v)

        logger.info(f"  Discovered {len(unique_vehicles)} unique {brand_title} vehicles")
        return unique_vehicles

    def _detect_fuel_type(self, link_text: str, parent_text: str, model_name: str) -> str:
        """Detect fuel type from context."""
        context = (link_text + " " + parent_text + " " + model_name).lower()
        if any(x in context for x in ['elektrisch', 'electric', 'ev', 'bz4x', 'e-vitara']):
            return "Electric"
        elif 'hybrid' in context:
            return "Hybrid"
        elif 'benzine' in context or 'petrol' in context:
            return "Petrol"
        return "Hybrid"

    def _extract_variant(self, link_text: str) -> str:
        """Extract variant from link text."""
        if link_text:
            variant_match = re.search(r'([\d.]+\s*(?:Hybrid|Electric)?.*?)(?:\d+d)?$', link_text, re.IGNORECASE)
            if variant_match:
                return variant_match.group(1).strip()
        return ""

    def _is_used_car(self, variant: str) -> bool:
        """Check if vehicle is used based on variant text."""
        variant_lower = variant.lower()
        used_indicators = ['kilometerstand', '1e tenaamstelling', 'bouwjaar', 'km ']
        return any(ind in variant_lower for ind in used_indicators)

    def _extract_edition_name(self, variant: str) -> str:
        """Extract clean edition name from variant."""
        edition_patterns = [
            r'\b(Active|Comfort|Dynamic|Executive|GR[- ]?Sport|Style|First|Edition|Premium|Lounge|Select|Select Pro|AllGrip)\b',
        ]

        for pattern in edition_patterns:
            match = re.search(pattern, variant, re.IGNORECASE)
            if match:
                edition = match.group(1).strip()
                if edition.upper().startswith('GR'):
                    return 'GR-Sport'
                return edition.title()

        return ""

    def scrape_vehicle_prices(self, vehicle: Dict[str, Any]) -> Optional[LeaseOffer]:
        """Scrape prices for a vehicle using slider manipulation."""
        url = vehicle.get('url')
        if not url:
            return None

        logger.info(f"  Scraping: {vehicle.get('brand')} {vehicle.get('model')} - {vehicle.get('variant', '')[:30]}")

        self.browser.get(url)
        self.browser.handle_cookie_consent()
        time.sleep(2)

        # Check if page has configurable sliders
        if not self._has_configurable_sliders():
            logger.warning(f"    No configurable sliders found")
            return None

        # Scrape price matrix
        price_matrix = self._scrape_price_matrix(vehicle)

        if not price_matrix:
            return None

        # Extract edition name
        edition_name = self._extract_edition_name(vehicle.get('variant', ''))

        return LeaseOffer(
            provider=self.PROVIDER,
            country=self.COUNTRY,
            currency=self.CURRENCY,
            brand=vehicle.get('brand', ''),
            model=vehicle.get('model', ''),
            edition_name=edition_name,
            variant=vehicle.get('variant', ''),
            fuel_type=fuel_type_from_string(vehicle.get('fuel_type', '')),
            transmission=Transmission.AUTOMATIC,
            condition=VehicleCondition.NEW if vehicle.get('is_new', True) else VehicleCondition.USED,
            price_matrix=PriceMatrix(prices=price_matrix),
            source_url=url,
            vehicle_id=vehicle.get('vehicle_id'),
        )

    def _has_configurable_sliders(self) -> bool:
        """Check if page has configurable duration/mileage sliders."""
        from selenium.webdriver.common.by import By

        try:
            sliders = self.browser.driver.find_elements(By.CSS_SELECTOR, "[role='slider']")
            duration_slider = False
            mileage_slider = False

            for slider in sliders:
                try:
                    min_val = int(slider.get_attribute('aria-valuemin') or 0)
                    max_val = int(slider.get_attribute('aria-valuemax') or 0)

                    if min_val == 12 and max_val == 72:
                        duration_slider = True
                    elif min_val == 5000 and max_val == 30000:
                        mileage_slider = True
                except (ValueError, TypeError):
                    continue

            return duration_slider and mileage_slider
        except Exception:
            return False

    def _scrape_price_matrix(self, vehicle: Dict[str, Any]) -> Dict[str, float]:
        """Scrape all price combinations using slider manipulation."""
        price_matrix = {}

        durations = self._config.price_matrix.durations if self._config else [24, 36, 48, 60, 72]
        mileages = self._config.price_matrix.mileages if self._config else [5000, 10000, 15000, 20000, 25000, 30000]

        brand = vehicle.get('brand', 'Unknown')
        model = vehicle.get('model', 'Unknown')
        desc = f"Ayvens | {brand} | {model}"

        combos = [(d, m) for d in durations for m in mileages]

        with tqdm(combos, unit="price", leave=False,
                  bar_format='{desc} {n_fmt}/{total_fmt} {bar}') as pbar:
            for duration, mileage in pbar:
                pbar.set_description(f"{desc} | {duration}mo/{mileage:,}km")

                # Set sliders
                self._set_slider('duration', duration)
                self._set_slider('mileage', mileage)
                time.sleep(0.5)

                # Get price
                price = self._get_current_price()
                if price:
                    price_matrix[f"{duration}_{mileage}"] = price

        return price_matrix

    def _get_current_price(self) -> Optional[float]:
        """Get current displayed price."""
        try:
            soup = BeautifulSoup(self.browser.page_source, 'lxml')
            price_elem = soup.select_one('[data-testid="localized-price"]')
            if price_elem:
                price_text = price_elem.get_text(strip=True)
                match = re.search(r'€\s*(\d+)', price_text)
                if match:
                    price = float(match.group(1))
                    if 100 <= price <= 2000:
                        return price
        except Exception as e:
            logger.debug(f"Error getting price: {e}")
        return None

    def _set_slider(self, slider_type: str, target_value: int) -> bool:
        """Set slider to target value using keyboard navigation."""
        from selenium.webdriver.common.by import By

        try:
            if slider_type == 'duration':
                min_val, max_val = 12, 72
            else:  # mileage
                min_val, max_val = 5000, 30000

            sliders = self.browser.driver.find_elements(By.CSS_SELECTOR, "[role='slider']")

            for slider in sliders:
                try:
                    slider_min = int(slider.get_attribute('aria-valuemin') or 0)
                    slider_max = int(slider.get_attribute('aria-valuemax') or 0)
                    current_val = int(slider.get_attribute('aria-valuenow') or 0)

                    if slider_min == min_val and slider_max == max_val:
                        # Focus slider and reset to minimum
                        slider.click()
                        time.sleep(0.1)
                        slider.send_keys(Keys.HOME)
                        time.sleep(0.2)

                        # Calculate steps needed
                        if slider_type == 'duration':
                            # Duration steps: 12, 24, 36, 48, 60, 72 (5 steps)
                            target_index = [12, 24, 36, 48, 60, 72].index(target_value) if target_value in [12, 24, 36, 48, 60, 72] else 0
                        else:
                            # Mileage steps: 5000, 7500, 10000, 15000, 20000, 25000, 30000 (7 values)
                            mileage_values = [5000, 7500, 10000, 15000, 20000, 25000, 30000]
                            if target_value in mileage_values:
                                target_index = mileage_values.index(target_value)
                            else:
                                # Find closest
                                target_index = min(range(len(mileage_values)),
                                                   key=lambda i: abs(mileage_values[i] - target_value))

                        # Press RIGHT arrow to reach target
                        for _ in range(target_index):
                            slider.send_keys(Keys.ARROW_RIGHT)
                            time.sleep(0.05)

                        time.sleep(0.2)
                        return True

                except Exception as e:
                    logger.debug(f"Error with slider: {e}")
                    continue

        except Exception as e:
            logger.debug(f"Error setting slider: {e}")

        return False

    def scrape_all(self, brand: Optional[str] = None, **kwargs) -> List[LeaseOffer]:
        """Scrape all Ayvens offers for a brand."""
        brand = brand or self.brand_filter or "Toyota"

        try:
            logger.info(f"Starting Ayvens {brand} scrape...")
            self.brand_filter = brand

            vehicles = self.discover_brand_vehicles(brand)
            logger.info(f"Found {len(vehicles)} vehicles to scrape")

            # Filter to new vehicles only (skip used)
            new_vehicles = [v for v in vehicles if v.get('is_new', True)]
            logger.info(f"  {len(new_vehicles)} are new (build-to-order)")

            offers = []
            for vehicle in tqdm(new_vehicles, desc=f"Ayvens | {brand}", unit="vehicle"):
                offer = self.scrape_vehicle_prices(vehicle)
                if offer:
                    offers.append(offer)

            logger.info(f"Scraped {len(offers)} offers for {brand}")
            return offers

        finally:
            self.close()

    def scrape_brand(self, brand: str) -> List[LeaseOffer]:
        """Scrape specific brand from Ayvens."""
        return self.scrape_all(brand=brand)

    def scrape_model(self, model: str, brand: str = "Toyota") -> List[LeaseOffer]:
        """Scrape specific model from Ayvens."""
        offers = self.scrape_all(brand=brand)
        model_lower = model.lower()
        return [o for o in offers if model_lower in o.model.lower()]
