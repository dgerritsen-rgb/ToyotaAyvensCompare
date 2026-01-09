"""
Browser utilities for Selenium-based scraping.

This module provides a reusable WebDriver manager and common browser
operations used across all scrapers.
"""

import time
import logging
from typing import Optional, List, Callable, Any
from contextlib import contextmanager

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
)
from webdriver_manager.chrome import ChromeDriverManager

logger = logging.getLogger(__name__)


class BrowserManager:
    """
    Manages Selenium WebDriver lifecycle and common operations.

    Provides:
    - Lazy driver initialization
    - Configurable Chrome options
    - Rate limiting between requests
    - Page load waiting
    - Cookie consent handling
    - Context manager support
    """

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        headless: bool = True,
        request_delay: float = 2.0,
        user_agent: Optional[str] = None,
        window_size: tuple = (1920, 1080),
    ):
        """
        Initialize browser manager.

        Args:
            headless: Run browser in headless mode
            request_delay: Minimum seconds between requests
            user_agent: Custom user agent string
            window_size: Browser window dimensions (width, height)
        """
        self.headless = headless
        self.request_delay = request_delay
        self.user_agent = user_agent or self.DEFAULT_USER_AGENT
        self.window_size = window_size

        self._driver: Optional[webdriver.Chrome] = None
        self._last_request_time: float = 0

    @property
    def driver(self) -> webdriver.Chrome:
        """Lazy initialization of Selenium WebDriver."""
        if self._driver is None:
            self._driver = self._create_driver()
        return self._driver

    def _create_driver(self) -> webdriver.Chrome:
        """Create and configure Chrome WebDriver."""
        options = Options()

        if self.headless:
            options.add_argument('--headless')

        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument(f'--window-size={self.window_size[0]},{self.window_size[1]}')
        options.add_argument(f'--user-agent={self.user_agent}')

        # Reduce detection
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option('excludeSwitches', ['enable-automation'])
        options.add_experimental_option('useAutomationExtension', False)

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)

        # Additional anti-detection measures
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': '''
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                })
            '''
        })

        return driver

    def close(self):
        """Clean up WebDriver resources."""
        if self._driver:
            try:
                self._driver.quit()
            except Exception as e:
                logger.warning(f"Error closing driver: {e}")
            finally:
                self._driver = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures driver cleanup."""
        self.close()
        return False

    def rate_limit(self):
        """Ensure minimum delay between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self._last_request_time = time.time()

    def get(self, url: str, wait_for_load: bool = True) -> None:
        """
        Navigate to URL with rate limiting.

        Args:
            url: URL to navigate to
            wait_for_load: Whether to wait for page load completion
        """
        self.rate_limit()
        self.driver.get(url)
        if wait_for_load:
            self.wait_for_page_load()

    def wait_for_page_load(self, timeout: int = 15, extra_wait: float = 2.0):
        """
        Wait for page to be fully loaded.

        Args:
            timeout: Maximum seconds to wait for document.readyState
            extra_wait: Additional seconds to wait for JS rendering
        """
        try:
            WebDriverWait(self.driver, timeout).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            if extra_wait > 0:
                time.sleep(extra_wait)
        except TimeoutException:
            logger.warning(f"Page load timeout after {timeout}s")

    def wait_for_element(
        self,
        by: By,
        value: str,
        timeout: int = 10,
        condition: str = "presence"
    ) -> Optional[Any]:
        """
        Wait for element to appear.

        Args:
            by: Selenium By locator type
            value: Locator value
            timeout: Maximum seconds to wait
            condition: 'presence', 'visible', or 'clickable'

        Returns:
            WebElement if found, None otherwise
        """
        conditions = {
            "presence": EC.presence_of_element_located,
            "visible": EC.visibility_of_element_located,
            "clickable": EC.element_to_be_clickable,
        }

        try:
            wait_condition = conditions.get(condition, EC.presence_of_element_located)
            return WebDriverWait(self.driver, timeout).until(
                wait_condition((by, value))
            )
        except TimeoutException:
            return None

    def wait_for_elements(
        self,
        by: By,
        value: str,
        timeout: int = 10
    ) -> List[Any]:
        """
        Wait for multiple elements to appear.

        Args:
            by: Selenium By locator type
            value: Locator value
            timeout: Maximum seconds to wait

        Returns:
            List of WebElements (empty if timeout)
        """
        try:
            return WebDriverWait(self.driver, timeout).until(
                EC.presence_of_all_elements_located((by, value))
            )
        except TimeoutException:
            return []

    def safe_click(
        self,
        element,
        scroll_into_view: bool = True,
        use_js: bool = False,
        retries: int = 3
    ) -> bool:
        """
        Safely click an element with retry logic.

        Args:
            element: WebElement to click
            scroll_into_view: Scroll element into view first
            use_js: Use JavaScript click instead of native
            retries: Number of retry attempts

        Returns:
            True if click succeeded, False otherwise
        """
        for attempt in range(retries):
            try:
                if scroll_into_view:
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});",
                        element
                    )
                    time.sleep(0.3)

                if use_js:
                    self.driver.execute_script("arguments[0].click();", element)
                else:
                    element.click()
                return True

            except ElementClickInterceptedException:
                logger.debug(f"Click intercepted, attempt {attempt + 1}/{retries}")
                time.sleep(0.5)
            except StaleElementReferenceException:
                logger.debug(f"Stale element, attempt {attempt + 1}/{retries}")
                return False
            except Exception as e:
                logger.debug(f"Click failed: {e}, attempt {attempt + 1}/{retries}")
                time.sleep(0.5)

        return False

    def handle_cookie_consent(
        self,
        selectors: Optional[List[str]] = None,
        timeout: int = 5
    ) -> bool:
        """
        Try to dismiss cookie consent dialogs.

        Args:
            selectors: CSS selectors to try for accept buttons
            timeout: Max seconds to wait for cookie dialog

        Returns:
            True if cookie dialog was handled
        """
        default_selectors = [
            '[data-testid="cookie-accept"]',
            '[data-testid="cookie-accept-all"]',
            '#onetrust-accept-btn-handler',
            '.cookie-accept',
            '[class*="cookie"] button[class*="accept"]',
            'button[id*="accept"]',
            'button[class*="accept"]',
            '[aria-label*="Accept"]',
            '[aria-label*="accept"]',
        ]

        selectors = selectors or default_selectors

        for selector in selectors:
            try:
                element = WebDriverWait(self.driver, timeout / len(selectors)).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                )
                if self.safe_click(element, use_js=True):
                    logger.debug(f"Accepted cookies via: {selector}")
                    time.sleep(0.5)
                    return True
            except (TimeoutException, NoSuchElementException):
                continue

        return False

    def execute_script(self, script: str, *args) -> Any:
        """Execute JavaScript in the browser."""
        return self.driver.execute_script(script, *args)

    @property
    def page_source(self) -> str:
        """Get current page HTML source."""
        return self.driver.page_source

    @property
    def current_url(self) -> str:
        """Get current page URL."""
        return self.driver.current_url


@contextmanager
def browser_session(
    headless: bool = True,
    request_delay: float = 2.0,
    **kwargs
):
    """
    Context manager for browser sessions.

    Usage:
        with browser_session(headless=True) as browser:
            browser.get('https://example.com')
            html = browser.page_source
    """
    browser = BrowserManager(headless=headless, request_delay=request_delay, **kwargs)
    try:
        yield browser
    finally:
        browser.close()
