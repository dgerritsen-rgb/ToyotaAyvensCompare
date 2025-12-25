#!/usr/bin/env python3
"""
Leasys.com Toyota Private Lease Scraper

Scrapes Toyota vehicles from Leasys using:
1. Get Toyota vehicle list from /nl/private/toyota
2. Navigate to individual model/edition pages
3. Use dropdown interaction to get prices for all duration/mileage combinations
"""

import re
import json
import logging
import time
import os
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict

from tqdm import tqdm
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Price matrix dimensions - Leasys has fewer mileage options than Toyota/Ayvens
DURATIONS = [24, 36, 48, 60, 72]  # months
MILEAGES = [5000, 10000, 15000, 20000]  # km/year (only 4 options)


@dataclass
class LeasysOffer:
    """A Leasys Toyota lease offer."""
    model: str
    variant: str  # Edition/trim name (e.g., "Play", "Premium")
    fuel_type: str
    transmission: str
    offer_url: Optional[str] = None
    price_matrix: Dict[str, float] = field(default_factory=dict)  # "duration_km" -> price
    edition_name: str = ""  # Clean edition name for matching

    def get_price(self, duration: int, km: int) -> Optional[float]:
        """Get price for specific duration/km combination."""
        key = f"{duration}_{km}"
        return self.price_matrix.get(key)

    def set_price(self, duration: int, km: int, price: float):
        """Set price for specific duration/km combination."""
        key = f"{duration}_{km}"
        self.price_matrix[key] = price


