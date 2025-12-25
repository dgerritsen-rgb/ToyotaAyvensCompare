#!/usr/bin/env python3
"""
Suzuki.nl Private Lease Scraper

Scrapes all Suzuki private lease editions and extracts the full price matrix
for each edition across all duration/mileage combinations.

Based on Toyota scraper - the websites have similar structure.

Duration options: 24, 36, 48, 60, 72 months
Mileage options: 5000, 10000, 15000, 20000, 25000, 30000 km/year
"""

import re
import json
import logging
import time
import os
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from urllib.parse import urlencode, urlparse, parse_qs

from tqdm import tqdm
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
class SuzukiEdition:
    """A specific Suzuki edition/variant available for private lease."""
    model: str
    edition_name: str
    edition_slug: str  # URL identifier
    fuel_type: str
    transmission: str
    power: Optional[str] = None
    base_url: Optional[str] = None
    configurator_url: Optional[str] = None
    price_matrix: Dict[str, float] = field(default_factory=dict)  # "duration_km" -> price

    def get_price(self, duration: int, km: int) -> Optional[float]:
        """Get price for specific duration/km combination."""
        key = f"{duration}_{km}"
        return self.price_matrix.get(key)

    def set_price(self, duration: int, km: int, price: float):
        """Set price for specific duration/km combination."""
        key = f"{duration}_{km}"
        self.price_matrix[key] = price


