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
from urllib.parse import unquote

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
    """A Leasys lease offer."""
    brand: str
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
    """Scraper for store.leasys.com private lease offerings."""

    BASE_URL = "https://store.leasys.com"
    TOYOTA_URL = "https://store.leasys.com/nl/private/toyota"
    PRIVATE_URL = "https://store.leasys.com/nl/private"

    # Known Toyota models on Leasys that are also on Toyota.nl
    # (URL pattern: /nl/private/brands/Toyota/{model})
    KNOWN_TOYOTA_MODELS = [
        {"slug": "AYGO%20X", "name": "Aygo X"},
        {"slug": "Yaris", "name": "Yaris"},
        {"slug": "Corolla%20Cross", "name": "Corolla Cross"},
    ]

    # All known brands on Leasys
    KNOWN_BRANDS = [
        "Abarth", "Alfa Romeo", "Audi", "BMW", "BYD", "Citroën", "CUPRA",
        "Dacia", "DS", "Fiat", "Ford", "Honda", "Hyundai", "Jeep", "Kia",
        "Lancia", "Land Rover", "Leapmotor", "Lynk & Co", "Mazda",
        "Mercedes-Benz", "Mini", "Mitsubishi", "Nissan", "Opel", "Peugeot",
        "Polestar", "Renault", "SEAT", "Skoda", "smart", "Subaru", "Suzuki",
        "Tesla", "Toyota", "Volkswagen", "Volvo", "XPENG"
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

    def _discover_models(self, brand: str = "Toyota") -> List[Dict[str, Any]]:
        """Discover models available for a given brand on Leasys."""
        logger.info(f"Discovering models for {brand} from Leasys...")

        # For Toyota, use known models for accurate matching
        if brand.lower() == "toyota":
            logger.info("Using known Toyota models from Leasys...")
            models = []
            for model_info in self.KNOWN_TOYOTA_MODELS:
                url = f"{self.BASE_URL}/nl/private/brands/Toyota/{model_info['slug']}"
                models.append({
                    'model_slug': model_info['slug'],
                    'model_name': model_info['name'],
                    'brand': brand,
                    'url': url,
                })
            logger.info(f"Found {len(models)} Toyota models: {[m['model_name'] for m in models]}")
            return models

        # For other brands, discover models from the brand page
        models = []
        try:
            # URL format: /nl/private/{brand-slug} (lowercase, dashes)
            brand_slug = brand.lower().replace(' ', '-').replace('&', '-')
            brand_url = f"{self.BASE_URL}/nl/private/{brand_slug}"

            self._rate_limit()
            self.driver.get(brand_url)
            self._wait_for_page_load()
            self._accept_cookies()

            soup = BeautifulSoup(self.driver.page_source, 'lxml')

            # Find model links - pattern: /nl/private/brands/{Brand}/{Model}
            # e.g., /nl/private/brands/Fiat/Topolino or /nl/private/brands/Fiat/Grande%20Panda
            links = soup.find_all('a', href=True)
            seen_models = set()

            for link in links:
                href = link.get('href', '')
                # Pattern: /nl/private/brands/{Brand}/{Model}
                # Brand name in URL is case-sensitive and matches the original brand name
                pattern = r'/nl/private/brands/([^/]+)/([^/]+)'
                match = re.search(pattern, href)
                if match:
                    url_brand = match.group(1)
                    model_slug = match.group(2)

                    # URL decode model slug (e.g., Grande%20Panda -> Grande Panda)
                    model_name = unquote(model_slug)

                    # Skip duplicates
                    if model_name.lower() in seen_models:
                        continue
                    seen_models.add(model_name.lower())

                    # Build URL - use original format
                    model_url = f"{self.BASE_URL}/nl/private/brands/{url_brand}/{model_slug}"

                    models.append({
                        'model_slug': model_slug,
                        'model_name': model_name,
                        'brand': url_brand,  # Use brand from URL to preserve case
                        'url': model_url,
                    })

            logger.info(f"Found {len(models)} {brand} models: {[m['model_name'] for m in models]}")

        except Exception as e:
            logger.error(f"Error discovering models for {brand}: {e}")

        return models

    def _discover_editions(self, model: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Discover available editions/trims for a model."""
        brand = model.get('brand', 'Toyota')
        logger.info(f"Discovering editions for {brand} {model['model_name']}...")
        editions = []

        try:
            self._rate_limit()
            self.driver.get(model['url'])
            self._wait_for_page_load()
            self._accept_cookies()

            soup = BeautifulSoup(self.driver.page_source, 'lxml')

            # Find edition links
            # Pattern 1 (lowercase brand): /nl/private/{brand-slug}/{model-slug}/{edition}/...
            # Pattern 2 (brands path): /nl/private/brands/{Brand}/{Model}/{edition}/...
            # Example: /nl/private/toyota/aygo-x/play/1-0-vvt-i-mt-m-p/pure-white/.../factory/2522
            # Example: /nl/private/fiat/topolino/dolcevita/electric/full-led/bianco-gelato-tri/yes/factory/11034
            links = soup.find_all('a', href=True)

            # Normalize brand and model slug for URL matching (e.g., "AYGO X" -> "aygo-x")
            brand_slug = brand.lower().replace(' ', '-').replace('&', '-')
            model_slug_normalized = model['model_name'].lower().replace(' ', '-').replace('%20', '-')

            for link in links:
                href = link.get('href', '')

                # Try both URL patterns
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

                if edition_slug:
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
                        'brand': brand,
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

        # Build descriptive progress bar: Leasys | Brand | Model | Edition
        brand = edition.get('brand', 'Unknown')
        model = edition.get('model_name', 'Unknown')
        edition_name = edition.get('edition_name', 'Unknown')
        desc = f"Leasys | {brand} | {model} | {edition_name}"

        try:
            with tqdm(combos, unit="price", leave=False,
                      bar_format='{desc} {n_fmt}/{total_fmt} {bar}') as pbar:
                for duration, mileage in pbar:
                    pbar.set_description(f"{desc} | {duration}mo/{mileage:,}km", refresh=True)

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

    def scrape_brand(self, brand: str) -> List[LeasysOffer]:
        """Scrape all offers for a specific brand with price matrices.

        Args:
            brand: Brand name (e.g., "Toyota", "BMW", "Volkswagen")

        Returns:
            List of LeasysOffer objects for the brand
        """
        logger.info(f"Starting Leasys {brand} private lease scrape")

        # Discover models for this brand
        models = self._discover_models(brand)

        if not models:
            logger.warning(f"No {brand} models found")
            return []

        offers = []

        for model in tqdm(models, desc=f"Leasys | {brand} | Models", unit="model",
                         bar_format='{desc} | {bar} {n_fmt}/{total_fmt} [{elapsed}<{remaining}]'):
            logger.info(f"Processing model: {brand} {model['model_name']}")

            # Discover editions for this model
            editions = self._discover_editions(model)

            if not editions:
                logger.info(f"  No editions found for {model['model_name']}")
                continue

            for edition in tqdm(editions, desc=f"Leasys | {brand} | {model['model_name']} | Editions", unit="ed", leave=False,
                               bar_format='{desc} | {bar} {n_fmt}/{total_fmt}'):
                logger.info(f"  Processing edition: {edition['edition_name']}")

                # Scrape price matrix
                price_matrix = self._scrape_edition_prices(edition)
                logger.info(f"    Captured {len(price_matrix)} price points")

                # Determine fuel type based on common patterns
                fuel_type = self._guess_fuel_type(brand, model['model_name'], edition['edition_name'])

                offer = LeasysOffer(
                    brand=brand,
                    model=model['model_name'],
                    variant=edition['edition_name'],
                    fuel_type=fuel_type,
                    transmission="Automatic",
                    offer_url=edition['url'],
                    price_matrix=price_matrix,
                    edition_name=edition['edition_name'],
                )

                offers.append(offer)

        logger.info(f"Completed scraping {len(offers)} Leasys {brand} offers")
        return offers

    def _guess_fuel_type(self, brand: str, model: str, edition: str) -> str:
        """Guess fuel type based on brand/model/edition names."""
        combined = f"{brand} {model} {edition}".lower()

        # Electric indicators
        if any(x in combined for x in ['electric', 'ev', 'bev', 'e-', ' e ', 'model 3', 'model y',
                                        'model s', 'model x', 'id.', 'i3', 'i4', 'ix', 'eq',
                                        'polestar', 'bz4x', 'ioniq', 'kona electric', 'zoe',
                                        'leaf', 'enyaq', 'born', 'e-208', 'e-308', 'e-c4',
                                        'mach-e', 'mustang mach', 'leapmotor', 'xpeng', 'byd']):
            return "Electric"

        # Hybrid indicators
        if any(x in combined for x in ['hybrid', 'phev', 'plug-in', 'hev']):
            return "Hybrid"

        # Diesel indicators
        if any(x in combined for x in ['diesel', 'tdi', 'cdi', 'hdi', 'dci', 'bluehdi', 'jtd']):
            return "Diesel"

        # Default to petrol
        return "Petrol"

    def scrape_all(self, brand: str = "Toyota") -> List[LeasysOffer]:
        """Scrape all offers for a brand with price matrices.

        Args:
            brand: Brand name (default: "Toyota")

        Returns:
            List of LeasysOffer objects
        """
        try:
            return self.scrape_brand(brand)
        finally:
            self.close()

    def scrape_all_brands(self, brands: Optional[List[str]] = None) -> Dict[str, List[LeasysOffer]]:
        """Scrape all offers for multiple brands.

        Args:
            brands: List of brand names. If None, scrapes all known brands.

        Returns:
            Dict mapping brand name to list of LeasysOffer objects
        """
        if brands is None:
            brands = self.KNOWN_BRANDS

        logger.info(f"Starting Leasys multi-brand scrape for {len(brands)} brands")

        all_offers = {}

        try:
            for brand in tqdm(brands, desc="Leasys | All Brands", unit="brand",
                             bar_format='{desc} | {bar} {n_fmt}/{total_fmt} [{elapsed}<{remaining}]'):
                try:
                    offers = self.scrape_brand(brand)
                    if offers:
                        all_offers[brand] = offers
                        logger.info(f"Scraped {len(offers)} offers for {brand}")
                    else:
                        logger.info(f"No offers found for {brand}")
                except Exception as e:
                    logger.error(f"Error scraping {brand}: {e}")
                    continue

            total_offers = sum(len(v) for v in all_offers.values())
            logger.info(f"Completed multi-brand scrape: {total_offers} offers across {len(all_offers)} brands")
            return all_offers

        finally:
            self.close()

    def get_overview_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Get lightweight metadata from model pages for change detection.

        Returns:
            Dict[model_name, {edition_count, editions_hash, editions: [...]}]
        """
        from cache_manager import compute_hash

        logger.info("Fetching Leasys overview metadata for change detection...")
        metadata = {}

        try:
            models = self._discover_models()

            for model in models:
                model_name = model['model_name']

                # Discover editions for this model
                editions = self._discover_editions(model)

                edition_slugs = [e.get('edition_slug', '') for e in editions]

                metadata[model_name] = {
                    'edition_count': len(editions),
                    'editions_hash': compute_hash(edition_slugs) if edition_slugs else '',
                    'editions': [e.get('edition_name') for e in editions],
                }

                logger.info(f"  {model_name}: {len(editions)} editions")

            return metadata

        except Exception as e:
            logger.error(f"Error fetching Leasys overview metadata: {e}")
            return {}
        finally:
            self.close()

    def scrape_model(self, model_name: str) -> List[LeasysOffer]:
        """Scrape a single model only.

        Args:
            model_name: Name of model to scrape (e.g., "Yaris", "Aygo X")

        Returns:
            List of LeasysOffer objects for that model
        """
        logger.info(f"Scraping single Leasys model: {model_name}")

        try:
            # Find matching model
            target_model = None
            for model_info in self.KNOWN_TOYOTA_MODELS:
                if model_info['name'].lower() == model_name.lower():
                    target_model = {
                        'model_slug': model_info['slug'],
                        'model_name': model_info['name'],
                        'url': f"{self.BASE_URL}/nl/private/brands/Toyota/{model_info['slug']}",
                    }
                    break

            if target_model is None:
                logger.error(f"Unknown model: {model_name}")
                logger.info(f"Available models: {[m['name'] for m in self.KNOWN_TOYOTA_MODELS]}")
                return []

            # Discover and scrape editions
            editions = self._discover_editions(target_model)
            offers = []

            for edition in editions:
                logger.info(f"  Processing edition: {edition['edition_name']}")

                price_matrix = self._scrape_edition_prices(edition)

                # Determine fuel type
                fuel_type = "Hybrid"
                model_lower = target_model['model_name'].lower()
                if 'proace' in model_lower:
                    fuel_type = "Diesel"
                elif 'bz4x' in model_lower:
                    fuel_type = "Electric"

                offer = LeasysOffer(
                    brand="Toyota",
                    model=target_model['model_name'],
                    variant=edition['edition_name'],
                    fuel_type=fuel_type,
                    transmission="Automatic",
                    offer_url=edition['url'],
                    price_matrix=price_matrix,
                    edition_name=edition['edition_name'],
                )

                offers.append(offer)

            return offers

        finally:
            self.close()


def save_offers(offers: List[LeasysOffer], output_file: str = "output/leasys_prices.json"):
    """Save offers to JSON file."""
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    output = [asdict(o) for o in offers]
    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2)


def save_all_brand_offers(all_offers: Dict[str, List[LeasysOffer]], output_dir: str = "output"):
    """Save offers from all brands to separate JSON files."""
    os.makedirs(output_dir, exist_ok=True)

    total = 0
    for brand, offers in all_offers.items():
        if offers:
            brand_slug = brand.lower().replace(' ', '_').replace('-', '_')
            output_file = os.path.join(output_dir, f"leasys_{brand_slug}_prices.json")
            save_offers(offers, output_file)
            total += len(offers)
            print(f"  Saved {len(offers)} {brand} offers to {output_file}")

    # Also save combined file
    all_combined = []
    for offers in all_offers.values():
        all_combined.extend(offers)
    combined_file = os.path.join(output_dir, "leasys_all_brands_prices.json")
    save_offers(all_combined, combined_file)
    print(f"\n  Saved combined {total} offers to {combined_file}")


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Scrape Leasys private lease offers")
    parser.add_argument('--brand', '-b', type=str, default=None,
                        help="Specific brand to scrape (e.g., Toyota, BMW, Volkswagen)")
    parser.add_argument('--all-brands', '-a', action='store_true',
                        help="Scrape all available brands")
    parser.add_argument('--list-brands', '-l', action='store_true',
                        help="List all known brands")
    args = parser.parse_args()

    scraper = LeasysScraper(headless=True)

    if args.list_brands:
        print("Known brands on Leasys:")
        for brand in sorted(scraper.KNOWN_BRANDS):
            print(f"  - {brand}")
        return

    if args.all_brands:
        print("Scraping all brands from Leasys...")
        all_offers = scraper.scrape_all_brands()

        if all_offers:
            print("\n" + "="*60)
            print("Leasys All Brands Private Lease Offers")
            print("="*60)

            for brand, offers in all_offers.items():
                print(f"\n{brand}: {len(offers)} offers")

            save_all_brand_offers(all_offers)
            total = sum(len(v) for v in all_offers.values())
            print(f"\nTotal: {total} offers from {len(all_offers)} brands")
        return

    # Single brand mode
    brand = args.brand or "Toyota"
    brand_slug = brand.lower().replace(' ', '_').replace('-', '_')
    output_file = f"output/leasys_{brand_slug}_prices.json"

    try:
        offers = scraper.scrape_all(brand)

        if offers:
            save_offers(offers, output_file)

            print("\n" + "="*60)
            print(f"Leasys {brand} Private Lease Offers")
            print("="*60)

            for offer in offers:
                print(f"\n{offer.brand} {offer.model} - {offer.variant}")
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
