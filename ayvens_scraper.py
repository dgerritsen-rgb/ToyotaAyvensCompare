#!/usr/bin/env python3
"""
Ayvens.com Toyota Private Lease Scraper - V2

Scrapes Toyota vehicles from Ayvens using the correct approach:
1. Get Toyota vehicle list from /private-lease-showroom/toyota/
2. Navigate to individual vehicle detail pages
3. Use slider interaction to get prices for all duration/mileage combinations
"""

import re
import json
import logging
import time
import os
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict

from tqdm import tqdm
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
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
    vehicle_id: str = ""
    power: Optional[str] = None
    offer_url: Optional[str] = None
    price_matrix: Dict[str, float] = field(default_factory=dict)  # "duration_km" -> price
    is_new: bool = True  # True = build-to-order, False = used car
    edition_name: str = ""  # Clean edition name for matching (e.g., "Active", "GR-Sport")

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
    TOYOTA_SHOWROOM_URL = "https://www.ayvens.com/nl-nl/private-lease-showroom/toyota/"

    REQUEST_DELAY = 1.5  # seconds between requests

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

    @staticmethod
    def _is_used_car(variant: str) -> bool:
        """Detect if a vehicle is used based on variant text."""
        variant_lower = variant.lower()
        used_indicators = [
            'kilometerstand',  # Odometer reading
            '1e tenaamstelling',  # First registration date
            'bouwjaar',  # Build year
            'km ',  # Mileage indicator
        ]
        return any(indicator in variant_lower for indicator in used_indicators)

    @staticmethod
    def _extract_edition_name(variant: str) -> str:
        """Extract clean edition name from variant text."""
        # Common Toyota edition names
        edition_patterns = [
            r'\b(Active|Comfort|Dynamic|Executive|GR[- ]?Sport|Style|First|Edition|Premium|Lounge)\b',
        ]

        for pattern in edition_patterns:
            match = re.search(pattern, variant, re.IGNORECASE)
            if match:
                edition = match.group(1).strip()
                # Normalize GR-Sport variants
                if edition.upper().startswith('GR'):
                    return 'GR-Sport'
                return edition.title()

        return ""

    @staticmethod
    def _extract_power_kw(variant: str) -> Optional[int]:
        """Extract power in kW from variant text."""
        # Look for patterns like "140", "115", "130" which indicate power
        # Or "85 kW", "103 kW"
        kw_match = re.search(r'(\d{2,3})\s*kW', variant)
        if kw_match:
            return int(kw_match.group(1))

        # Look for power indicator at start (e.g., "140 Active")
        power_match = re.search(r'^(\d{3})\s+\w', variant)
        if power_match:
            return int(power_match.group(1))

        return None

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
            buttons = self.driver.find_elements(By.CSS_SELECTOR, "#onetrust-accept-btn-handler")
            for btn in buttons:
                if btn.is_displayed():
                    btn.click()
                    time.sleep(1)
                    logger.debug("Accepted cookies")
                    return
        except Exception as e:
            logger.debug(f"No cookie banner or error: {e}")

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

    def _get_current_price(self) -> Optional[float]:
        """Get current displayed price from page."""
        try:
            soup = BeautifulSoup(self.driver.page_source, 'lxml')
            price_elem = soup.select_one('[data-testid="localized-price"]')
            if price_elem:
                price_text = price_elem.get_text(strip=True)
                match = re.search(r'€\s*(\d+)', price_text)
                if match:
                    price = float(match.group(1))
                    # Validate price is in reasonable range for private lease
                    if 100 <= price <= 2000:
                        return price
                    else:
                        logger.debug(f"Price {price} outside valid range, ignoring")
                        return None
        except Exception as e:
            logger.debug(f"Error getting price: {e}")
        return None

    def _get_slider_values(self) -> Tuple[Optional[int], Optional[int]]:
        """Get current duration and mileage from sliders."""
        duration = None
        mileage = None

        try:
            sliders = self.driver.find_elements(By.CSS_SELECTOR, "[role='slider']")
            for slider in sliders:
                try:
                    min_val = int(slider.get_attribute('aria-valuemin') or 0)
                    max_val = int(slider.get_attribute('aria-valuemax') or 0)
                    now_val = int(slider.get_attribute('aria-valuenow') or 0)

                    # Duration slider has min=12, max=72
                    if min_val == 12 and max_val == 72:
                        duration = now_val
                    # Mileage slider has min=5000, max=30000
                    elif min_val == 5000 and max_val == 30000:
                        mileage = now_val
                except (ValueError, TypeError):
                    continue
        except Exception as e:
            logger.debug(f"Error getting slider values: {e}")

        return duration, mileage

    def _set_slider_value(self, slider_type: str, target_value: int) -> bool:
        """Set slider to specific value using JavaScript."""
        try:
            sliders = self.driver.find_elements(By.CSS_SELECTOR, "[role='slider']")

            for slider in sliders:
                try:
                    min_val = int(slider.get_attribute('aria-valuemin') or 0)
                    max_val = int(slider.get_attribute('aria-valuemax') or 0)

                    # Find the right slider
                    is_duration = (slider_type == 'duration' and min_val == 12 and max_val == 72)
                    is_mileage = (slider_type == 'mileage' and min_val == 5000 and max_val == 30000)

                    if is_duration or is_mileage:
                        # Calculate position percentage
                        pct = (target_value - min_val) / (max_val - min_val)

                        # Find the slider track
                        parent = slider.find_element(By.XPATH, "./..")
                        track = parent.find_element(By.CSS_SELECTOR, "[class*='track'], [class*='Track']")

                        if track:
                            # Click at the right position on the track
                            track_width = track.size['width']
                            offset_x = int(track_width * pct) - int(track_width / 2)

                            actions = ActionChains(self.driver)
                            actions.move_to_element(track)
                            actions.move_by_offset(offset_x, 0)
                            actions.click()
                            actions.perform()

                            time.sleep(0.5)
                            return True

                except Exception as e:
                    logger.debug(f"Error with slider: {e}")
                    continue

        except Exception as e:
            logger.debug(f"Error setting slider: {e}")

        return False

    def _set_slider_by_drag(self, slider_type: str, target_value: int) -> bool:
        """Set slider by dragging handle to position."""
        try:
            sliders = self.driver.find_elements(By.CSS_SELECTOR, "[role='slider']")

            for slider in sliders:
                try:
                    min_val = int(slider.get_attribute('aria-valuemin') or 0)
                    max_val = int(slider.get_attribute('aria-valuemax') or 0)
                    now_val = int(slider.get_attribute('aria-valuenow') or 0)

                    is_duration = (slider_type == 'duration' and min_val == 12 and max_val == 72)
                    is_mileage = (slider_type == 'mileage' and min_val == 5000 and max_val == 30000)

                    if is_duration or is_mileage:
                        # Find the handle element
                        parent = slider.find_element(By.XPATH, "./..")
                        handles = parent.find_elements(By.CSS_SELECTOR, "[class*='handle'], [class*='Handle'], [class*='thumb'], [class*='Thumb']")

                        if handles:
                            handle = handles[0]
                            track_width = 300  # Approximate track width

                            # Calculate drag distance
                            current_pct = (now_val - min_val) / (max_val - min_val)
                            target_pct = (target_value - min_val) / (max_val - min_val)
                            delta_pct = target_pct - current_pct
                            drag_x = int(delta_pct * track_width)

                            if abs(drag_x) > 5:
                                actions = ActionChains(self.driver)
                                actions.click_and_hold(handle)
                                actions.move_by_offset(drag_x, 0)
                                actions.release()
                                actions.perform()
                                time.sleep(0.5)
                                return True

                except Exception as e:
                    logger.debug(f"Error dragging slider: {e}")
                    continue

        except Exception as e:
            logger.debug(f"Error in drag operation: {e}")

        return False

    def _set_slider_by_js(self, slider_type: str, target_value: int) -> bool:
        """Set slider value using JavaScript events."""
        try:
            # Find slider element and set value directly
            if slider_type == 'duration':
                min_val, max_val = 12, 72
            else:
                min_val, max_val = 5000, 30000

            sliders = self.driver.find_elements(By.CSS_SELECTOR, "[role='slider']")

            for slider in sliders:
                try:
                    slider_min = int(slider.get_attribute('aria-valuemin') or 0)
                    slider_max = int(slider.get_attribute('aria-valuemax') or 0)

                    if slider_min == min_val and slider_max == max_val:
                        # Calculate percentage for React slider
                        pct = (target_value - min_val) / (max_val - min_val) * 100

                        # Try to trigger React's onChange by simulating key events
                        self.driver.execute_script("""
                            arguments[0].setAttribute('aria-valuenow', arguments[1]);
                            arguments[0].focus();
                        """, slider, target_value)

                        # Dispatch input event
                        self.driver.execute_script("""
                            var event = new Event('input', { bubbles: true });
                            arguments[0].dispatchEvent(event);
                        """, slider)

                        time.sleep(0.3)
                        return True

                except Exception as e:
                    logger.debug(f"Error with JS slider: {e}")
                    continue

        except Exception as e:
            logger.debug(f"Error in JS operation: {e}")

        return False

    # Hardcoded BTO (Build-to-Order) Toyota variant pages on Ayvens
    # These are the only new/configurable Toyota models available
    BTO_VARIANT_URLS = [
        "https://www.ayvens.com/nl-nl/private-lease-showroom/model/toyota/yaris-cross/suv/",
        "https://www.ayvens.com/nl-nl/private-lease-showroom/model/toyota/corolla-touring-sports/stationwagon/",
    ]

    def _discover_variant_pages(self) -> List[str]:
        """Return the known BTO Toyota variant pages."""
        logger.info("Using known BTO Toyota variant pages...")
        logger.info(f"BTO models: Yaris Cross SUV, Corolla Touring Sports")
        return self.BTO_VARIANT_URLS

    def _discover_toyota_vehicles(self) -> List[Dict[str, Any]]:
        """Discover all Toyota vehicles by navigating through variant pages."""
        logger.info("Discovering Toyota vehicles from showroom...")
        vehicles = []

        try:
            # Step 1: Get variant page URLs from the Toyota showroom
            variant_urls = self._discover_variant_pages()

            if not variant_urls:
                logger.warning("No variant pages found")
                return []

            # Step 2: Visit each variant page to find individual vehicle URLs
            for variant_url in variant_urls:
                logger.info(f"Checking variant page: {variant_url}")
                self._rate_limit()
                self.driver.get(variant_url)
                self._wait_for_page_load()

                # Scroll to load all vehicles
                for _ in range(3):
                    self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(0.5)

                soup = BeautifulSoup(self.driver.page_source, 'lxml')
                all_links = soup.find_all('a', href=True)

                for link in all_links:
                    href = link.get('href', '')

                    # Look for vehicle detail page links
                    # Pattern: /private-lease-showroom/onze-autos/{id}/toyota-{model}
                    match = re.search(r'/private-lease-showroom/onze-autos/(\d+)/toyota-([^/]+)', href)
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

                        # Get parent context for more details
                        parent = link.find_parent(['div', 'article'])
                        parent_text = parent.get_text(' ', strip=True) if parent else ""

                        # Detect fuel type
                        fuel_type = "Hybrid"
                        context = (link_text + " " + parent_text).lower()
                        if any(x in context for x in ['elektrisch', 'electric', 'ev', 'bz4x']):
                            fuel_type = "Electric"
                        elif 'hybrid' in context:
                            fuel_type = "Hybrid"

                        # Extract variant from link text
                        variant = ""
                        if link_text:
                            # Try to extract variant info (e.g., "1.5 Hybrid Active Automaat")
                            variant_match = re.search(r'([\d.]+\s*(?:Hybrid|Electric)?.*?)(?:\d+d)?$', link_text, re.IGNORECASE)
                            if variant_match:
                                variant = variant_match.group(1).strip()

                        vehicles.append({
                            'vehicle_id': vehicle_id,
                            'model': model_name,
                            'model_slug': model_slug,
                            'variant': variant,
                            'url': full_url,
                            'fuel_type': fuel_type,
                        })

            # Deduplicate by vehicle_id
            seen_ids = set()
            unique_vehicles = []
            for v in vehicles:
                if v['vehicle_id'] not in seen_ids:
                    seen_ids.add(v['vehicle_id'])
                    unique_vehicles.append(v)

            logger.info(f"Discovered {len(unique_vehicles)} unique Toyota vehicles")
            return unique_vehicles

        except Exception as e:
            logger.error(f"Error discovering vehicles: {e}")
            return []

    def _has_configurable_sliders(self) -> bool:
        """Check if the current page has configurable duration/mileage sliders."""
        try:
            sliders = self.driver.find_elements(By.CSS_SELECTOR, "[role='slider']")
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

    def _reset_slider_to_min(self, slider_type: str) -> bool:
        """Reset slider to minimum value using HOME key with retry."""
        try:
            from selenium.webdriver.common.keys import Keys

            if slider_type == 'duration':
                min_val, max_val = 12, 72
            else:
                min_val, max_val = 5000, 30000

            for attempt in range(3):  # Retry up to 3 times
                sliders = self.driver.find_elements(By.CSS_SELECTOR, "[role='slider']")

                for slider in sliders:
                    try:
                        slider_min = int(slider.get_attribute('aria-valuemin') or 0)
                        slider_max = int(slider.get_attribute('aria-valuemax') or 0)
                        current_val = int(slider.get_attribute('aria-valuenow') or 0)

                        if slider_min == min_val and slider_max == max_val:
                            # Already at minimum
                            if current_val == min_val:
                                return True

                            # Click and focus the slider
                            try:
                                # First scroll the slider into view
                                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", slider)
                                time.sleep(0.2)
                                # Try regular click first
                                slider.click()
                            except Exception:
                                try:
                                    # Use JavaScript click if regular click fails
                                    self.driver.execute_script("arguments[0].click(); arguments[0].focus();", slider)
                                except Exception:
                                    continue
                            time.sleep(0.15)

                            # Send HOME key to go to minimum
                            try:
                                slider.send_keys(Keys.HOME)
                            except Exception:
                                continue
                            time.sleep(0.3)

                            # Verify it actually moved
                            new_val = int(slider.get_attribute('aria-valuenow') or 0)
                            if new_val == min_val:
                                return True

                            time.sleep(0.2)

                    except Exception:
                        continue

            # If all attempts failed, try multiple LEFT keys as fallback
            logger.debug(f"HOME key failed for {slider_type}, trying LEFT key fallback")
            sliders = self.driver.find_elements(By.CSS_SELECTOR, "[role='slider']")
            for slider in sliders:
                try:
                    slider_min = int(slider.get_attribute('aria-valuemin') or 0)
                    slider_max = int(slider.get_attribute('aria-valuemax') or 0)

                    if slider_min == min_val and slider_max == max_val:
                        slider.click()
                        time.sleep(0.1)
                        # Send many LEFT keys to ensure we reach minimum
                        for _ in range(10):
                            slider.send_keys(Keys.ARROW_LEFT)
                            time.sleep(0.05)
                        time.sleep(0.2)
                        return True
                except Exception:
                    continue

        except Exception as e:
            logger.debug(f"Error in reset: {e}")
        return False

    def _move_slider_right(self, slider_type: str) -> bool:
        """Move slider one position to the right."""
        try:
            from selenium.webdriver.common.keys import Keys

            if slider_type == 'duration':
                min_val, max_val = 12, 72
            else:
                min_val, max_val = 5000, 30000

            sliders = self.driver.find_elements(By.CSS_SELECTOR, "[role='slider']")

            for slider in sliders:
                try:
                    slider_min = int(slider.get_attribute('aria-valuemin') or 0)
                    slider_max = int(slider.get_attribute('aria-valuemax') or 0)

                    if slider_min == min_val and slider_max == max_val:
                        # Scroll into view and click
                        try:
                            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", slider)
                            time.sleep(0.1)
                            slider.click()
                        except Exception:
                            try:
                                self.driver.execute_script("arguments[0].click(); arguments[0].focus();", slider)
                            except Exception:
                                continue

                        time.sleep(0.05)
                        slider.send_keys(Keys.ARROW_RIGHT)
                        time.sleep(0.2)
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def _set_slider_with_keys(self, slider_type: str, target_value: int) -> bool:
        """Set slider value using keyboard arrow keys - more reliable than dragging."""
        try:
            from selenium.webdriver.common.keys import Keys

            if slider_type == 'duration':
                min_val, max_val, step = 12, 72, 12  # Duration steps: 12, 24, 36, 48, 60, 72
            else:
                min_val, max_val, step = 5000, 30000, 5000  # Mileage steps: 5000, 10000, ...

            sliders = self.driver.find_elements(By.CSS_SELECTOR, "[role='slider']")
            logger.debug(f"Found {len(sliders)} sliders, looking for {slider_type} (min={min_val}, max={max_val})")

            for slider in sliders:
                try:
                    slider_min = int(slider.get_attribute('aria-valuemin') or 0)
                    slider_max = int(slider.get_attribute('aria-valuemax') or 0)
                    current_val = int(slider.get_attribute('aria-valuenow') or 0)

                    logger.debug(f"  Slider: min={slider_min}, max={slider_max}, current={current_val}")

                    if slider_min == min_val and slider_max == max_val:
                        # Already at target value
                        if current_val == target_value:
                            logger.debug(f"  Already at target {target_value}")
                            return True

                        # Focus the slider
                        slider.click()
                        time.sleep(0.1)

                        # Calculate steps needed
                        steps_needed = (target_value - current_val) // step
                        logger.debug(f"  Moving {steps_needed} steps from {current_val} to {target_value}")

                        if steps_needed > 0:
                            for _ in range(steps_needed):
                                slider.send_keys(Keys.ARROW_RIGHT)
                                time.sleep(0.05)
                        elif steps_needed < 0:
                            for _ in range(abs(steps_needed)):
                                slider.send_keys(Keys.ARROW_LEFT)
                                time.sleep(0.05)

                        time.sleep(0.3)

                        # Verify the slider actually moved
                        new_val = int(slider.get_attribute('aria-valuenow') or 0)
                        logger.debug(f"  After keys: new value = {new_val}")

                        return True

                except Exception as e:
                    logger.debug(f"Error with slider keys: {e}")
                    continue

        except Exception as e:
            logger.debug(f"Error setting slider with keys: {e}")

        return False

    def _ensure_valid_session(self):
        """Ensure the browser session is valid, recreating if necessary."""
        try:
            # Try a simple operation to check session validity
            self.driver.current_url
            return True
        except Exception:
            logger.info("Session invalid, recreating browser...")
            try:
                if self._driver:
                    self._driver.quit()
            except Exception:
                pass
            self._driver = None
            # Access driver property to create new instance
            _ = self.driver
            return True

    def _scrape_vehicle_prices(self, vehicle: Dict[str, Any]) -> Dict[str, float]:
        """Scrape all price combinations for a vehicle by iterating through slider positions."""
        price_matrix = {}

        try:
            # Ensure we have a valid session
            self._ensure_valid_session()

            self._rate_limit()
            self.driver.get(vehicle['url'])
            self._wait_for_page_load()
            time.sleep(2)

            # Accept cookies - required before sliders can be interacted with
            self._accept_cookies()

            # Check if this vehicle has configurable sliders (new cars do, used cars may not)
            if not self._has_configurable_sliders():
                logger.debug(f"No configurable sliders found for {vehicle['model']} - likely a used car")
                # Just get the initial price
                initial_price = self._get_current_price()
                duration, mileage = self._get_slider_values()
                if initial_price and duration and mileage:
                    price_matrix[f"{duration}_{mileage}"] = initial_price
                return price_matrix

            # Duration slider has 6 positions: 12, 24, 36, 48, 60, 72
            # Mileage slider has 7 positions: 5000, 7500, 10000, 15000, 20000, 25000, 30000
            DURATION_POSITIONS = 6
            MILEAGE_POSITIONS = 7
            total_combos = DURATION_POSITIONS * MILEAGE_POSITIONS

            # Reset duration to minimum (12 months)
            self._reset_slider_to_min('duration')
            time.sleep(0.5)

            combo_count = 0
            with tqdm(total=total_combos, desc="    Price points", unit="point", leave=False,
                      bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]') as pbar:
                for dur_pos in range(DURATION_POSITIONS):
                    # For each duration position, reset mileage to minimum
                    self._reset_slider_to_min('mileage')
                    time.sleep(0.5)

                    for mil_pos in range(MILEAGE_POSITIONS):
                        # Get current slider values and price
                        time.sleep(0.5)  # Wait for price to update
                        actual_duration, actual_mileage = self._get_slider_values()
                        price = self._get_current_price()

                        if price and actual_duration and actual_mileage:
                            key = f"{actual_duration}_{actual_mileage}"
                            if key not in price_matrix:
                                price_matrix[key] = price
                                pbar.set_postfix_str(f"{actual_duration}mo/{actual_mileage}km: €{price}")
                                logger.debug(f"  {actual_duration}mo/{actual_mileage}km = €{price}")

                        # Move mileage to next position (unless at last position)
                        if mil_pos < MILEAGE_POSITIONS - 1:
                            self._move_slider_right('mileage')
                            time.sleep(0.2)

                        pbar.update(1)

                    # Move duration to next position (unless at last position)
                    if dur_pos < DURATION_POSITIONS - 1:
                        self._move_slider_right('duration')
                        time.sleep(0.3)

        except Exception as e:
            logger.error(f"Error scraping vehicle prices: {e}")

        return price_matrix

    def scrape_all(self) -> List[AyvensOffer]:
        """Scrape all Toyota offers with price matrices."""
        logger.info("Starting Ayvens Toyota private lease scrape")

        try:
            # Discover vehicles
            vehicles = self._discover_toyota_vehicles()

            if not vehicles:
                logger.warning("No Toyota vehicles found")
                return []

            offers = []

            for vehicle in tqdm(vehicles, desc="Ayvens Vehicles", unit="vehicle",
                               bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]'):
                logger.info(f"Processing: Toyota {vehicle['model']} ({vehicle.get('variant', '')[:50]}...)")

                # Scrape full price matrix by iterating through all slider combinations
                price_matrix = self._scrape_vehicle_prices(vehicle)

                logger.info(f"  Captured {len(price_matrix)} price points")

                variant_text = vehicle.get('variant', '')
                is_new = not self._is_used_car(variant_text)
                edition_name = self._extract_edition_name(variant_text)

                offer = AyvensOffer(
                    model=vehicle['model'],
                    variant=variant_text,
                    fuel_type=vehicle['fuel_type'],
                    transmission="Automatic",
                    vehicle_id=vehicle['vehicle_id'],
                    offer_url=vehicle['url'],
                    price_matrix=price_matrix,
                    is_new=is_new,
                    edition_name=edition_name
                )

                offers.append(offer)

            logger.info(f"Completed scraping {len(offers)} unique Toyota offers")
            return offers

        finally:
            self.close()


