#!/usr/bin/env python3
"""
Run new framework scrapers and save to cache format.

This script uses the new framework-native scrapers and saves the output
in the same format as the legacy scrapers for comparison.
"""

import json
import os
import logging
import sys
from datetime import datetime
from typing import List, Dict, Any

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.providers import ToyotaNLScraper, LeasysNLScraper, SuzukiNLScraper, AyvensNLScraper
from src.core.schema import LeaseOffer

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

OUTPUT_DIR = "output"


def offer_to_legacy_dict(offer: LeaseOffer) -> Dict[str, Any]:
    """Convert LeaseOffer to legacy format for comparison."""
    return {
        'brand': offer.brand,
        'model': offer.model,
        'edition_name': offer.edition_name,
        'variant': offer.variant,
        'fuel_type': offer.fuel_type.value if offer.fuel_type else 'unknown',
        'transmission': offer.transmission.value if offer.transmission else 'unknown',
        'power': offer.power,
        'price_matrix': offer.price_matrix.prices if offer.price_matrix else {},
        'source_url': offer.source_url,
    }


def save_cache(data: List[Dict[str, Any]], filename: str):
    """Save data to cache file."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2)
    logger.info(f"Saved {len(data)} items to {filepath}")


def run_toyota_scraper():
    """Run new Toyota scraper."""
    print("\n" + "="*60)
    print("Running ToyotaNLScraper (new framework)")
    print("="*60)

    try:
        scraper = ToyotaNLScraper(headless=True)
        offers = scraper.scrape_all()

        legacy_format = [offer_to_legacy_dict(o) for o in offers]
        save_cache(legacy_format, 'toyota_prices_new.json')

        print(f"Scraped {len(offers)} Toyota editions")
        return offers
    except Exception as e:
        logger.error(f"Toyota scraper error: {e}")
        raise


def run_leasys_scraper(brand: str = "Toyota"):
    """Run new Leasys scraper."""
    print("\n" + "="*60)
    print(f"Running LeasysNLScraper for {brand} (new framework)")
    print("="*60)

    try:
        scraper = LeasysNLScraper(headless=True, brand=brand)
        offers = scraper.scrape_all(brand=brand)

        legacy_format = [offer_to_legacy_dict(o) for o in offers]
        filename = f'leasys_{brand.lower()}_prices_new.json'
        save_cache(legacy_format, filename)

        print(f"Scraped {len(offers)} Leasys {brand} offers")
        return offers
    except Exception as e:
        logger.error(f"Leasys scraper error: {e}")
        raise


def run_suzuki_scraper():
    """Run new Suzuki scraper."""
    print("\n" + "="*60)
    print("Running SuzukiNLScraper (new framework)")
    print("="*60)

    try:
        scraper = SuzukiNLScraper(headless=True)
        offers = scraper.scrape_all()

        legacy_format = [offer_to_legacy_dict(o) for o in offers]
        save_cache(legacy_format, 'suzuki_prices_new.json')

        print(f"Scraped {len(offers)} Suzuki editions")
        return offers
    except Exception as e:
        logger.error(f"Suzuki scraper error: {e}")
        raise


def run_ayvens_scraper(brand: str = "Toyota"):
    """Run new Ayvens scraper."""
    print("\n" + "="*60)
    print(f"Running AyvensNLScraper for {brand} (new framework)")
    print("="*60)

    try:
        scraper = AyvensNLScraper(headless=True, brand=brand)
        offers = scraper.scrape_all(brand=brand)

        legacy_format = [offer_to_legacy_dict(o) for o in offers]
        filename = f'ayvens_{brand.lower()}_prices_new.json'
        save_cache(legacy_format, filename)

        print(f"Scraped {len(offers)} Ayvens {brand} offers")
        return offers
    except Exception as e:
        logger.error(f"Ayvens scraper error: {e}")
        raise


def main():
    """Run all scrapers."""
    import argparse

    parser = argparse.ArgumentParser(description="Run new framework scrapers")
    parser.add_argument('--provider', choices=['toyota', 'leasys', 'suzuki', 'ayvens', 'all'],
                       default='all', help='Which provider to scrape')
    parser.add_argument('--brand', default='Toyota', help='Brand for multi-brand scrapers')
    args = parser.parse_args()

    print("\n" + "="*60)
    print("NEW FRAMEWORK SCRAPER TEST")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)

    results = {}

    try:
        if args.provider in ['toyota', 'all']:
            results['toyota'] = run_toyota_scraper()

        if args.provider in ['leasys', 'all']:
            results[f'leasys_{args.brand.lower()}'] = run_leasys_scraper(args.brand)

        if args.provider in ['suzuki', 'all']:
            results['suzuki'] = run_suzuki_scraper()

        if args.provider in ['ayvens', 'all']:
            results[f'ayvens_{args.brand.lower()}'] = run_ayvens_scraper(args.brand)

        print("\n" + "="*60)
        print("SCRAPING COMPLETE")
        print("="*60)

        for key, offers in results.items():
            print(f"  {key}: {len(offers)} offers")

    except Exception as e:
        logger.error(f"Scraping failed: {e}")
        raise


if __name__ == "__main__":
    main()
