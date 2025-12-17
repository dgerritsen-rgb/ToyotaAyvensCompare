#!/usr/bin/env python3
"""
Ayvens.com Toyota Private Lease Scraper

Scrapes Toyota vehicles from Ayvens private lease showroom with full price matrix.
Filters to Toyota brand only and extracts prices for all duration/mileage combinations.
"""

import re
import json
import logging
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from urllib.parse import urlencode

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


# Price matrix dimensions - same as Toyota for comparison
DURATIONS = [24, 36, 48, 60, 72]  # months
MILEAGES = [5000, 10000, 15000, 20000, 25000, 30000]  # km/year


@dataclass
class AyvensOffer:
    """An Ayvens Toyota lease offer."""
    model: str
    variant: str
    fuel_type: str
    transmission: str
    power: Optional[str] = None
    offer_url: Optional[str] = None
    price_matrix: Dict[str, float] = field(default_factory=dict)  # "duration_km" -> price

    def get_price(self, duration: int, km: int) -> Optional[float]:
        """Get price for specific duration/km combination."""
        key = f"{duration}_{km}"
        return self.price_matrix.get(key)

    def set_price(self, duration: int, km: int, price: float):
        """Set price for specific duration/km combination."""
        key = f"{duration}_{km}"
        self.price_matrix[key] = price


