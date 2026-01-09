"""
Toyota.nl scraper using the new framework.

This is a proper implementation using the framework's base classes,
BrowserManager, and configuration system.
"""

import re
import time
import logging
from typing import Dict, List, Optional, Any
from dataclasses import asdict

from bs4 import BeautifulSoup
from tqdm import tqdm

from src.core.base_scraper import MultiModelScraper
from src.core.browser import BrowserManager
from src.core.schema import (
    LeaseOffer,
    Provider,
    Country,
    Currency,
    PriceMatrix,
    fuel_type_from_string,
    transmission_from_string,
    VehicleCondition,
)
from src.core.config import get_provider_config
from src.core.registry import register_scraper

logger = logging.getLogger(__name__)


@register_scraper(Provider.TOYOTA_NL)
class ToyotaNLScraper(MultiModelScraper):
    """
    Toyota Netherlands scraper using the new framework.

    Uses BrowserManager for browser operations and outputs LeaseOffer objects.
    """

    PROVIDER = Provider.TOYOTA_NL
    COUNTRY = Country.NL
    CURRENCY = Currency.EUR
    BASE_URL = "https://www.toyota.nl"
    REQUEST_DELAY = 2.0

    # Toyota models available for private lease
    KNOWN_MODELS = {
        "aygo-x": "Aygo X",
        "yaris": "Yaris",
        "yaris-cross": "Yaris Cross",
        "corolla-hatchback": "Corolla Hatchback",
        "corolla-touring-sports": "Corolla Touring Sports",
        "corolla-cross": "Corolla Cross",
        "c-hr": "C-HR",
        "rav4": "RAV4",
        "bz4x": "bZ4X",
    }

    # Known edition/trim names
    KNOWN_EDITIONS = [
        'Active', 'Comfort', 'Dynamic', 'Executive', 'GR-Sport', 'GR Sport',
        'Style', 'First Edition', 'Premium', 'Lounge', 'Adventure', 'Team D',
        'Play', 'Limited', 'Pulse', 'Pure', 'Flow', 'Beyond', 'Trek', 'Envy',
        'Edition 1', 'Edition1', 'JBL'
    ]

    def __init__(self, headless: bool = True):
        super().__init__(headless=headless)
        self._config = get_provider_config('toyota_nl')

    @property
    def overview_url(self) -> str:
        return f"{self.BASE_URL}/private-lease/modellen"

    def discover_models(self) -> List[Dict[str, Any]]:
        """Discover available models."""
        return [
            {"slug": slug, "name": name}
            for slug, name in self.KNOWN_MODELS.items()
        ]

    def discover_model_editions(self, model: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Discover editions for a specific model by visiting the model page."""
        model_slug = model["slug"]
        model_name = model["name"]

        model_url = f"{self.overview_url}/{model_slug}"
        self.browser.get(model_url)
        self.browser.handle_cookie_consent()
        time.sleep(2)

        editions = []
        edition_slugs = self._find_edition_slugs(model_slug)

        for slug in edition_slugs:
            edition_info = self._parse_edition_slug(slug, model_name)
            if edition_info:
                editions.append(edition_info)

        return editions

    def _find_edition_slugs(self, model_slug: str) -> List[str]:
        """Find edition slugs from page source."""
        soup = BeautifulSoup(self.browser.page_source, 'lxml')
        edition_slugs = set()

        # Find in href attributes
        for link in soup.find_all('a', href=True):
            href = link.get('href', '')
            match = re.search(r'#/edition/([^/\?]+)', href)
            if match:
                edition_slugs.add(match.group(1))

        # Find in script tags
        for script in soup.find_all('script'):
            if script.string:
                matches = re.findall(r'"editionId"\s*:\s*"([^"]+)"', script.string)
                edition_slugs.update(matches)
                matches = re.findall(r'/edition/([a-z0-9-]+)', script.string)
                edition_slugs.update(matches)

        # Check current URL
        match = re.search(r'#/edition/([^/\?]+)', self.browser.current_url)
        if match:
            edition_slugs.add(match.group(1))

        # Filter to valid slugs for this model
        model_key = model_slug.replace('-', '')
        valid_slugs = [
            s for s in edition_slugs
            if model_key in s.replace('-', '').lower() or model_slug in s.lower()
        ]

        return list(valid_slugs)

    def _parse_edition_slug(self, slug: str, model_name: str) -> Optional[Dict[str, Any]]:
        """Parse edition information from URL slug."""
        clean_slug = slug.lower()
        parts = clean_slug.split('-')

        # Detect fuel type
        fuel_type = "Hybrid"
        if 'bz4x' in clean_slug or 'electric' in clean_slug:
            fuel_type = "Electric"
        elif 'phev' in clean_slug or 'plug-in' in clean_slug:
            fuel_type = "Plug-in Hybrid"

        # Detect transmission
        transmission = "Automatic"
        if 'mt' in parts or 'manual' in clean_slug:
            transmission = "Manual"

        # Extract edition name
        edition_name = self._extract_edition_name(slug)

        return {
            "model": model_name,
            "slug": slug,
            "edition_name": edition_name,
            "fuel_type": fuel_type,
            "transmission": transmission,
        }

    def _extract_edition_name(self, slug: str) -> str:
        """Extract clean edition name from slug."""
        slug_lower = slug.lower()

        # Check known editions
        for edition in self.KNOWN_EDITIONS:
            if edition.lower().replace('-', '').replace(' ', '') in slug_lower.replace('-', ''):
                if edition.lower() == 'gr sport':
                    return 'GR-Sport'
                return edition

        # Fallback: use last part of slug
        parts = slug.split('-')
        if len(parts) > 3:
            return parts[-1].title()
        return "Standard"

    def discover_vehicles(self) -> List[Dict[str, Any]]:
        """
        Discover all vehicles using the model page approach.

        This overrides the parent method to use a more efficient approach
        that scrapes directly from model pages with price matrices.
        """
        logger.info("Discovering Toyota editions from model pages...")
        all_vehicles = []

        self.browser.get(self.overview_url)
        self.browser.handle_cookie_consent()

        for model_slug, model_name in self.KNOWN_MODELS.items():
            logger.info(f"Processing model: {model_name}")

            # Get editions and prices from model page
            filter_url = f"{self.overview_url}#?model[]={model_slug}&durationMonths=72&yearlyKilometers=5000"
            editions = self._scrape_model_page(model_slug, model_name, filter_url)

            for edition in editions:
                all_vehicles.append(edition)

            logger.info(f"  Found {len(editions)} editions for {model_name}")

        logger.info(f"Total vehicles discovered: {len(all_vehicles)}")
        return all_vehicles

    def _scrape_model_page(
        self,
        model_slug: str,
        model_name: str,
        filter_url: str
    ) -> List[Dict[str, Any]]:
        """Scrape editions and prices from a model page."""
        editions = []

        # First visit the model page to discover edition slugs by clicking cards
        model_url = f"{self.overview_url}/{model_slug}"
        logger.info(f"Discovering edition URLs from: {model_url}")
        self.browser.get(model_url)
        self.browser.handle_cookie_consent()
        time.sleep(3)  # Extra wait for JS to load

        # Discover editions by clicking cards
        edition_data = self._discover_edition_slugs_by_clicking(model_name, model_url)

        if not edition_data:
            logger.warning(f"No editions found for {model_name}")
            return editions

        logger.info(f"  Discovered {len(edition_data)} editions")

        # Extract slugs and names
        edition_slugs = [slug for _, slug, _ in edition_data]
        edition_names = {i: name for i, (name, _, _) in enumerate(edition_data)}

        # Navigate to filter URL and scrape prices
        logger.info(f"Scraping prices from model page: {filter_url}")
        self.browser.get(filter_url)
        time.sleep(2)

        # Scrape full price matrix using dropdown manipulation
        edition_prices = self._scrape_all_prices_with_dropdowns(model_name, len(edition_data))

        # Create edition dictionaries
        for idx, (edition_name, slug, _) in enumerate(edition_data):
            edition_info = self._parse_edition_slug(slug, model_name)
            if edition_info:
                # Override edition name with the one from the card
                edition_info['edition_name'] = edition_name
                if idx in edition_prices and edition_prices[idx]:
                    edition_info['price_matrix'] = edition_prices[idx]
                    edition_info['source_url'] = f"{self.overview_url}#/edition/{slug}/configurator?durationMonths=72&yearlyKilometers=5000"
                    editions.append(edition_info)

        return editions

    def _discover_edition_slugs_by_clicking(self, model_name: str, model_url: str) -> List[tuple]:
        """
        Discover edition slugs by clicking edition cards.

        Returns list of tuples: (edition_name, slug, full_url)
        """
        from selenium.webdriver.common.by import By

        edition_data = []

        # Find edition cards using the same XPATH as the legacy scraper
        # This looks for h4 elements with data-testid="edition-name" and their parent card
        edition_cards = self.browser.driver.find_elements(
            By.XPATH,
            '//h4[@data-testid="edition-name"]/ancestor::*[contains(@class, "card") or @role="button"][1]'
        )
        num_cards = len(edition_cards)
        logger.info(f"  Found {num_cards} edition cards to click")

        # Click each card to discover its URL (re-finding after each click due to DOM changes)
        for i in range(num_cards):
            try:
                # Re-find cards fresh each iteration (DOM changes after navigation)
                edition_cards = self.browser.driver.find_elements(
                    By.XPATH,
                    '//h4[@data-testid="edition-name"]/ancestor::*[contains(@class, "card") or @role="button"][1]'
                )
                if i >= len(edition_cards):
                    break

                card = edition_cards[i]

                # Get edition name before clicking
                try:
                    edition_name_elem = card.find_element(By.CSS_SELECTOR, '[data-testid="edition-name"]')
                    edition_name = edition_name_elem.text.strip()
                except:
                    edition_name = f"Edition {i+1}"

                # Scroll to card and click
                self.browser.driver.execute_script('arguments[0].scrollIntoView(true);', card)
                time.sleep(0.5)
                card.click()
                time.sleep(1.5)

                # Get URL after click
                current_url = self.browser.current_url

                # Extract slug from URL
                slug_match = re.search(r'#/edition/([^/]+)/configurator', current_url)
                if slug_match:
                    slug = slug_match.group(1)
                    edition_data.append((edition_name, slug, current_url))
                    logger.info(f"    Edition {i+1}: {edition_name} -> {slug}")

                # Navigate back to model page for next card
                self.browser.get(model_url)
                time.sleep(2)

            except Exception as e:
                logger.debug(f"    Error clicking card {i}: {e}")
                # Try to get back to model page if something went wrong
                try:
                    self.browser.get(model_url)
                    time.sleep(2)
                except:
                    pass
                continue

        return edition_data

    def _scrape_all_prices_with_dropdowns(
        self,
        model_name: str,
        num_editions: int
    ) -> Dict[int, Dict[str, float]]:
        """Scrape full price matrix for all editions using dropdown manipulation."""
        from selenium.webdriver.common.by import By

        edition_prices = {i: {} for i in range(num_editions)}

        durations = self._config.price_matrix.durations if self._config else [24, 36, 48, 60, 72]
        mileages = self._config.price_matrix.mileages if self._config else [5000, 10000, 15000, 20000, 25000, 30000]

        combos = [(d, k) for d in durations for k in mileages]

        with tqdm(combos, desc=f"Toyota | {model_name.lower()}", unit="combo",
                  bar_format='{desc} {n_fmt}/{total_fmt} {bar} [{elapsed}<{remaining}]') as pbar:
            for duration, km in pbar:
                pbar.set_description(f"Toyota | {model_name.lower()} | {duration}mo/{km:,}km")

                # Set the dropdowns using Selenium
                if not self._set_duration_km_dropdowns(duration, km):
                    # Fallback: try URL parameter approach
                    self.browser.execute_script(f"""
                        var url = new URL(window.location.href);
                        url.searchParams.set('durationMonths', '{duration}');
                        url.searchParams.set('yearlyKilometers', '{km}');
                        history.pushState(null, '', url.toString());
                        window.dispatchEvent(new Event('popstate'));
                    """)
                    time.sleep(1.5)

                # Extract prices
                prices = self._extract_current_prices(num_editions)
                for idx, price in prices.items():
                    if price:
                        key = f"{duration}_{km}"
                        edition_prices[idx][key] = price

        return edition_prices

    def _set_duration_km_dropdowns(self, duration: int, km: int) -> bool:
        """Set the duration and km dropdowns using Selenium."""
        from selenium.webdriver.common.by import By

        try:
            # Find all MUI NativeSelect elements
            selects = self.browser.driver.find_elements(By.CSS_SELECTOR, "select.MuiNativeSelect-select")

            duration_set = False
            km_set = False

            for select in selects:
                try:
                    # Get current value to determine which dropdown this is
                    options = select.find_elements(By.TAG_NAME, "option")
                    option_texts = [opt.text for opt in options]

                    # Check if this is duration dropdown (contains "maanden")
                    if any('maanden' in t or 'maand' in t for t in option_texts):
                        # Find the option matching our duration
                        for opt in options:
                            if str(duration) in opt.text:
                                opt.click()
                                duration_set = True
                                break

                    # Check if this is km dropdown (contains "km")
                    elif any('km' in t.lower() for t in option_texts):
                        # Find the option matching our km
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
                time.sleep(1)  # Wait for price update

            return duration_set and km_set

        except Exception as e:
            logger.debug(f"Error setting dropdowns: {e}")
            return False

    def _extract_current_prices(self, num_editions: int) -> Dict[int, Optional[float]]:
        """Extract monthly lease prices for all editions from current page state."""
        prices = {}
        soup = BeautifulSoup(self.browser.page_source, 'lxml')

        # Find price elements with data-testid="price" - these are the lease prices
        # Need to filter out catalog prices (which are much higher, like €23.750)
        price_elements = soup.select('[data-testid*="price"]')

        lease_prices = []
        for elem in price_elements:
            price_text = elem.get_text(strip=True)
            # Look for monthly lease prices (typically €300-€2000 range)
            match = re.search(r'€\s*(\d+)', price_text)
            if match:
                price = float(match.group(1))
                # Filter to reasonable monthly lease prices
                if 150 <= price <= 2000:
                    lease_prices.append(price)

        # Assign prices to editions
        for idx in range(num_editions):
            if idx < len(lease_prices):
                prices[idx] = lease_prices[idx]

        return prices

    def scrape_vehicle_prices(self, vehicle: Dict[str, Any]) -> Optional[LeaseOffer]:
        """Convert vehicle dict to LeaseOffer - prices already included."""
        if not vehicle.get('price_matrix'):
            return None

        return LeaseOffer(
            provider=self.PROVIDER,
            country=self.COUNTRY,
            currency=self.CURRENCY,
            brand="Toyota",
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
        """Scrape all Toyota offers."""
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
