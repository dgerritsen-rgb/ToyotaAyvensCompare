#!/usr/bin/env python3
"""
Toyota.nl Private Lease Scraper

Scrapes all Toyota private lease editions and extracts the full price matrix
for each edition across all duration/mileage combinations.

Duration options: 24, 36, 48, 60, 72 months
Mileage options: 5000, 10000, 15000, 20000, 25000, 30000 km/year
"""

import re
import json
import logging
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from urllib.parse import urlencode, urlparse, parse_qs

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Price matrix dimensions
DURATIONS = [24, 36, 48, 60, 72]  # months
MILEAGES = [5000, 10000, 15000, 20000, 25000, 30000]  # km/year


@dataclass
class ToyotaEdition:
    """A specific Toyota edition/variant available for private lease."""
    model: str
    edition_name: str
    edition_slug: str  # URL identifier like "toyota-aygo-x-toyota-aygo-x-10-vvt-i-mt-play-1"
    fuel_type: str
    transmission: str
    power: Optional[str] = None
    base_url: Optional[str] = None
    price_matrix: Dict[str, float] = field(default_factory=dict)  # "duration_km" -> price

    def get_price(self, duration: int, km: int) -> Optional[float]:
        """Get price for specific duration/km combination."""
        key = f"{duration}_{km}"
        return self.price_matrix.get(key)

    def set_price(self, duration: int, km: int, price: float):
        """Set price for specific duration/km combination."""
        key = f"{duration}_{km}"
        self.price_matrix[key] = price