def load_progress(output_file: str = "output/ayvens_toyota_prices.json") -> Dict[str, dict]:
    """Load existing progress from JSON file."""
    if os.path.exists(output_file):
        try:
            with open(output_file, 'r') as f:
                data = json.load(f)
            return {item['vehicle_id']: item for item in data if 'vehicle_id' in item}
        except (json.JSONDecodeError, KeyError):
            return {}
    return {}


def save_progress(offers: List[AyvensOffer], output_file: str = "output/ayvens_toyota_prices.json"):
    """Save current progress to JSON file."""
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    output = [asdict(o) for o in offers]
    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2)


def main():
    """Main entry point."""
    output_file = "output/ayvens_toyota_prices.json"

    scraper = AyvensScraper(headless=True)

    try:
        offers = scraper.scrape_all()

        if offers:
            save_progress(offers, output_file)

            print("\n" + "="*60)
            print("Ayvens Toyota Private Lease Offers")
            print("="*60)

            for offer in offers:
                print(f"\nToyota {offer.model}")
                print(f"  ID: {offer.vehicle_id}")
                print(f"  Fuel: {offer.fuel_type}")
                print(f"  Prices found: {len(offer.price_matrix)}")

                for key, price in offer.price_matrix.items():
                    duration, km = key.split('_')
                    print(f"    {duration}mo/{km}km: €{price}/mo")

            print(f"\nSaved {len(offers)} offers to {output_file}")

    finally:
        scraper.close()


if __name__ == "__main__":
    main()