class LeasysScraper:
    """Scraper for store.leasys.com private lease Toyota offerings."""

    BASE_URL = "https://store.leasys.com"
    TOYOTA_URL = "https://store.leasys.com/nl/private/toyota"

    # Known Toyota models on Leasys that are also on Toyota.nl
    # (URL pattern: /nl/private/brands/Toyota/{model})
    KNOWN_MODELS = [
        {"slug": "AYGO%20X", "name": "Aygo X"},
        {"slug": "Yaris", "name": "Yaris"},
        {"slug": "Corolla%20Cross", "name": "Corolla Cross"},
    ]

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
            time.sleep(2)
        except TimeoutException:
            logger.warning("Page load timeout, proceeding anyway")

    def _accept_cookies(self):
        """Handle cookie consent banner if present."""
        try:
            # Try common cookie consent button selectors
            selectors = [
                "button[id*='accept']",
                "button[class*='accept']",
                "[data-testid='cookie-accept']",
                "button:contains('Accept')",
                "button:contains('Accepteren')",
            ]
            for selector in selectors:
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

    def _parse_price(self, text: str) -> Optional[float]:
        """Extract price from text like '€341' or '341,00'."""
        if not text:
            return None
        text = text.replace('\xa0', ' ').replace(' ', '').replace('.', '')
        match = re.search(r'€?\s*(\d+)(?:[.,](\d{2}))?', text)
        if match:
            whole = int(match.group(1))
            cents = int(match.group(2)) if match.group(2) else 0
            return whole + cents / 100
        return None

    def _get_current_price(self) -> Optional[float]:
        """Get current displayed price from page."""
        try:
            soup = BeautifulSoup(self.driver.page_source, 'lxml')

            # Try specific Leasys price selectors first
            price_selectors = [
                '[class*="StyledPriceInteger"]',  # Main price integer element
                '[class*="StyledPrice"]',  # Price wrapper
                '[class*="Price__Styled"]',  # Price component
                '[class*="price"]',
                '[class*="Price"]',
            ]

            for selector in price_selectors:
                elements = soup.select(selector)
                for elem in elements:
                    text = elem.get_text(strip=True)
                    # Look for 3-digit price (typical monthly lease price range)
                    match = re.search(r'€?\s*(\d{3,4})(?:\s|$|€)', text)
                    if match:
                        price = float(match.group(1))
                        # Validate price is in reasonable range for monthly lease
                        if 200 <= price <= 1500:
                            return price

            # Fallback: search entire page for price patterns
            all_text = soup.get_text(' ', strip=True)
            # Look for prices like €341 or € 507
            prices = re.findall(r'€\s*(\d{3,4})(?:\s|$)', all_text)
            for price_str in prices:
                price = float(price_str)
                if 200 <= price <= 1500:
                    return price

        except Exception as e:
            logger.debug(f"Error getting price: {e}")
        return None

    def _discover_models(self) -> List[Dict[str, Any]]:
        """Return known Toyota models available on Leasys."""
        logger.info("Using known Toyota models from Leasys...")

        models = []
        for model_info in self.KNOWN_MODELS:
            url = f"{self.BASE_URL}/nl/private/brands/Toyota/{model_info['slug']}"
            models.append({
                'model_slug': model_info['slug'],
                'model_name': model_info['name'],
                'url': url,
            })

        logger.info(f"Found {len(models)} Toyota models: {[m['model_name'] for m in models]}")
        return models

    def _discover_editions(self, model: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Discover available editions/trims for a model."""
        logger.info(f"Discovering editions for {model['model_name']}...")
        editions = []

        try:
            self._rate_limit()
            self.driver.get(model['url'])
            self._wait_for_page_load()
            self._accept_cookies()

            soup = BeautifulSoup(self.driver.page_source, 'lxml')

            # Find edition links - pattern: /nl/private/toyota/{model-slug}/{edition}/...
            # Example: /nl/private/toyota/aygo-x/play/1-0-vvt-i-mt-m-p/pure-white/.../factory/2522?annualMileage=5000&term=72
            links = soup.find_all('a', href=True)

            # Normalize model slug for URL matching (e.g., "AYGO X" -> "aygo-x")
            model_slug_normalized = model['model_name'].lower().replace(' ', '-')

            for link in links:
                href = link.get('href', '')
                # Pattern: /nl/private/toyota/{model-slug}/{edition}/...
                pattern = rf'/nl/private/toyota/{model_slug_normalized}/([a-z0-9-]+)/'
                match = re.search(pattern, href, re.IGNORECASE)
                if match:
                    edition_slug = match.group(1).lower()

                    # Skip if it's not a new car (factory)
                    if '/factory/' not in href:
                        continue

                    # Build full URL
                    full_url = self.BASE_URL + href if href.startswith('/') else href
                    edition_name = edition_slug.replace('-', ' ').title()

                    editions.append({
                        'edition_slug': edition_slug,
                        'edition_name': edition_name,
                        'url': full_url,
                        'model_name': model['model_name'],
                        'model_slug': model_slug_normalized,
                    })

            # Deduplicate by edition_slug (keep first occurrence)
            seen = set()
            unique_editions = []
            for e in editions:
                if e['edition_slug'] not in seen:
                    seen.add(e['edition_slug'])
                    unique_editions.append(e)

            logger.info(f"  Found {len(unique_editions)} editions: {[e['edition_name'] for e in unique_editions]}")
            return unique_editions

        except Exception as e:
            logger.error(f"Error discovering editions: {e}")
            return []

    def _select_duration(self, duration: int) -> bool:
        """Select duration from dropdown."""
        try:
            # Find duration dropdown - try various selectors
            selectors = [
                "select[name*='duration']",
                "select[name*='term']",
                "select[id*='duration']",
                "select[id*='term']",
                "[data-testid*='duration'] select",
                "[data-testid*='term'] select",
            ]

            for selector in selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for elem in elements:
                        if elem.is_displayed():
                            select = Select(elem)
                            # Try to select by value or visible text
                            try:
                                select.select_by_value(str(duration))
                                time.sleep(0.5)
                                return True
                            except Exception:
                                try:
                                    select.select_by_visible_text(str(duration))
                                    time.sleep(0.5)
                                    return True
                                except Exception:
                                    continue
                except Exception:
                    continue

            # Try clicking on dropdown options directly (for custom dropdowns)
            try:
                # Click to open dropdown
                dropdown_triggers = self.driver.find_elements(By.CSS_SELECTOR,
                    "[class*='duration'], [class*='term'], [data-testid*='duration']")
                for trigger in dropdown_triggers:
                    if trigger.is_displayed():
                        trigger.click()
                        time.sleep(0.3)
                        # Find and click the option
                        options = self.driver.find_elements(By.XPATH, f"//*[text()='{duration}']")
                        for opt in options:
                            if opt.is_displayed():
                                opt.click()
                                time.sleep(0.5)
                                return True
            except Exception:
                pass

        except Exception as e:
            logger.debug(f"Error selecting duration: {e}")
        return False

    def _select_mileage(self, mileage: int) -> bool:
        """Select mileage from dropdown."""
        try:
            # Format mileage (e.g., 5000 -> "5.000" or "5000")
            mileage_str = str(mileage)
            mileage_formatted = f"{mileage:,}".replace(",", ".")

            selectors = [
                "select[name*='mileage']",
                "select[name*='kilometer']",
                "select[id*='mileage']",
                "select[id*='kilometer']",
                "[data-testid*='mileage'] select",
                "[data-testid*='kilometer'] select",
            ]

            for selector in selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for elem in elements:
                        if elem.is_displayed():
                            select = Select(elem)
                            # Try different formats
                            for val in [mileage_str, mileage_formatted]:
                                try:
                                    select.select_by_value(val)
                                    time.sleep(0.5)
                                    return True
                                except Exception:
                                    try:
                                        select.select_by_visible_text(val)
                                        time.sleep(0.5)
                                        return True
                                    except Exception:
                                        continue
                except Exception:
                    continue

        except Exception as e:
            logger.debug(f"Error selecting mileage: {e}")
        return False

    def _scrape_edition_prices(self, edition: Dict[str, Any]) -> Dict[str, float]:
        """Scrape all price combinations for an edition by modifying URL params."""
        price_matrix = {}
        base_url = edition['url']

        # Remove existing query params and rebuild
        if '?' in base_url:
            base_url = base_url.split('?')[0]

        combos = [(d, m) for d in DURATIONS for m in MILEAGES]

        try:
            with tqdm(combos, desc=f"      {edition['edition_name']}", unit="price", leave=False,
                      bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]') as pbar:
                for duration, mileage in pbar:
                    pbar.set_postfix_str(f"{duration}mo/{mileage}km")

                    # Build URL with specific duration and mileage
                    url = f"{base_url}?annualMileage={mileage}&term={duration}"

                    self._rate_limit()
                    self.driver.get(url)
                    time.sleep(2)  # Wait for page to load and price to render

                    price = self._get_current_price()
                    if price:
                        key = f"{duration}_{mileage}"
                        price_matrix[key] = price
                        logger.debug(f"      {duration}mo/{mileage}km = €{price}")

            logger.info(f"    Captured {len(price_matrix)} price points")

        except Exception as e:
            logger.error(f"Error scraping edition prices: {e}")

        return price_matrix

    def scrape_all(self) -> List[LeasysOffer]:
        """Scrape all Toyota offers with price matrices."""
        logger.info("Starting Leasys Toyota private lease scrape")

        try:
            # Discover models
            models = self._discover_models()

            if not models:
                logger.warning("No Toyota models found")
                return []

            offers = []

            for model in tqdm(models, desc="Leasys Models", unit="model",
                             bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]'):
                logger.info(f"Processing model: {model['model_name']}")

                # Discover editions for this model
                editions = self._discover_editions(model)

                if not editions:
                    logger.info(f"  No editions found for {model['model_name']}")
                    continue

                for edition in tqdm(editions, desc=f"  {model['model_name']} editions", unit="ed", leave=False,
                                   bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]'):
                    logger.info(f"  Processing edition: {edition['edition_name']}")

                    # Scrape price matrix
                    price_matrix = self._scrape_edition_prices(edition)
                    logger.info(f"    Captured {len(price_matrix)} price points")

                    # Determine fuel type (most Toyotas are hybrid)
                    fuel_type = "Hybrid"
                    model_lower = model['model_name'].lower()
                    if 'proace' in model_lower:
                        fuel_type = "Diesel"
                    elif 'bz4x' in model_lower or 'prius' in model_lower:
                        fuel_type = "Electric" if 'bz4x' in model_lower else "Hybrid"

                    offer = LeasysOffer(
                        model=model['model_name'],
                        variant=edition['edition_name'],
                        fuel_type=fuel_type,
                        transmission="Automatic",
                        offer_url=edition['url'],
                        price_matrix=price_matrix,
                        edition_name=edition['edition_name'],
                    )

                    offers.append(offer)

            logger.info(f"Completed scraping {len(offers)} Leasys Toyota offers")
            return offers

        finally:
            self.close()


def save_offers(offers: List[LeasysOffer], output_file: str = "output/leasys_toyota_prices.json"):
    """Save offers to JSON file."""
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    output = [asdict(o) for o in offers]
    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2)


def main():
    """Main entry point."""
    output_file = "output/leasys_toyota_prices.json"

    scraper = LeasysScraper(headless=True)

    try:
        offers = scraper.scrape_all()

        if offers:
            save_offers(offers, output_file)

            print("\n" + "="*60)
            print("Leasys Toyota Private Lease Offers")
            print("="*60)

            for offer in offers:
                print(f"\nToyota {offer.model} - {offer.variant}")
                print(f"  URL: {offer.offer_url}")
                print(f"  Fuel: {offer.fuel_type}")
                print(f"  Prices found: {len(offer.price_matrix)}")

                for key, price in sorted(offer.price_matrix.items()):
                    duration, km = key.split('_')
                    print(f"    {duration}mo/{km}km: €{price}/mo")

            print(f"\nSaved {len(offers)} offers to {output_file}")

    finally:
        scraper.close()


if __name__ == "__main__":
    main()