class ToyotaScraper:
    """Scraper for Toyota.nl private lease offerings."""

    BASE_URL = "https://www.toyota.nl"
    OVERVIEW_URL = "https://www.toyota.nl/private-lease/modellen"
    REQUEST_DELAY = 2.0  # seconds between requests

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._driver: Optional[webdriver.Chrome] = None
        self._last_request_time: float = 0

    @property
    def driver(self) -> webdriver.Chrome:
        """Lazy initialization of Selenium WebDriver."""
        if self._driver is None:
            options = Options()
            if self.headless:
                options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--window-size=1920,1080')
            options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')

            service = Service(ChromeDriverManager().install())
            self._driver = webdriver.Chrome(service=service, options=options)
        return self._driver

    def close(self):
        """Clean up resources."""
        if self._driver:
            self._driver.quit()
            self._driver = None

    def _rate_limit(self):
        """Ensure minimum delay between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.REQUEST_DELAY:
            time.sleep(self.REQUEST_DELAY - elapsed)
        self._last_request_time = time.time()

    def _wait_for_page_load(self, timeout: int = 15):
        """Wait for page to be fully loaded."""
        try:
            WebDriverWait(self.driver, timeout).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            time.sleep(2)  # Extra wait for JS rendering
        except TimeoutException:
            logger.warning("Page load timeout, proceeding anyway")

    def _accept_cookies(self):
        """Handle cookie consent banner if present."""
        try:
            cookie_selectors = [
                "#onetrust-accept-btn-handler",
                "[id*='accept']",
                "[class*='accept']",
                "button[data-testid*='cookie']",
            ]
            for selector in cookie_selectors:
                try:
                    buttons = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for btn in buttons:
                        if btn.is_displayed():
                            btn.click()
                            time.sleep(1)
                            logger.debug("Accepted cookies")
                            return
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"No cookie banner or error: {e}")

    # Known Toyota private lease models
    KNOWN_MODELS = [
        ("aygo-x", "Aygo X"),
        ("yaris", "Yaris"),
        ("yaris-cross", "Yaris Cross"),
        ("urban-cruiser", "Urban Cruiser"),
        ("corolla", "Corolla Hatchback"),
        ("corolla-touring-sports", "Corolla Touring Sports"),
        ("corolla-cross", "Corolla Cross"),
        ("c-hr", "C-HR"),
        ("rav4", "RAV4"),
        ("bz4x", "bZ4X"),
    ]

    def _discover_editions(self) -> List[ToyotaEdition]:
        """Discover all available Toyota editions by visiting each model page."""
        logger.info("Discovering Toyota editions from model pages...")

        self._rate_limit()
        self.driver.get(self.OVERVIEW_URL)
        self._wait_for_page_load()
        self._accept_cookies()

        all_editions = []

        for model_slug, model_name in self.KNOWN_MODELS:
            logger.info(f"Checking model: {model_name}")
            editions = self._discover_editions_for_model(model_slug, model_name)
            all_editions.extend(editions)
            logger.info(f"  Found {len(editions)} editions for {model_name}")

        logger.info(f"Total editions discovered: {len(all_editions)}")
        return all_editions

    def _discover_editions_for_model(self, model_slug: str, model_name: str) -> List[ToyotaEdition]:
        """Discover editions for a specific model."""
        editions = []
        edition_slugs = set()

        # Try model-specific page
        model_url = f"{self.OVERVIEW_URL}/{model_slug}"
        self._rate_limit()
        self.driver.get(model_url)
        self._wait_for_page_load()
        time.sleep(2)

        # Look for edition links in page source
        page_source = self.driver.page_source
        soup = BeautifulSoup(page_source, 'lxml')

        # Find edition slugs in various places
        # 1. In href attributes
        for link in soup.find_all('a', href=True):
            href = link.get('href', '')
            match = re.search(r'#/edition/([^/\?]+)', href)
            if match:
                edition_slugs.add(match.group(1))

        # 2. In script tags (JSON data)
        for script in soup.find_all('script'):
            if script.string:
                # Look for edition patterns
                matches = re.findall(r'"editionId"\s*:\s*"([^"]+)"', script.string)
                edition_slugs.update(matches)

                # Also look for URL patterns
                matches = re.findall(r'/edition/([a-z0-9-]+)', script.string)
                edition_slugs.update(matches)

        # 3. Check current URL after page load (may have auto-navigated)
        current_url = self.driver.current_url
        match = re.search(r'#/edition/([^/\?]+)', current_url)
        if match:
            edition_slugs.add(match.group(1))

        # Filter to valid edition slugs (should contain model name)
        model_key = model_slug.replace('-', '')
        valid_slugs = [s for s in edition_slugs if model_key in s.replace('-', '').lower()
                       or model_slug in s.lower()]

        # If no valid slugs found, create a default one
        if not valid_slugs:
            logger.debug(f"No edition slugs found for {model_name}, trying default pattern")
            # Try the default edition pattern
            default_slug = f"toyota-{model_slug}-toyota-{model_slug}-default"
            valid_slugs = [default_slug]

        for slug in valid_slugs:
            edition = self._parse_edition_from_slug(slug, model_name)
            if edition:
                editions.append(edition)

        return editions

    def _parse_edition_from_slug(self, slug: str, model_name: Optional[str] = None) -> Optional[ToyotaEdition]:
        """Parse edition information from URL slug."""
        # Example slug: toyota-aygo-x-toyota-aygo-x-10-vvt-i-mt-play-1
        # Format: toyota-{model}-toyota-{model}-{engine}-{transmission}-{trim}-{version}

        clean_slug = slug.lower()
        parts = clean_slug.split('-')

        # Use provided model name or detect from slug
        detected_model = model_name
        if not detected_model:
            models = ['aygo-x', 'yaris-cross', 'yaris', 'urban-cruiser', 'corolla-cross',
                      'corolla', 'c-hr', 'rav4', 'bz4x', 'land-cruiser', 'hilux', 'proace']
            for model in models:
                if model in clean_slug:
                    detected_model = model.replace('-', ' ').title()
                    break
            if not detected_model:
                detected_model = parts[1] if len(parts) > 1 else "Unknown"

        # Detect fuel type
        fuel_type = "Hybrid"  # Toyota default
        if 'bz4x' in clean_slug or 'electric' in clean_slug or 'ev' in clean_slug:
            fuel_type = "Electric"
        elif 'phev' in clean_slug or 'plug-in' in clean_slug:
            fuel_type = "Plug-in Hybrid"

        # Detect transmission
        transmission = "Automatic"  # Toyota default (most hybrids are CVT)
        if 'mt' in parts or 'manual' in clean_slug:
            transmission = "Manual"

        # Create edition name from slug (more readable)
        edition_name = slug.replace('-', ' ').title()

        return ToyotaEdition(
            model=detected_model,
            edition_name=edition_name,
            edition_slug=slug,
            fuel_type=fuel_type,
            transmission=transmission,
            base_url=f"{self.OVERVIEW_URL}#/edition/{slug}/configurator"
        )

    def _build_configurator_url(self, slug: str, duration: int, km: int) -> str:
        """Build configurator URL with specific duration and mileage."""
        base = f"{self.OVERVIEW_URL}#/edition/{slug}/configurator"
        params = f"?durationMonths={duration}&yearlyKilometers={km}"
        return base + params

    def _extract_price_from_page(self) -> Optional[float]:
        """Extract the monthly price from the current configurator page."""
        soup = BeautifulSoup(self.driver.page_source, 'lxml')

        # Look for price patterns
        price_patterns = [
            r'€\s*(\d+)[,.]?(\d{2})?\s*(?:p\.?\s*m\.?|per\s*maand|/\s*maand)',
            r'(\d+)[,.](\d{2})\s*(?:p\.?\s*m\.?|per\s*maand)',
            r'maandbedrag[:\s]*€?\s*(\d+)',
        ]

        text = soup.get_text()

        for pattern in price_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    if isinstance(match, tuple):
                        whole = int(match[0])
                        cents = int(match[1]) if len(match) > 1 and match[1] else 0
                        price = whole + cents / 100
                    else:
                        price = float(match)

                    # Validate reasonable price range
                    if 150 <= price <= 2000:
                        return price
                except (ValueError, TypeError):
                    continue

        # Try finding price elements directly
        price_selectors = [
            '[class*="price"]',
            '[class*="monthly"]',
            '[data-testid*="price"]',
        ]

        for selector in price_selectors:
            elements = soup.select(selector)
            for elem in elements:
                elem_text = elem.get_text()
                match = re.search(r'€?\s*(\d+)[,.]?(\d{2})?', elem_text)
                if match:
                    try:
                        whole = int(match.group(1))
                        cents = int(match.group(2)) if match.group(2) else 0
                        price = whole + cents / 100
                        if 150 <= price <= 2000:
                            return price
                    except (ValueError, TypeError):
                        continue

        return None

    def _scrape_price_for_combination(self, slug: str, duration: int, km: int) -> Optional[float]:
        """Scrape price for a specific duration/km combination."""
        url = self._build_configurator_url(slug, duration, km)

        self._rate_limit()
        self.driver.get(url)
        self._wait_for_page_load()

        # Wait for price to potentially update
        time.sleep(1)

        price = self._extract_price_from_page()

        if price:
            logger.debug(f"  {duration}mo/{km}km: €{price}/mo")
        else:
            logger.debug(f"  {duration}mo/{km}km: No price found")

        return price

    def scrape_edition_prices(self, edition: ToyotaEdition) -> ToyotaEdition:
        """Scrape the full price matrix for an edition."""
        logger.info(f"Scraping prices for: {edition.model} - {edition.edition_name}")

        for duration in DURATIONS:
            for km in MILEAGES:
                price = self._scrape_price_for_combination(
                    edition.edition_slug, duration, km
                )
                if price:
                    edition.set_price(duration, km, price)

        prices_found = len(edition.price_matrix)
        total_combinations = len(DURATIONS) * len(MILEAGES)
        logger.info(f"  Found {prices_found}/{total_combinations} prices")

        return edition

    def scrape_all(self) -> List[ToyotaEdition]:
        """Scrape all Toyota editions with full price matrices."""
        logger.info("Starting Toyota.nl private lease scrape")

        try:
            editions = self._discover_editions()

            if not editions:
                logger.warning("No editions discovered, trying direct URL approach")
                editions = self._try_direct_models()

            results = []
            for i, edition in enumerate(editions):
                logger.info(f"Processing edition {i+1}/{len(editions)}: {edition.model}")
                scraped = self.scrape_edition_prices(edition)
                if scraped.price_matrix:
                    results.append(scraped)

            logger.info(f"Completed scraping {len(results)} editions with prices")
            return results

        finally:
            self.close()

    def _try_direct_models(self) -> List[ToyotaEdition]:
        """Try accessing known model pages directly."""
        known_models = [
            "aygo-x", "yaris", "yaris-cross", "urban-cruiser",
            "corolla", "corolla-touring-sports", "corolla-cross",
            "c-hr", "rav4", "bz4x"
        ]

        editions = []

        for model in known_models:
            try:
                url = f"{self.OVERVIEW_URL}/{model}"
                self._rate_limit()
                self.driver.get(url)
                self._wait_for_page_load()

                # Look for edition selector on model page
                soup = BeautifulSoup(self.driver.page_source, 'lxml')

                # Find edition links
                for link in soup.find_all('a', href=True):
                    href = link.get('href', '')
                    if '#/edition/' in href:
                        match = re.search(r'#/edition/([^/]+)', href)
                        if match:
                            slug = match.group(1)
                            edition = self._parse_edition_from_slug(slug)
                            if edition and edition not in editions:
                                editions.append(edition)

            except Exception as e:
                logger.debug(f"Error trying model {model}: {e}")
                continue

        return editions


def main():
    """Main entry point."""
    scraper = ToyotaScraper(headless=True)

    try:
        editions = scraper.scrape_all()

        # Print summary
        print("\n" + "="*60)
        print("Toyota Private Lease Price Matrix")
        print("="*60)

        for edition in editions:
            print(f"\n{edition.model} - {edition.edition_name}")
            print(f"  Fuel: {edition.fuel_type}, Trans: {edition.transmission}")
            print(f"  Prices found: {len(edition.price_matrix)}")

            if edition.price_matrix:
                # Show sample prices
                for duration in DURATIONS[:3]:
                    for km in MILEAGES[:2]:
                        price = edition.get_price(duration, km)
                        if price:
                            print(f"    {duration}mo/{km}km: €{price}/mo")

        # Save to JSON
        output = []
        for edition in editions:
            output.append(asdict(edition))

        with open("output/toyota_prices.json", "w") as f:
            json.dump(output, f, indent=2)

        print(f"\nSaved {len(editions)} editions to output/toyota_prices.json")

    finally:
        scraper.close()


if __name__ == "__main__":
    main()
