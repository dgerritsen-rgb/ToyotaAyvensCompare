"""Quick change detection using model/trim counts.

This module provides fast hash-based change detection by checking
trim counts from __NEXT_DATA__ instead of full page scraping.
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


@dataclass
class QuickCheckResult:
    """Result of a quick change check."""
    provider: str
    brand: str
    hash_current: str
    hash_cached: Optional[str]
    changed: bool
    counts: Dict[str, int]
    details: Dict[str, Dict]
    check_time: float


def get_leasys_model_counts(browser, brand: str, models: List[str]) -> Dict[str, Dict]:
    """Get trim counts for models from __NEXT_DATA__ (fast).

    Args:
        browser: BrowserManager instance
        brand: Brand name (e.g., 'suzuki')
        models: List of model slugs

    Returns:
        Dict mapping model slug to count info
    """
    results = {}

    for model_slug in models:
        url = f'https://store.leasys.com/nl/private/{brand.lower()}/{model_slug}'
        try:
            browser.get(url)
            time.sleep(1)  # Minimal wait

            soup = BeautifulSoup(browser.page_source, 'lxml')
            next_data = soup.find('script', id='__NEXT_DATA__')

            if not next_data:
                logger.warning(f"No __NEXT_DATA__ found for {brand}/{model_slug}")
                continue

            data = json.loads(next_data.string)
            props = data.get('props', {}).get('pageProps', {})
            config = props.get('initialOffer', {}).get('configurationOptions', {})

            trims = config.get('trims', [])
            engines = config.get('engines', [])
            colors = config.get('exteriorColours', [])

            results[model_slug] = {
                'trims': len(trims),
                'engines': len(engines),
                'colors': len(colors),
                'trim_names': [t.get('title', t.get('slug', '')) for t in trims],
            }

        except Exception as e:
            logger.error(f"Error checking {brand}/{model_slug}: {e}")

    return results


def compute_hash(counts: Dict[str, Dict]) -> str:
    """Compute hash from model counts."""
    # Create deterministic string from counts
    parts = []
    for model in sorted(counts.keys()):
        info = counts[model]
        parts.append(f"{model}:t{info['trims']}e{info['engines']}c{info['colors']}")

    count_str = '|'.join(parts)
    return hashlib.md5(count_str.encode()).hexdigest()[:16]


def load_cached_hash(provider: str, brand: str) -> Optional[Dict]:
    """Load cached quick check data."""
    cache_path = Path(f"output/quick_check/{provider}_{brand.lower()}.json")

    if cache_path.exists():
        try:
            with open(cache_path) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load quick check cache: {e}")

    return None


def save_quick_check(provider: str, brand: str, result: QuickCheckResult):
    """Save quick check result to cache."""
    cache_dir = Path("output/quick_check")
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_path = cache_dir / f"{provider}_{brand.lower()}.json"

    data = {
        'provider': result.provider,
        'brand': result.brand,
        'hash': result.hash_current,
        'counts': result.counts,
        'details': result.details,
        'checked_at': datetime.now().isoformat(),
    }

    with open(cache_path, 'w') as f:
        json.dump(data, f, indent=2)


def quick_check_leasys(browser, brand: str) -> QuickCheckResult:
    """Perform quick change check for Leasys brand.

    Args:
        browser: BrowserManager instance (already initialized)
        brand: Brand to check ('toyota' or 'suzuki')

    Returns:
        QuickCheckResult with change detection info
    """
    start_time = time.time()
    provider = 'leasys_nl'

    # Define models per brand
    brand_models = {
        'toyota': ['aygo-x', 'yaris', 'yaris-cross', 'corolla', 'corolla-cross',
                   'c-hr', 'rav4', 'highlander', 'bz4x', 'camry', 'land-cruiser'],
        'suzuki': ['swift', 'vitara', 's-cross', 'swace', 'across', 'e-vitara'],
    }

    models = brand_models.get(brand.lower(), [])
    if not models:
        raise ValueError(f"Unknown brand: {brand}")

    # Get current counts
    logger.info(f"Quick check: {brand} ({len(models)} models)")
    details = get_leasys_model_counts(browser, brand, models)

    # Compute hash
    current_hash = compute_hash(details)

    # Load cached hash
    cached = load_cached_hash(provider, brand)
    cached_hash = cached.get('hash') if cached else None

    # Determine if changed
    changed = cached_hash is None or current_hash != cached_hash

    # Build counts summary
    counts = {model: info['trims'] for model, info in details.items()}

    result = QuickCheckResult(
        provider=provider,
        brand=brand,
        hash_current=current_hash,
        hash_cached=cached_hash,
        changed=changed,
        counts=counts,
        details=details,
        check_time=time.time() - start_time,
    )

    # Save new hash
    save_quick_check(provider, brand, result)

    return result


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from src.core.browser import BrowserManager

    logging.basicConfig(level=logging.INFO)

    browser = BrowserManager(headless=True)
    browser.get('https://store.leasys.com')  # Initialize
    browser.handle_cookie_consent()

    print("\n=== Quick Check: Leasys Suzuki ===\n")
    result = quick_check_leasys(browser, 'suzuki')

    print(f"Hash (current):  {result.hash_current}")
    print(f"Hash (cached):   {result.hash_cached or 'none'}")
    print(f"Changed:         {result.changed}")
    print(f"Check time:      {result.check_time:.1f}s")
    print(f"\nModel trim counts:")
    for model, count in sorted(result.counts.items()):
        print(f"  {model}: {count} trims")

    browser.driver.quit()
