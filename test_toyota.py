#!/usr/bin/env python3
"""Quick test for Toyota scraper - discover editions and test one price extraction."""

import logging
from toyota_scraper import ToyotaScraper, DURATIONS, MILEAGES

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def test_discovery():
    """Test edition discovery and one price extraction."""
    scraper = ToyotaScraper(headless=True)

    try:
        # Test discovery
        editions = scraper._discover_editions()

        print(f"\nDiscovered {len(editions)} editions:")
        for edition in editions:
            print(f"  - {edition.model}: {edition.edition_slug[:50]}...")

        # Test price extraction for first edition
        if editions:
            print(f"\nTesting price extraction for: {editions[0].edition_name}")

            # Try one duration/km combination
            price = scraper._scrape_price_for_combination(
                editions[0].edition_slug,
                DURATIONS[2],  # 48 months
                MILEAGES[1]    # 10000 km
            )

            if price:
                print(f"  Price: €{price}/mo for 48mo/10000km")
            else:
                print("  No price found - may need different approach")

    finally:
        scraper.close()


if __name__ == '__main__':
    test_discovery()