class SuzukiScraper:
    """Scraper for Suzuki.nl private lease offerings."""

    BASE_URL = "https://www.suzuki.nl"
    OVERVIEW_URL = "https://www.suzuki.nl/auto/private-lease/modellen"
    MODEL_URL_BASE = "https://www.suzuki.nl/auto/private-lease"  # Model pages use this base
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
                ".cookie-accept",
                "#CybotCookiebotDialogBodyButtonAccept",
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

    # All Suzuki models available for private lease on Suzuki.nl
    # Each tuple: (model_slug, model_name)
    KNOWN_MODELS = [
        ("swift", "Swift"),
        ("vitara", "Vitara"),
        ("s-cross", "S-Cross"),
        ("swace", "Swace"),
        ("across", "Across"),
        ("e-vitara", "e VITARA"),
    ]

    # Known Suzuki edition/trim names
    KNOWN_EDITIONS = [
        'Active', 'Comfort', 'Select', 'Style', 'Select Pro',
        'Comfort+', 'Stijl', 'Two Tone', 'AllGrip',
    ]

    def _is_price_text(self, text: str) -> bool:
        """Check if text appears to be a price rather than an edition name."""
        if not text:
            return False
        price_patterns = [
            r'€',
            r'EUR',
            r'\d+,-',
            r'\d+,\d{2}',
            r'per\s*maand',
            r'p/m',
            r'incl\.?\s*btw',
            r'vanaf',
            r'^\d+$',
        ]
        for pattern in price_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def _extract_edition_name_from_element(self, elem) -> str:
        """Extract a clean edition name from an element, avoiding prices."""
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

        for edition in self.KNOWN_EDITIONS:
            if edition.lower() in text_content.lower():
                return edition

        for heading in card.find_all(['h2', 'h3', 'h4', 'h5']):
            heading_text = heading.get_text(strip=True)
            if self._is_price_text(heading_text):
                continue
            if re.match(r'^[\d\s.,]+$', heading_text):
                continue
            if len(heading_text) < 3:
                continue
            return heading_text

        for class_pattern in ['name', 'title', 'heading', 'edition', 'variant', 'trim']:
            for elem_with_class in card.select(f'[class*="{class_pattern}"]'):
                text = elem_with_class.get_text(strip=True)
                if self._is_price_text(text):
                    continue
                if len(text) >= 3 and len(text) <= 50:
                    return text

        return ""

    def _extract_prices_from_model_page(self) -> List[Dict[str, Any]]:
        """Extract all edition prices and URLs from model page cards."""
        soup = BeautifulSoup(self.driver.page_source, 'lxml')
        editions = []

        # Find price elements - try multiple selectors for Suzuki
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

        logger.debug(f"Found {len(price_elements)} price elements")

        seen_editions = set()

        for elem in price_elements:
            price_text = elem.get_text(strip=True)
            match = re.search(r'€\s*(\d+)', price_text)
            if match:
                price = float(match.group(1))
                if 150 <= price <= 2000:
                    edition_name = self._extract_edition_name_from_element(elem)

                    if edition_name and self._is_price_text(edition_name):
                        edition_name = ""

                    edition_url = None
                    card = elem
                    for _ in range(15):
                        parent = card.find_parent()
                        if not parent:
                            break
                        links = parent.find_all('a', href=True)
                        for link in links:
                            href = link.get('href', '')
                            if '#/edition/' in href or '/configurator' in href:
                                edition_url = href
                                break
                        if edition_url:
                            break
                        card = parent

                    key = edition_name if edition_name else f"edition_{len(editions)}"
                    if key in seen_editions:
                        continue
                    seen_editions.add(key)

                    editions.append({
                        'price': price,
                        'edition_name': edition_name,
                        'edition_url': edition_url
                    })

        return editions

    def _set_duration_km_dropdowns(self, duration: int, km: int) -> bool:
        """Set the duration and km dropdowns using Selenium."""
        try:
            # Find all select elements
            selects = self.driver.find_elements(By.CSS_SELECTOR, "select")

            duration_set = False
            km_set = False

            for select in selects:
                try:
                    options = select.find_elements(By.TAG_NAME, "option")
                    option_texts = [opt.text for opt in options]

                    # Check if this is duration dropdown
                    if any('maanden' in t or 'maand' in t for t in option_texts):
                        for opt in options:
                            if str(duration) in opt.text:
                                opt.click()
                                duration_set = True
                                break

                    # Check if this is km dropdown
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
                time.sleep(1)

            return duration_set and km_set

        except Exception as e:
            logger.debug(f"Error setting dropdowns: {e}")
            return False

    def _extract_price_from_page(self) -> Optional[float]:
        """Extract the monthly price from the current page."""
        soup = BeautifulSoup(self.driver.page_source, 'lxml')

        # Look for price elements
        price_selectors = [
            '[data-testid*="price"]',
            '[class*="price"]',
            '[class*="Price"]',
            '.lease-price',
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

        # Fallback: Search all text for price patterns
        text = soup.get_text()
        price_patterns = [
            r'€\s*(\d+)[,.-]*\s*(?:p\.?\s*m\.?|per\s*maand|/\s*maand)',
            r'(\d+)[,.](\d{2})\s*p/m',
        ]

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

                    if 150 <= price <= 2000:
                        return price
                except (ValueError, TypeError):
                    continue

        return None

    def _build_configurator_url(self, model_slug: str, duration: int, km: int) -> str:
        """Build configurator URL with specific duration and mileage."""
        # Suzuki URL pattern - adjust based on actual site structure
        base = f"{self.OVERVIEW_URL}/{model_slug}"
        params = f"?durationMonths={duration}&yearlyKilometers={km}"
        return base + params

    def _scrape_model_page_prices(self, model_slug: str, model_name: str) -> List[SuzukiEdition]:
        """Scrape all editions for a model by visiting the model page."""
        editions = []
        edition_prices = {}
        edition_names = {}

        model_url = f"{self.MODEL_URL_BASE}/{model_slug}/"
        logger.info(f"Scraping prices from model page: {model_url}")

        self._rate_limit()
        self.driver.get(model_url)
        self._wait_for_page_load()
        self._accept_cookies()
        time.sleep(2)

        # Get edition info from initial page load
        initial_prices = self._extract_prices_from_model_page()
        num_editions = len(initial_prices)
        logger.info(f"  Found {num_editions} editions on page")

        if num_editions == 0:
            # Try alternative approach - look for any price on page
            price = self._extract_price_from_page()
            if price:
                # Single edition model
                initial_prices = [{'price': price, 'edition_name': model_name, 'edition_url': None}]
                num_editions = 1
            else:
                return []

        # Initialize edition data
        for idx, ep in enumerate(initial_prices):
            edition_prices[idx] = {}

        # Iterate through duration/km combinations with progress bar
        total_combos = len(DURATIONS) * len(MILEAGES)
        combos = [(d, k) for d in DURATIONS for k in MILEAGES]

        desc = f"Suzuki | {model_name}"
        with tqdm(combos, desc=desc, unit="price", leave=True,
                  bar_format='{desc} {n_fmt}/{total_fmt} {bar} [{elapsed}<{remaining}]') as pbar:
            for duration, km in pbar:
                pbar.set_description(f"Suzuki | {model_name} | {duration}mo/{km:,}km", refresh=True)

                # Try setting dropdowns
                self._set_duration_km_dropdowns(duration, km)

                # Wait for prices to update
                time.sleep(0.5)

                # Extract current prices
                current_prices = self._extract_prices_from_model_page()

                if not current_prices:
                    # Try getting single price
                    price = self._extract_price_from_page()
                    if price:
                        current_prices = [{'price': price}]

                # Store prices for each edition
                for idx, ep in enumerate(current_prices):
                    if idx < num_editions:
                        edition_prices[idx][f"{duration}_{km}"] = ep['price']

        print(f"  {model_name}: Complete - {num_editions} editions")

        # Detect fuel type based on model
        def get_fuel_type(model: str) -> str:
            model_lower = model.lower()
            if 'e-vitara' in model_lower or 'e vitara' in model_lower:
                return "Electric"
            elif 'swace' in model_lower or 'across' in model_lower:
                return "Hybrid"
            else:
                return "Hybrid"  # Most Suzuki models are mild hybrid

        # Create SuzukiEdition objects
        for idx, ed_data in enumerate(initial_prices):
            edition_name = ed_data.get('edition_name', '')
            if not edition_name or self._is_price_text(edition_name):
                edition_name = f"Edition {idx+1}"

            edition_slug = f"suzuki-{model_slug}-{edition_name.lower().replace(' ', '-')}"
            configurator_url = f"{self.MODEL_URL_BASE}/{model_slug}/"

            edition = SuzukiEdition(
                model=model_name,
                edition_name=edition_name,
                edition_slug=edition_slug,
                fuel_type=get_fuel_type(model_name),
                transmission="Automatic",
                base_url=model_url,
                configurator_url=configurator_url,
                price_matrix=edition_prices.get(idx, {})
            )
            if edition.price_matrix:
                editions.append(edition)

        return editions

    def scrape_all(self, use_cache: bool = False) -> List[SuzukiEdition]:
        """Scrape all Suzuki editions with full price matrices."""
        logger.info("Starting Suzuki.nl private lease scrape")

        try:
            all_editions = []

            print("\n" + "="*60)
            print("Scraping Suzuki.nl Private Lease")
            print("="*60 + "\n")

            for model_slug, model_name in tqdm(self.KNOWN_MODELS, desc="Suzuki | Total", unit="model",
                                                bar_format='{desc} | {n_fmt}/{total_fmt} models | {bar} | Elapsed: {elapsed} | ETA: {remaining}'):
                print(f"\nProcessing: {model_name}")
                editions = self._scrape_model_page_prices(model_slug, model_name)

                if editions:
                    all_editions.extend(editions)
                    logger.info(f"  Got {len(editions)} editions for {model_name}")
                else:
                    logger.info(f"  No editions found for {model_name}")

            logger.info(f"Completed scraping {len(all_editions)} editions with prices")
            return all_editions

        finally:
            self.close()

    def get_overview_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Get lightweight metadata from overview pages for change detection."""
        from cache_manager import compute_hash

        logger.info("Fetching Suzuki overview metadata for change detection...")
        metadata = {}

        try:
            for model_slug, model_name in self.KNOWN_MODELS:
                model_url = f"{self.MODEL_URL_BASE}/{model_slug}/"
                self._rate_limit()
                self.driver.get(model_url)
                self._wait_for_page_load()

                if model_name == self.KNOWN_MODELS[0][1]:
                    self._accept_cookies()

                time.sleep(1)

                soup = BeautifulSoup(self.driver.page_source, 'lxml')

                # Find editions and prices
                editions_found = self._extract_prices_from_model_page()
                edition_names = [e.get('edition_name', '') for e in editions_found if e.get('edition_name')]
                prices = [e.get('price') for e in editions_found if e.get('price')]

                metadata[model_name] = {
                    'edition_count': len(editions_found),
                    'editions_hash': compute_hash(edition_names) if edition_names else '',
                    'cheapest_price': min(prices) if prices else None,
                    'editions': edition_names,
                }

                logger.info(f"  {model_name}: {len(editions_found)} editions, cheapest EUR{min(prices) if prices else 'N/A'}")

        except Exception as e:
            logger.error(f"Error fetching overview metadata: {e}")

        return metadata

    def scrape_model(self, model_name: str) -> List[SuzukiEdition]:
        """Scrape a single model only."""
        logger.info(f"Scraping single model: {model_name}")

        model_slug = None
        for slug, name in self.KNOWN_MODELS:
            if name.lower() == model_name.lower():
                model_slug = slug
                break

        if model_slug is None:
            logger.error(f"Unknown model: {model_name}")
            logger.info(f"Available models: {[n for _, n in self.KNOWN_MODELS]}")
            return []

        try:
            editions = self._scrape_model_page_prices(model_slug, model_name)
            return editions
        finally:
            self.close()


def save_progress(editions: List[SuzukiEdition], output_file: str = "output/suzuki_prices.json"):
    """Save current progress to JSON file."""
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    output = [asdict(e) for e in editions]
    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2)


def main():
    """Main entry point."""
    output_file = "output/suzuki_prices.json"

    scraper = SuzukiScraper(headless=True)

    try:
        editions = scraper.scrape_all()

        if editions:
            save_progress(editions, output_file)

            print("\n" + "="*60)
            print("Suzuki Private Lease Price Matrix")
            print("="*60)

            for edition in editions:
                print(f"\n{edition.model} - {edition.edition_name}")
                print(f"  Fuel: {edition.fuel_type}, Trans: {edition.transmission}")
                print(f"  Prices found: {len(edition.price_matrix)}")

                if edition.price_matrix:
                    for duration in DURATIONS[:3]:
                        for km in MILEAGES[:2]:
                            price = edition.get_price(duration, km)
                            if price:
                                print(f"    {duration}mo/{km}km: EUR{price}/mo")

            print(f"\nSaved {len(editions)} editions to {output_file}")
        else:
            print("No editions found!")

    except Exception as e:
        logger.error(f"Error: {e}")
        raise


if __name__ == "__main__":
    main()