class AyvensScraper:
    """Scraper for Ayvens.com private lease Toyota offerings."""

    BASE_URL = "https://www.ayvens.com"
    SHOWROOM_URL = "https://www.ayvens.com/nl-nl/private-lease-showroom/"

    # BTO filter for new cars only (excludes used/occasion)
    BTO_FILTER_ID = "ab6b12d4-b554-4815-a3d4-99b7681587f4"

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
            cookie_selectors = [
                "#onetrust-accept-btn-handler",
                ".onetrust-accept-btn-handler",
                "[id*='accept']",
                "[class*='accept']",
            ]
            for selector in cookie_selectors:
                try:
                    buttons = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for btn in buttons:
                        if btn.is_displayed() and ('accept' in btn.text.lower() or 'akkoord' in btn.text.lower()):
                            btn.click()
                            time.sleep(1)
                            logger.debug("Accepted cookies")
                            return
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"No cookie banner or error: {e}")

    def _build_filtered_url(self, duration: int, km: int) -> str:
        """Build URL with Toyota brand filter and duration/km parameters."""
        params = {
            'leaseOption[contractDuration]': duration,
            'leaseOption[mileage]': km,
            'popularFilters': self.BTO_FILTER_ID,
            'brand': 'Toyota',
        }
        return f"{self.SHOWROOM_URL}?{urlencode(params)}"

    def _scroll_to_load_all(self, max_scrolls: int = 20):
        """Scroll down to load all vehicles."""
        last_height = self.driver.execute_script("return document.body.scrollHeight")
        scroll_count = 0

        while scroll_count < max_scrolls:
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)

            new_height = self.driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break

            last_height = new_height
            scroll_count += 1

        logger.debug(f"Scrolled {scroll_count} times")

    def _parse_price(self, text: str) -> Optional[float]:
        """Extract price from text like '€ 399' or '399,00'."""
        if not text:
            return None
        text = text.replace('\xa0', ' ').replace(' ', '').replace('.', '')
        match = re.search(r'€?\s*(\d+)(?:[.,](\d{2}))?', text)
        if match:
            whole = int(match.group(1))
            cents = int(match.group(2)) if match.group(2) else 0
            return whole + cents / 100
        return None

    def _parse_fuel_type(self, text: str) -> str:
        """Parse fuel type from text."""
        text_lower = text.lower()
        if any(x in text_lower for x in ['elektrisch', 'electric', 'ev', 'bev']):
            return "Electric"
        if any(x in text_lower for x in ['plug-in', 'phev']):
            return "Plug-in Hybrid"
        if any(x in text_lower for x in ['hybride', 'hybrid']):
            return "Hybrid"
        if 'diesel' in text_lower:
            return "Diesel"
        if any(x in text_lower for x in ['benzine', 'petrol']):
            return "Petrol"
        return "Unknown"

    def _parse_transmission(self, text: str) -> str:
        """Parse transmission from text."""
        text_lower = text.lower()
        if any(x in text_lower for x in ['automaat', 'automatic', 'cvt', 'dct']):
            return "Automatic"
        if any(x in text_lower for x in ['handgeschakeld', 'manual']):
            return "Manual"
        return "Unknown"

    def _extract_terms_from_text(self, text: str) -> tuple:
        """Extract duration and km from card text."""
        duration = None
        km_year = None

        duration_match = re.search(r'(\d+)\s*maanden?', text, re.IGNORECASE)
        if duration_match:
            duration = int(duration_match.group(1))

        km_match = re.search(r'(\d+[\.,]?\d*)\s*km', text, re.IGNORECASE)
        if km_match:
            km_str = km_match.group(1).replace('.', '').replace(',', '')
            km_year = int(km_str)
            if km_year < 1000:
                km_year = km_year * 1000

        return duration, km_year

    def _parse_vehicle_cards(self, duration: int, km: int) -> List[Dict[str, Any]]:
        """Parse vehicle cards from the current page."""
        cars = []
        soup = BeautifulSoup(self.driver.page_source, 'lxml')

        # Find vehicle cards
        card_selectors = [
            'article',
            '[class*="vehicle-card"]',
            '[class*="car-card"]',
            '[class*="product-card"]',
            '[class*="card"]',
        ]

        cards_found = []
        for selector in card_selectors:
            found = soup.select(selector)
            vehicle_cards = [c for c in found if self._looks_like_toyota_card(c)]
            if vehicle_cards and len(vehicle_cards) > len(cards_found):
                cards_found = vehicle_cards

        logger.debug(f"Found {len(cards_found)} Toyota vehicle cards")

        for card in cards_found:
            try:
                car_data = self._extract_car_data(card, duration, km)
                if car_data:
                    cars.append(car_data)
            except Exception as e:
                logger.debug(f"Error parsing card: {e}")
                continue

        return cars

    def _looks_like_toyota_card(self, element) -> bool:
        """Check if element looks like a Toyota vehicle card."""
        text = element.get_text(' ', strip=True).lower()

        # Must be Toyota
        if 'toyota' not in text:
            return False

        # Must have price indicator
        has_price = '€' in text or 'per maand' in text or '/maand' in text

        # Should have vehicle content
        has_vehicle = any(x in text for x in [
            'pk', 'kw', 'km', 'automaat', 'hybride', 'electric', 'lease', 'maand'
        ])

        has_content = len(text) > 20

        return has_price and (has_vehicle or has_content)

    def _extract_car_data(self, card, duration: int, km: int) -> Optional[Dict[str, Any]]:
        """Extract car data from a single card element."""
        text_content = card.get_text(' ', strip=True)
        text_lower = text_content.lower()

        # Skip non-Toyota or subscription offers
        if 'toyota' not in text_lower:
            return None
        if 'free auto-abonnement' in text_lower:
            return None

        # Extract actual terms from card
        actual_duration, actual_km = self._extract_terms_from_text(text_content)
        duration_months = actual_duration or duration
        km_per_year = actual_km or km

        # Extract price
        price_matches = re.findall(r'€\s*(\d+(?:[.,]\d{2})?)', text_content)
        monthly_price = None
        if price_matches:
            for price_str in reversed(price_matches):
                price = self._parse_price(f"€{price_str}")
                if price and 100 <= price <= 3000:
                    monthly_price = price
                    break

        if not monthly_price:
            return None

        # Extract title
        title = ""
        title_selectors = ['h2', 'h3', 'h4', '.title', '[class*="title"]', '[class*="name"]']
        for tag in title_selectors:
            elem = card.select_one(tag)
            if elem:
                title = elem.get_text(strip=True)
                if title and len(title) > 3:
                    break

        if not title or len(title) < 3:
            return None

        # Parse model from title
        model, variant = self._parse_toyota_title(title)

        # Get offer URL
        offer_url = ""
        link = card.select_one('a[href]')
        if link:
            href = link.get('href', '')
            if href:
                if href.startswith('/'):
                    offer_url = self.BASE_URL + href
                elif href.startswith('http'):
                    offer_url = href

        return {
            'model': model,
            'variant': variant,
            'monthly_price': monthly_price,
            'duration_months': duration_months,
            'km_per_year': km_per_year,
            'fuel_type': self._parse_fuel_type(text_content),
            'transmission': self._parse_transmission(text_content),
            'offer_url': offer_url,
            'raw_title': title,
        }

    def _parse_toyota_title(self, title: str) -> tuple:
        """Parse Toyota model and variant from title."""
        # Remove "Toyota" prefix
        title_clean = re.sub(r'^toyota\s+', '', title, flags=re.IGNORECASE).strip()

        parts = title_clean.split(' ', 1)
        model = parts[0] if parts else title_clean
        variant = parts[1] if len(parts) > 1 else None

        return model, variant

    def _scrape_showroom(self, duration: int, km: int) -> List[Dict[str, Any]]:
        """Scrape all Toyota vehicles for a specific duration/km combination."""
        logger.info(f"Scraping Ayvens Toyota: {duration}mo / {km}km")

        try:
            url = self._build_filtered_url(duration, km)
            self._rate_limit()
            self.driver.get(url)
            self._wait_for_page_load()
            self._accept_cookies()

            time.sleep(2)
            self._scroll_to_load_all()

            cars = self._parse_vehicle_cards(duration, km)
            logger.info(f"Found {len(cars)} Toyota cars for {duration}mo / {km}km")

            return cars

        except Exception as e:
            logger.error(f"Error scraping showroom: {e}")
            return []

    def scrape_all(self) -> List[AyvensOffer]:
        """Scrape all Toyota offers with full price matrices."""
        logger.info("Starting Ayvens Toyota private lease scrape")

        try:
            # Collect all offers across duration/km combinations
            all_offers: Dict[str, AyvensOffer] = {}

            for duration in DURATIONS:
                for km in MILEAGES:
                    cars = self._scrape_showroom(duration, km)

                    for car in cars:
                        # Create unique key for this vehicle
                        key = f"{car['model']}|{car.get('variant', '')}|{car['fuel_type']}"

                        if key not in all_offers:
                            all_offers[key] = AyvensOffer(
                                model=car['model'],
                                variant=car.get('variant', ''),
                                fuel_type=car['fuel_type'],
                                transmission=car['transmission'],
                                offer_url=car.get('offer_url'),
                            )

                        # Set price for this duration/km
                        all_offers[key].set_price(
                            car['duration_months'],
                            car['km_per_year'],
                            car['monthly_price']
                        )

                    self._rate_limit()

            offers = list(all_offers.values())
            logger.info(f"Completed scraping {len(offers)} unique Toyota offers")
            return offers

        finally:
            self.close()


def main():
    """Main entry point."""
    scraper = AyvensScraper(headless=True)

    try:
        offers = scraper.scrape_all()

        # Print summary
        print("\n" + "="*60)
        print("Ayvens Toyota Private Lease Offers")
        print("="*60)

        for offer in offers:
            print(f"\nToyota {offer.model} {offer.variant or ''}")
            print(f"  Fuel: {offer.fuel_type}, Trans: {offer.transmission}")
            print(f"  Prices found: {len(offer.price_matrix)}")

            if offer.price_matrix:
                for duration in DURATIONS[:3]:
                    for km in MILEAGES[:2]:
                        price = offer.get_price(duration, km)
                        if price:
                            print(f"    {duration}mo/{km}km: €{price}/mo")

        # Save to JSON
        output = []
        for offer in offers:
            output.append(asdict(offer))

        with open("output/ayvens_toyota_prices.json", "w") as f:
            json.dump(output, f, indent=2)

        print(f"\nSaved {len(offers)} offers to output/ayvens_toyota_prices.json")

    finally:
        scraper.close()


if __name__ == "__main__":
    main()
