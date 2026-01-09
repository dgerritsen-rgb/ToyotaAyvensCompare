"""Robots.txt compliance checking utility.

This module provides utilities to check robots.txt rules before scraping.
"""

import logging
from functools import lru_cache
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

logger = logging.getLogger(__name__)

# Default user agent for scraping
DEFAULT_USER_AGENT = "ToyAyPriceCompare/1.0"


@lru_cache(maxsize=32)
def get_robots_parser(base_url: str) -> RobotFileParser | None:
    """Fetch and parse robots.txt for a domain.

    Args:
        base_url: Base URL of the website (e.g., https://www.toyota.nl)

    Returns:
        RobotFileParser instance or None if robots.txt couldn't be fetched
    """
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    try:
        response = requests.get(robots_url, timeout=10)
        if response.status_code == 200:
            parser = RobotFileParser()
            parser.parse(response.text.splitlines())
            logger.debug(f"Successfully parsed robots.txt from {robots_url}")
            return parser
        elif response.status_code == 404:
            # No robots.txt means everything is allowed
            logger.debug(f"No robots.txt found at {robots_url} - all paths allowed")
            return None
        else:
            logger.warning(f"Unexpected status {response.status_code} from {robots_url}")
            return None
    except requests.RequestException as e:
        logger.warning(f"Failed to fetch robots.txt from {robots_url}: {e}")
        return None


def can_fetch(url: str, user_agent: str = DEFAULT_USER_AGENT) -> bool:
    """Check if a URL can be fetched according to robots.txt rules.

    Args:
        url: Full URL to check
        user_agent: User agent string to check against

    Returns:
        True if the URL can be fetched, False otherwise
    """
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    parser = get_robots_parser(base_url)

    if parser is None:
        # No robots.txt or couldn't fetch - assume allowed
        return True

    return parser.can_fetch(user_agent, url)


def check_provider_compliance(base_url: str, paths: list[str] | None = None) -> dict:
    """Check robots.txt compliance for a provider.

    Args:
        base_url: Base URL of the provider website
        paths: Optional list of paths to check (e.g., ["/private-lease", "/api"])

    Returns:
        Dictionary with compliance information
    """
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    result = {
        "base_url": base_url,
        "robots_url": robots_url,
        "robots_exists": False,
        "can_scrape": True,
        "paths_checked": {},
        "crawl_delay": None,
        "sitemaps": [],
    }

    try:
        response = requests.get(robots_url, timeout=10)
        if response.status_code == 200:
            result["robots_exists"] = True
            result["robots_content"] = response.text[:1000]  # First 1000 chars

            parser = RobotFileParser()
            parser.parse(response.text.splitlines())

            # Check crawl delay
            try:
                result["crawl_delay"] = parser.crawl_delay(DEFAULT_USER_AGENT)
            except AttributeError:
                pass

            # Check specific paths
            if paths:
                for path in paths:
                    full_url = f"{base_url.rstrip('/')}{path}"
                    allowed = parser.can_fetch(DEFAULT_USER_AGENT, full_url)
                    result["paths_checked"][path] = allowed
                    if not allowed:
                        result["can_scrape"] = False

            # Extract sitemaps
            for line in response.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    result["sitemaps"].append(line.split(":", 1)[1].strip())

        elif response.status_code == 404:
            result["can_scrape"] = True  # No robots.txt means allowed
        else:
            result["error"] = f"HTTP {response.status_code}"

    except requests.RequestException as e:
        result["error"] = str(e)

    return result


def verify_all_providers() -> dict:
    """Check robots.txt compliance for all known providers.

    Returns:
        Dictionary mapping provider names to compliance results
    """
    providers = {
        "toyota_nl": {
            "base_url": "https://www.toyota.nl",
            "paths": ["/private-lease", "/private-lease/modellen"],
        },
        "suzuki_nl": {
            "base_url": "https://www.suzuki.nl",
            "paths": ["/private-lease"],
        },
        "ayvens_nl": {
            "base_url": "https://www.ayvens.com",
            "paths": ["/nl-nl/zakelijk/auto-configurator"],
        },
        "leasys_nl": {
            "base_url": "https://store.leasys.com",
            "paths": ["/nl-nl/toyota", "/nl-nl/suzuki"],
        },
    }

    results = {}
    for name, config in providers.items():
        results[name] = check_provider_compliance(
            config["base_url"],
            config["paths"]
        )
        logger.info(f"{name}: can_scrape={results[name]['can_scrape']}")

    return results


if __name__ == "__main__":
    # Quick test
    logging.basicConfig(level=logging.INFO)

    print("\n=== Robots.txt Compliance Check ===\n")
    results = verify_all_providers()

    for provider, info in results.items():
        status = "ALLOWED" if info["can_scrape"] else "BLOCKED"
        print(f"{provider}: {status}")
        if info.get("crawl_delay"):
            print(f"  Crawl delay: {info['crawl_delay']}s")
        if info.get("paths_checked"):
            for path, allowed in info["paths_checked"].items():
                print(f"  {path}: {'allowed' if allowed else 'BLOCKED'}")
        print()
