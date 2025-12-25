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
class ToyotaEdition:
    """A specific Toyota edition/variant available for private lease."""
    model: str
    edition_name: str
    edition_slug: str  # URL identifier like "toyota-aygo-x-toyota-aygo-x-10-vvt-i-mt-play-1"
    fuel_type: str
    transmission: str
    power: Optional[str] = None
    base_url: Optional[str] = None
    configurator_url: Optional[str] = None  # Direct URL to this edition's configurator
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

    # All Toyota models available for private lease on Toyota.nl
    # Each tuple: (model_slug, model_name)
    KNOWN_MODELS = [
        ("aygo-x", "Aygo X"),
        ("yaris", "Yaris"),
        ("yaris-cross", "Yaris Cross"),
        ("corolla-hatchback", "Corolla Hatchback"),
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

    # Known Toyota edition/trim names
    KNOWN_EDITIONS = [
        'Active', 'Comfort', 'Dynamic', 'Executive', 'GR-Sport', 'GR Sport',
        'Style', 'First Edition', 'Premium', 'Lounge', 'Adventure', 'Team D',
        'Play', 'Limited', 'Pulse', 'Pure', 'Flow', 'Beyond', 'Trek'
    ]

    def _is_price_text(self, text: str) -> bool:
        """Check if text appears to be a price rather than an edition name."""
        if not text:
            return False
        # Common price patterns in Dutch
        price_patterns = [
            r'€',              # Euro symbol
            r'EUR',            # EUR text
            r'\d+,-',          # "299,-" format
            r'\d+,\d{2}',      # "299,00" format
            r'per\s*maand',    # "per maand"
            r'p/m',            # "p/m"
            r'incl\.?\s*btw',  # "incl btw"
            r'vanaf',          # "vanaf"
            r'^\d+$',          # Just a number
        ]
        for pattern in price_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def _extract_edition_name_from_element(self, elem) -> str:
        """Extract a clean edition name from an element, avoiding prices."""
        # Go up to find a card-like container
        card = elem
        for _ in range(10):  # Search up to 10 levels
            parent = card.find_parent()
            if not parent:
                break
            parent_class = ' '.join(parent.get('class', []))
            if any(k in parent_class.lower() for k in ['card', 'item', 'product', 'edition']):
                card = parent
                break
            card = parent

        # Search for edition name in the card
        text_content = card.get_text(' ', strip=True)

        # Try to find known edition names first
        for edition in self.KNOWN_EDITIONS:
            if edition.lower() in text_content.lower():
                # Return normalized edition name
                if edition.lower() == 'gr sport':
                    return 'GR-Sport'
                return edition

        # Look for h2, h3, h4 elements that don't contain price patterns
        for heading in card.find_all(['h2', 'h3', 'h4', 'h5']):
            heading_text = heading.get_text(strip=True)
            # Skip if it contains price pattern
            if self._is_price_text(heading_text):
                continue
            # Skip if it's just a number
            if re.match(r'^[\d\s.,]+$', heading_text):
                continue
            # Skip very short strings that are likely not edition names
            if len(heading_text) < 3:
                continue
            # This is likely an edition name
            return heading_text

        # Try finding text in elements with specific classes
        for class_pattern in ['name', 'title', 'heading', 'edition', 'variant', 'trim']:
            for elem_with_class in card.select(f'[class*="{class_pattern}"]'):
                text = elem_with_class.get_text(strip=True)
                # Skip price patterns
                if self._is_price_text(text):
                    continue
                if len(text) >= 3 and len(text) <= 50:
                    return text

        return ""

    def _extract_prices_from_model_page(self) -> List[Dict[str, Any]]:
        """Extract all edition prices and URLs from model page cards."""
        soup = BeautifulSoup(self.driver.page_source, 'lxml')
        editions = []

        # Find price elements with data-testid="price"
        price_elements = soup.select('[data-testid*="price"]')
        logger.debug(f"Found {len(price_elements)} price elements")

        seen_editions = set()  # Track to avoid duplicates

        for elem in price_elements:
            price_text = elem.get_text(strip=True)
            # Extract price value (e.g., "€ 349,-" -> 349)
            match = re.search(r'€\s*(\d+)', price_text)
            if match:
                price = float(match.group(1))
                if 150 <= price <= 2000:
                    # Extract proper edition name
                    edition_name = self._extract_edition_name_from_element(elem)

                    # Double-check: if edition_name still looks like a price, clear it
                    if edition_name and self._is_price_text(edition_name):
                        edition_name = ""

                    # Find the edition URL by looking for links in parent card
                    edition_url = None
                    card = elem
                    for _ in range(15):  # Search up to 15 levels
                        parent = card.find_parent()
                        if not parent:
                            break
                        # Look for edition link in this container
                        links = parent.find_all('a', href=True)
                        for link in links:
                            href = link.get('href', '')
                            if '#/edition/' in href:
                                edition_url = href
                                break
                        if edition_url:
                            break
                        card = parent

                    # Create a key to deduplicate
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
            # Find all MUI NativeSelect elements
            selects = self.driver.find_elements(By.CSS_SELECTOR, "select.MuiNativeSelect-select")

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
                        target_km_str = f"{km:,}".replace(",", ".")  # Format: 10.000
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

    def _extract_price_from_page(self) -> Optional[float]:
        """Extract the monthly price from the current page."""
        soup = BeautifulSoup(self.driver.page_source, 'lxml')

        # Primary method: Look for data-testid="price" elements
        price_elements = soup.select('[data-testid*="price"]')
        for elem in price_elements:
            price_text = elem.get_text(strip=True)
            match = re.search(r'€\s*(\d+)', price_text)
            if match:
                price = float(match.group(1))
                if 150 <= price <= 2000:
                    return price

        # Fallback: Look for MuiTypography with price pattern
        mui_elements = soup.select('.MuiTypography-root')
        for elem in mui_elements:
            text = elem.get_text(strip=True)
            match = re.search(r'€\s*(\d+)[,.-]*', text)
            if match:
                price = float(match.group(1))
                if 150 <= price <= 2000:
                    return price

        # Last resort: Search all text for price patterns
        price_patterns = [
            r'€\s*(\d+)[,.-]*\s*(?:p\.?\s*m\.?|per\s*maand|/\s*maand)',
            r'(\d+)[,.](\d{2})\s*p/m',
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

    def _scrape_model_page_prices(self, model_slug: str, model_name: str, filter_url: str = None) -> List[ToyotaEdition]:
        """Scrape all editions for a model by using the model page dropdowns."""
        editions = []
        edition_prices = {}  # {edition_index: {duration_km: price}}
        edition_names = {}   # {edition_index: edition_name}
        discovered_slugs = []  # Edition slugs discovered from model page

        # First, visit the model-specific page to discover edition slugs by clicking cards
        model_page_url = f"{self.OVERVIEW_URL}/{model_slug}"
        logger.info(f"Discovering edition URLs from: {model_page_url}")
        self._rate_limit()
        self.driver.get(model_page_url)
        self._wait_for_page_load()
        self._accept_cookies()
        time.sleep(3)  # Extra wait for JS to load

        # Find edition cards by looking for ancestor of edition-name elements
        edition_cards = self.driver.find_elements(
            By.XPATH,
            '//h4[@data-testid="edition-name"]/ancestor::*[contains(@class, "card") or @role="button"][1]'
        )
        num_cards = len(edition_cards)
        logger.info(f"  Found {num_cards} edition cards to click")

        # Click each card to discover its URL (by index, re-finding after each click)
        edition_urls = []  # List of (edition_name, slug, full_url)
        for i in range(num_cards):
            try:
                # Re-find cards fresh each iteration (DOM changes after navigation)
                edition_cards = self.driver.find_elements(
                    By.XPATH,
                    '//h4[@data-testid="edition-name"]/ancestor::*[contains(@class, "card") or @role="button"][1]'
                )
                if i >= len(edition_cards):
                    break

                card = edition_cards[i]

                # Get edition name before clicking
                edition_name_elem = card.find_element(By.CSS_SELECTOR, '[data-testid="edition-name"]')
                edition_name = edition_name_elem.text.strip()

                # Scroll to card and click
                self.driver.execute_script('arguments[0].scrollIntoView(true);', card)
                time.sleep(0.5)
                card.click()
                time.sleep(1.5)

                # Get URL after click
                current_url = self.driver.current_url

                # Extract slug from URL
                slug_match = re.search(r'#/edition/([^/]+)/configurator', current_url)
                if slug_match:
                    slug = slug_match.group(1)
                    discovered_slugs.append(slug)
                    edition_urls.append((edition_name, slug, current_url))
                    logger.info(f"    Edition {i+1}: {edition_name} -> {slug}")

                # Navigate back to model page for next card
                self.driver.get(model_page_url)
                time.sleep(2)

            except Exception as e:
                logger.debug(f"    Error clicking card {i}: {e}")
                # Try to get back to model page if something went wrong
                try:
                    self.driver.get(model_page_url)
                    time.sleep(2)
                except:
                    pass
                continue

        logger.info(f"  Discovered {len(discovered_slugs)} edition slugs")

        # Now visit the filter URL to get prices
        model_url = filter_url if filter_url else model_page_url
        logger.info(f"Scraping prices from model page: {model_url}")

        self._rate_limit()
        self.driver.get(model_url)
        self._wait_for_page_load()
        time.sleep(2)

        # First, get edition names/info from the initial page load
        initial_prices = self._extract_prices_from_model_page()
        num_editions = len(initial_prices)
        logger.info(f"  Found {num_editions} editions on page")

        if num_editions == 0:
            return []

        # Initialize edition data
        for idx, ep in enumerate(initial_prices):
            edition_prices[idx] = {}

        # Now iterate through duration/km combinations with progress bar
        total_combos = len(DURATIONS) * len(MILEAGES)
        combos = [(d, k) for d in DURATIONS for k in MILEAGES]

        with tqdm(combos, desc=f"  {model_name}", unit="combo", leave=True,
                  bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]') as pbar:
            for duration, km in pbar:
                pbar.set_postfix_str(f"{duration}mo/{km}km")

                # Set the dropdowns
                if not self._set_duration_km_dropdowns(duration, km):
                    logger.debug(f"Could not set dropdowns for {duration}/{km}")

                # Wait for prices to update
                time.sleep(0.5)

                # Extract current prices
                current_prices = self._extract_prices_from_model_page()

                # Store prices for each edition
                for idx, ep in enumerate(current_prices):
                    if idx < num_editions:
                        edition_prices[idx][f"{duration}_{km}"] = ep['price']

        print(f"  {model_name}: Complete - {num_editions} editions")

        # Create ToyotaEdition objects
        for idx, ed_data in enumerate(initial_prices):
            # Use edition_name if valid, otherwise use numbered fallback
            edition_name = ed_data.get('edition_name', '')
            if not edition_name or self._is_price_text(edition_name):
                edition_name = f"Edition {idx+1}"

            # Use discovered edition slug if available (by index)
            if idx < len(discovered_slugs):
                edition_slug = discovered_slugs[idx]
                # Format: https://www.toyota.nl/private-lease/modellen#/edition/{slug}/configurator?model[]={model}&durationMonths=72&yearlyKilometers=5000
                configurator_url = f"{self.OVERVIEW_URL}#/edition/{edition_slug}/configurator?model[]={model_slug}&durationMonths=72&yearlyKilometers=5000"
            else:
                # Try to use edition URL from page if available
                edition_url = ed_data.get('edition_url', '')
                if edition_url:
                    if edition_url.startswith('#'):
                        configurator_url = f"{self.OVERVIEW_URL}/{model_slug}{edition_url}"
                    elif edition_url.startswith('/'):
                        configurator_url = f"{self.BASE_URL}{edition_url}"
                    else:
                        configurator_url = edition_url
                    slug_match = re.search(r'#/edition/([^/]+)', edition_url)
                    edition_slug = slug_match.group(1) if slug_match else f"toyota-{model_slug}-{idx}"
                else:
                    # Fallback to overview URL with model filter
                    configurator_url = f"{self.OVERVIEW_URL}#?model[]={model_slug}&durationMonths=72&yearlyKilometers=5000"
                    edition_slug = f"toyota-{model_slug}-{idx}"

            edition = ToyotaEdition(
                model=model_name,
                edition_name=edition_name,
                edition_slug=edition_slug,
                fuel_type="Hybrid",
                transmission="Automatic",
                base_url=model_url,
                configurator_url=configurator_url,
                price_matrix=edition_prices.get(idx, {})
            )
            if edition.price_matrix:
                editions.append(edition)

        return editions

    def scrape_edition_prices(self, edition: ToyotaEdition, edition_num: int = 0, total_editions: int = 0) -> ToyotaEdition:
        """Scrape the full price matrix for an edition."""
        total_combinations = len(DURATIONS) * len(MILEAGES)
        combos = [(d, k) for d in DURATIONS for k in MILEAGES]

        edition_info = f"[{edition_num}/{total_editions}]" if total_editions > 0 else ""
        desc = f"{edition_info} {edition.model}"

        with tqdm(combos, desc=desc, unit="combo", leave=True,
                  bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]') as pbar:
            for duration, km in pbar:
                pbar.set_postfix_str(f"{duration}mo/{km}km")

                price = self._scrape_price_for_combination(
                    edition.edition_slug, duration, km
                )
                if price:
                    edition.set_price(duration, km, price)

        prices_found = len(edition.price_matrix)
        print(f"{edition.model}: {prices_found}/{total_combinations} prices found")
        logger.info(f"  Found {prices_found}/{total_combinations} prices for {edition.model}")

        return edition

    def scrape_all(self, use_cache: bool = True, cache_file: str = "output/toyota_prices.json") -> List[ToyotaEdition]:
        """Scrape all Toyota editions with full price matrices.

        Args:
            use_cache: If True, check cached data and only refresh if prices changed
            cache_file: Path to the cache file
        """
        logger.info("Starting Toyota.nl private lease scrape")

        try:
            all_editions = []
            cached_data = {}

            # Load cached data if exists
            if use_cache and os.path.exists(cache_file):
                try:
                    with open(cache_file, 'r') as f:
                        cached_list = json.load(f)
                    # Index by model+edition for quick lookup
                    for item in cached_list:
                        key = f"{item.get('model', '')}_{item.get('edition_name', '')}"
                        cached_data[key] = item
                    logger.info(f"Loaded {len(cached_data)} cached editions")
                except Exception as e:
                    logger.warning(f"Could not load cache: {e}")

            # First, get overview prices for all models to check what needs refreshing
            overview_prices = {}
            if use_cache and cached_data:
                logger.info("Checking overview prices to determine which models need refreshing...")
                overview_prices = self._get_overview_prices()

            # Use the new model page approach - scrape each model page directly
            print("\n" + "="*60)
            print("Scraping Toyota.nl Private Lease")
            print("="*60 + "\n")

            for model_slug, model_name in tqdm(self.KNOWN_MODELS, desc="Toyota Models", unit="model",
                                                bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]'):
                # Check if we can use cached data for this model
                if use_cache and cached_data:
                    cached_editions = [v for k, v in cached_data.items() if v.get('model') == model_name]

                    if cached_editions:
                        # Check if overview prices match cached prices (at default 72mo/5000km)
                        needs_refresh = False
                        model_overview = overview_prices.get(model_name, {})

                        for cached in cached_editions:
                            cached_price = cached.get('price_matrix', {}).get('72_5000')
                            edition_name = cached.get('edition_name', '')
                            overview_price = model_overview.get(edition_name)

                            if overview_price and cached_price:
                                if abs(overview_price - cached_price) > 5:  # More than €5 difference
                                    needs_refresh = True
                                    logger.info(f"  {model_name} {edition_name}: price changed €{cached_price} -> €{overview_price}")
                                    break

                        if not needs_refresh and cached_editions:
                            print(f"\n{model_name}: Using cached data ({len(cached_editions)} editions)")
                            for cached in cached_editions:
                                edition = ToyotaEdition(
                                    model=cached.get('model', model_name),
                                    edition_name=cached.get('edition_name', ''),
                                    edition_slug=cached.get('edition_slug', ''),
                                    fuel_type=cached.get('fuel_type', 'Hybrid'),
                                    transmission=cached.get('transmission', 'Automatic'),
                                    power=cached.get('power'),
                                    base_url=cached.get('base_url'),
                                    configurator_url=cached.get('configurator_url'),
                                    price_matrix=cached.get('price_matrix', {})
                                )
                                all_editions.append(edition)
                            continue

                # Need to scrape this model fresh
                print(f"\nProcessing: {model_name}")
                filter_url = f"{self.OVERVIEW_URL}#?model[]={model_slug}&durationMonths=72&yearlyKilometers=5000"
                editions = self._scrape_model_page_prices(model_slug, model_name, filter_url)

                if editions:
                    all_editions.extend(editions)
                    logger.info(f"  Got {len(editions)} editions for {model_name}")
                else:
                    logger.info(f"  No editions found for {model_name}")

            logger.info(f"Completed scraping {len(all_editions)} editions with prices")
            return all_editions

        finally:
            self.close()

    def _get_overview_prices(self) -> Dict[str, Dict[str, float]]:
        """Get prices from overview page for cache validation.

        Returns: {model_name: {edition_name: price}}
        """
        overview_prices = {}

        try:
            # Visit overview with default settings (72mo/5000km)
            overview_url = f"{self.OVERVIEW_URL}#?durationMonths=72&yearlyKilometers=5000"
            self._rate_limit()
            self.driver.get(overview_url)
            self._wait_for_page_load()
            self._accept_cookies()
            time.sleep(2)

            soup = BeautifulSoup(self.driver.page_source, 'lxml')

            # Find all model sections
            for model_slug, model_name in self.KNOWN_MODELS:
                overview_prices[model_name] = {}

                # Try to find prices for this model on the page
                # Look for cards containing the model name
                model_cards = soup.find_all(string=re.compile(model_name, re.IGNORECASE))

                for card_text in model_cards:
                    # Find the parent card
                    card = card_text.find_parent()
                    for _ in range(10):
                        if not card:
                            break
                        card_class = ' '.join(card.get('class', []))
                        if 'card' in card_class.lower():
                            # Found a card, look for price
                            price_elem = card.select_one('[data-testid*="price"]')
                            if price_elem:
                                price_text = price_elem.get_text(strip=True)
                                match = re.search(r'€\s*(\d+)', price_text)
                                if match:
                                    price = float(match.group(1))
                                    # Try to get edition name
                                    edition_elem = card.select_one('[data-testid="edition-name"], h4, h3')
                                    edition_name = edition_elem.get_text(strip=True) if edition_elem else "Unknown"
                                    if not self._is_price_text(edition_name):
                                        overview_prices[model_name][edition_name] = price
                            break
                        card = card.find_parent()

        except Exception as e:
            logger.warning(f"Error getting overview prices: {e}")

        return overview_prices

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


def load_progress(output_file: str = "output/toyota_prices.json") -> Dict[str, dict]:
    """Load existing progress from JSON file."""
    if os.path.exists(output_file):
        try:
            with open(output_file, 'r') as f:
                data = json.load(f)
            # Index by edition_slug for quick lookup
            return {item['edition_slug']: item for item in data}
        except (json.JSONDecodeError, KeyError):
            return {}
    return {}


def save_progress(editions: List[ToyotaEdition], output_file: str = "output/toyota_prices.json"):
    """Save current progress to JSON file."""
    import os
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    output = [asdict(e) for e in editions]
    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2)


def main():
    """Main entry point."""
    output_file = "output/toyota_prices.json"

    scraper = ToyotaScraper(headless=True)

    try:
        # Use the new scrape_all method which uses model page approach
        editions = scraper.scrape_all()

        if editions:
            # Save results
            save_progress(editions, output_file)

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

            print(f"\nSaved {len(editions)} editions to {output_file}")
        else:
            print("No editions found!")

    except Exception as e:
        logger.error(f"Error: {e}")
        raise


if __name__ == "__main__":
    main()
