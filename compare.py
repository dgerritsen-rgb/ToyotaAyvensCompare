#!/usr/bin/env python3
"""
Toyota Private Lease Price Comparison Tool

Compares Toyota.nl private lease prices with Ayvens and Leasys Toyota prices.
Matches models and generates a detailed comparison report.
"""

import json
import os
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import pandas as pd

from toyota_scraper import ToyotaScraper, ToyotaEdition, DURATIONS, MILEAGES
from ayvens_scraper import AyvensScraper, AyvensOffer
from leasys_scraper import LeasysScraper, LeasysOffer


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class PriceComparison:
    """Price comparison between Toyota.nl, Ayvens, and Leasys."""
    model: str
    toyota_variant: str
    ayvens_variant: str
    leasys_variant: str
    duration: int
    km_per_year: int
    toyota_price: Optional[float]
    ayvens_price: Optional[float]
    leasys_price: Optional[float]
    toyota_url: Optional[str] = None
    ayvens_url: Optional[str] = None
    leasys_url: Optional[str] = None

    @property
    def cheapest_supplier(self) -> Optional[str]:
        """Which supplier has the lowest price."""
        prices = []
        if self.toyota_price:
            prices.append(('Toyota', self.toyota_price))
        if self.ayvens_price:
            prices.append(('Ayvens', self.ayvens_price))
        if self.leasys_price:
            prices.append(('Leasys', self.leasys_price))

        if not prices:
            return None

        # Find the minimum
        cheapest = min(prices, key=lambda x: x[1])
        return cheapest[0]

    @property
    def price_spread(self) -> Optional[float]:
        """Difference between highest and lowest price."""
        prices = [p for p in [self.toyota_price, self.ayvens_price, self.leasys_price] if p]
        if len(prices) < 2:
            return None
        return max(prices) - min(prices)


class ModelMatcher:
    """Matches Toyota models between Toyota.nl and Ayvens."""

    # Model name mappings (Toyota.nl name -> Ayvens patterns)
    MODEL_ALIASES = {
        'aygo x': ['aygo x', 'aygo-x', 'aygox'],
        'yaris': ['yaris'],
        'yaris cross': ['yaris cross', 'yaris-cross'],
        'urban cruiser': ['urban cruiser', 'urban-cruiser'],
        'corolla': ['corolla'],
        'corolla hatchback': ['corolla', 'corolla hatchback'],
        'corolla touring sports': ['corolla touring', 'corolla ts', 'corolla touring sports'],
        'corolla cross': ['corolla cross', 'corolla-cross'],
        'c-hr': ['c-hr', 'chr'],
        'rav4': ['rav4', 'rav-4'],
        'bz4x': ['bz4x', 'bz-4x'],
        'land cruiser': ['land cruiser', 'landcruiser'],
    }

    # Edition name mappings (Toyota.nl edition -> Ayvens patterns)
    EDITION_ALIASES = {
        'active': ['active'],
        'comfort': ['comfort'],
        'dynamic': ['dynamic'],
        'executive': ['executive'],
        'gr-sport': ['gr-sport', 'gr sport', 'grsport'],
        'style': ['style'],
        'first edition': ['first edition', 'first'],
        'premium': ['premium'],
        'lounge': ['lounge'],
    }

    @classmethod
    def normalize_model(cls, model: str) -> str:
        """Normalize model name for matching."""
        return model.lower().strip().replace('-', ' ')

    @classmethod
    def normalize_edition(cls, edition: str) -> str:
        """Normalize edition name for matching."""
        return edition.lower().strip().replace('-', ' ')

    @classmethod
    def is_valid_edition_name(cls, edition: str) -> bool:
        """Check if edition name is valid (not a price, empty, or generic placeholder)."""
        if not edition:
            return False
        import re
        # Skip if it looks like a price
        price_patterns = [r'€', r'\d+,-', r'\d+,\d{2}', r'vanaf', r'per maand', r'p/m']
        for pattern in price_patterns:
            if re.search(pattern, edition, re.IGNORECASE):
                return False
        # Skip if it's a generic numbered edition (e.g., "Edition 1", "Edition 2")
        if re.match(r'^Edition\s*\d+$', edition, re.IGNORECASE):
            return False
        return True

    @classmethod
    def extract_edition(cls, variant: str) -> str:
        """Extract edition name from variant string."""
        import re
        variant_lower = variant.lower()

        # Look for known edition names
        for edition, aliases in cls.EDITION_ALIASES.items():
            for alias in aliases:
                if alias in variant_lower:
                    return edition

        # Try to extract from patterns like "1.5 Hybrid Active" or "140 Active"
        patterns = [
            r'\b(active|comfort|dynamic|executive|gr[ -]?sport|style|first|premium|lounge)\b',
        ]
        for pattern in patterns:
            match = re.search(pattern, variant_lower)
            if match:
                edition = match.group(1).replace(' ', '-')
                if edition.startswith('gr'):
                    return 'gr-sport'
                return edition

        return ""

    @classmethod
    def models_match(cls, toyota_model: str, ayvens_model: str) -> bool:
        """Check if two model names match."""
        toyota_norm = cls.normalize_model(toyota_model)
        ayvens_norm = cls.normalize_model(ayvens_model)

        # Direct match
        if toyota_norm == ayvens_norm:
            return True

        # Check aliases
        for base_model, aliases in cls.MODEL_ALIASES.items():
            if toyota_norm in aliases or toyota_norm == base_model:
                if ayvens_norm in aliases or ayvens_norm == base_model:
                    return True

        # Partial match (one contains the other)
        if toyota_norm in ayvens_norm or ayvens_norm in toyota_norm:
            return True

        return False

    @classmethod
    def editions_match(cls, toyota_edition: str, ayvens_edition: str) -> bool:
        """Check if two edition names match."""
        toyota_norm = cls.normalize_edition(toyota_edition)
        ayvens_norm = cls.normalize_edition(ayvens_edition)

        # Empty matches empty
        if not toyota_norm and not ayvens_norm:
            return True

        # Direct match
        if toyota_norm == ayvens_norm:
            return True

        # Check aliases
        for base_edition, aliases in cls.EDITION_ALIASES.items():
            if toyota_norm in aliases or toyota_norm == base_edition:
                if ayvens_norm in aliases or ayvens_norm == base_edition:
                    return True

        return False

    @classmethod
    def is_used_car(cls, variant: str) -> bool:
        """Detect if a vehicle is used based on variant text."""
        variant_lower = variant.lower()
        used_indicators = [
            'kilometerstand',
            '1e tenaamstelling',
            'bouwjaar',
        ]
        return any(indicator in variant_lower for indicator in used_indicators)


def load_cached_data() -> Tuple[Optional[List[dict]], Optional[List[dict]], Optional[List[dict]]]:
    """Load cached price data if available."""
    toyota_data = None
    ayvens_data = None
    leasys_data = None

    toyota_path = "output/toyota_prices.json"
    ayvens_path = "output/ayvens_toyota_prices.json"
    leasys_path = "output/leasys_toyota_prices.json"

    if os.path.exists(toyota_path):
        with open(toyota_path, 'r') as f:
            toyota_data = json.load(f)
        logger.info(f"Loaded {len(toyota_data)} Toyota editions from cache")

    if os.path.exists(ayvens_path):
        with open(ayvens_path, 'r') as f:
            ayvens_data = json.load(f)
        logger.info(f"Loaded {len(ayvens_data)} Ayvens offers from cache")

    if os.path.exists(leasys_path):
        with open(leasys_path, 'r') as f:
            leasys_data = json.load(f)
        logger.info(f"Loaded {len(leasys_data)} Leasys offers from cache")

    return toyota_data, ayvens_data, leasys_data


def scrape_fresh_data(use_cache: bool = False) -> Tuple[List[ToyotaEdition], List[AyvensOffer], List[LeasysOffer]]:
    """Scrape data from all sites.

    Args:
        use_cache: If True, use smart caching for Toyota (check overview prices first)
    """
    print("\n" + "="*70)
    print("TOYOTA PRIVATE LEASE PRICE COMPARISON - DATA COLLECTION")
    print("="*70)

    # Scrape Toyota (with optional smart caching)
    print("\n>>> PHASE 1: SCRAPING TOYOTA.NL <<<\n")
    toyota_scraper = ToyotaScraper(headless=True)
    toyota_editions = toyota_scraper.scrape_all(use_cache=use_cache)
    print(f"\nToyota scraping complete: {len(toyota_editions)} editions\n")

    # Scrape Ayvens
    print("\n>>> PHASE 2: SCRAPING AYVENS <<<\n")
    ayvens_scraper = AyvensScraper(headless=True)
    ayvens_offers = ayvens_scraper.scrape_all()
    print(f"\nAyvens scraping complete: {len(ayvens_offers)} offers\n")

    # Scrape Leasys
    print("\n>>> PHASE 3: SCRAPING LEASYS <<<\n")
    leasys_scraper = LeasysScraper(headless=True)
    leasys_offers = leasys_scraper.scrape_all()
    print(f"\nLeasys scraping complete: {len(leasys_offers)} offers\n")

    print("\n>>> DATA COLLECTION COMPLETE <<<")
    print("="*70 + "\n")

    return toyota_editions, ayvens_offers, leasys_offers


def match_editions(
    toyota_editions: List[dict],
    ayvens_offers: List[dict],
    leasys_offers: List[dict],
    exclude_used: bool = True
) -> List[Tuple[dict, Optional[dict], Optional[dict]]]:
    """Match Toyota editions with Ayvens and Leasys offers.

    Args:
        toyota_editions: List of Toyota editions (primary source)
        ayvens_offers: List of Ayvens offers
        leasys_offers: List of Leasys offers
        exclude_used: If True, exclude vehicles that are clearly used

    Returns:
        List of tuples: (toyota_edition, ayvens_match_or_none, leasys_match_or_none)
    """
    matches = []

    # Filter out used cars from Ayvens
    if exclude_used:
        filtered_ayvens = []
        for ayvens in ayvens_offers:
            variant = ayvens.get('variant', '')
            if ModelMatcher.is_used_car(variant):
                logger.debug(f"Excluding used car: {variant[:60]}...")
                continue
            filtered_ayvens.append(ayvens)
        logger.info(f"Filtered to {len(filtered_ayvens)} Ayvens vehicles (excluded {len(ayvens_offers) - len(filtered_ayvens)} used cars)")
        ayvens_offers = filtered_ayvens

    # Group suppliers by model
    ayvens_by_model = {}
    for a in ayvens_offers:
        model = ModelMatcher.normalize_model(a.get('model', ''))
        if model not in ayvens_by_model:
            ayvens_by_model[model] = []
        ayvens_by_model[model].append(a)

    leasys_by_model = {}
    for l in leasys_offers:
        model = ModelMatcher.normalize_model(l.get('model', ''))
        if model not in leasys_by_model:
            leasys_by_model[model] = []
        leasys_by_model[model].append(l)

    # Track matched offers to avoid duplicates
    matched_ayvens_ids = set()
    matched_leasys_ids = set()

    for toyota in toyota_editions:
        toyota_model = toyota.get('model', '')
        toyota_edition = toyota.get('edition_name', '')
        toyota_edition_norm = ModelMatcher.normalize_edition(toyota_edition)
        toyota_edition_valid = ModelMatcher.is_valid_edition_name(toyota_edition)

        # Find matching Ayvens offers
        matching_ayvens = []
        for ayvens_model_norm, ayvens_list in ayvens_by_model.items():
            if ModelMatcher.models_match(toyota_model, ayvens_list[0].get('model', '')):
                matching_ayvens.extend(ayvens_list)

        # Find matching Leasys offers
        matching_leasys = []
        for leasys_model_norm, leasys_list in leasys_by_model.items():
            if ModelMatcher.models_match(toyota_model, leasys_list[0].get('model', '')):
                matching_leasys.extend(leasys_list)

        # Find best Ayvens match
        ayvens_match = None
        for ayvens in matching_ayvens:
            ayvens_id = ayvens.get('vehicle_id', id(ayvens))
            if ayvens_id in matched_ayvens_ids:
                continue

            ayvens_variant = ayvens.get('variant', '')
            ayvens_edition = ayvens.get('edition_name', '') or ModelMatcher.extract_edition(ayvens_variant)
            ayvens_edition_valid = ModelMatcher.is_valid_edition_name(ayvens_edition)

            if toyota_edition_valid and ayvens_edition_valid:
                if ModelMatcher.editions_match(toyota_edition, ayvens_edition):
                    ayvens_match = ayvens
                    break
            elif ayvens_edition_valid:
                ayvens_match = ayvens
                break
            elif not ayvens_match:
                ayvens_match = ayvens

        if ayvens_match:
            matched_ayvens_ids.add(ayvens_match.get('vehicle_id', id(ayvens_match)))

        # Find best Leasys match
        leasys_match = None
        for leasys in matching_leasys:
            leasys_id = leasys.get('offer_url', id(leasys))
            if leasys_id in matched_leasys_ids:
                continue

            leasys_edition = leasys.get('edition_name', '') or leasys.get('variant', '')
            leasys_edition_valid = ModelMatcher.is_valid_edition_name(leasys_edition)

            if toyota_edition_valid and leasys_edition_valid:
                if ModelMatcher.editions_match(toyota_edition, leasys_edition):
                    leasys_match = leasys
                    break
            elif leasys_edition_valid:
                leasys_match = leasys
                break
            elif not leasys_match:
                leasys_match = leasys

        if leasys_match:
            matched_leasys_ids.add(leasys_match.get('offer_url', id(leasys_match)))

        # Only add if at least one supplier match
        if ayvens_match or leasys_match:
            matches.append((toyota, ayvens_match, leasys_match))

    logger.info(f"Found {len(matches)} Toyota editions with supplier matches")
    logger.info(f"  - {sum(1 for _, a, _ in matches if a)} with Ayvens match")
    logger.info(f"  - {sum(1 for _, _, l in matches if l)} with Leasys match")
    return matches


def is_valid_price(price: Optional[float]) -> bool:
    """Check if a price is valid (within reasonable range for private lease)."""
    if price is None:
        return False
    # Private lease prices typically range from €150-€2000/month
    return 100 <= price <= 2000


def extract_toyota_display_name(toyota: dict) -> str:
    """Extract a clean display name for Toyota variant from edition_slug.

    Converts slugs like 'toyota-yaris-cross-toyota-yaris-cross-hybrid-115-active-automaat-1'
    to 'Hybrid 115 Active'
    """
    import re
    slug = toyota.get('edition_slug', '')

    # Try to extract from slug (e.g., "hybrid-115-active-automaat" or "hybrid-140-gr-sport")
    # Look for pattern: hybrid-{power}-{edition}-automaat (edition can be multi-word like gr-sport)
    match = re.search(r'(hybrid|electric)-(\d+)-([\w-]+?)(?:-automaat)?(?:-\d)?$', slug, re.IGNORECASE)
    if match:
        fuel = match.group(1).title()
        power = match.group(2)
        edition = match.group(3).replace('-', ' ').title()
        # Normalize GR Sport to GR-Sport
        if 'Gr Sport' in edition:
            edition = edition.replace('Gr Sport', 'GR-Sport')
        # Remove trailing "Automaat" if present
        edition = re.sub(r'\s*Automaat\s*$', '', edition, flags=re.IGNORECASE)
        return f"{fuel} {power} {edition}"

    # Fallback to edition_name if extraction fails
    edition_name = toyota.get('edition_name', '')
    if edition_name and not edition_name.startswith('Edition '):
        return edition_name

    return slug.replace('-', ' ').title() if slug else ''


def extract_ayvens_display_name(ayvens: dict) -> str:
    """Extract a clean display name for Ayvens variant.

    Converts variants like '140 Active 5d Hybrid 140 Active 5d...'
    to 'Hybrid 140 Active 5d'
    """
    import re
    variant = ayvens.get('variant', '')

    # Ayvens variant format is typically: "{power} {edition} {doors}d Hybrid..."
    # where edition can be multi-word like "GR-Sport"

    # Try pattern: "Hybrid {power} {edition} {doors}d" - edition can include hyphens
    match = re.search(r'Hybrid\s+(\d+)\s+([\w-]+)\s+(?:Automaat\s+)?(\d)d', variant, re.IGNORECASE)
    if match:
        power = match.group(1)
        edition = match.group(2)
        # Normalize GR-Sport
        if edition.lower().startswith('gr'):
            edition = 'GR-Sport'
        else:
            edition = edition.title()
        doors = match.group(3)
        return f"Hybrid {power} {edition} {doors}d"

    # Try pattern at start: "{power} {edition} {doors}d Hybrid" - edition can include hyphens
    match = re.search(r'^(\d+)\s+([\w-]+)\s+(\d)d\s+Hybrid', variant, re.IGNORECASE)
    if match:
        power = match.group(1)
        edition = match.group(2)
        # Normalize GR-Sport
        if edition.lower().startswith('gr'):
            edition = 'GR-Sport'
        else:
            edition = edition.title()
        doors = match.group(3)
        return f"Hybrid {power} {edition} {doors}d"

    # Fallback: just return the first meaningful part
    parts = variant.split()
    if len(parts) >= 4:
        # Take first 4 words
        result = ' '.join(parts[:4])
        if 'Hybrid' not in result:
            result = 'Hybrid ' + result
        return result

    return variant[:50] if variant else ''


def extract_leasys_display_name(leasys: dict) -> str:
    """Extract a clean display name for Leasys variant."""
    if not leasys:
        return ''

    edition = leasys.get('edition_name', '') or leasys.get('variant', '')
    model = leasys.get('model', '')

    # Capitalize edition name properly
    if edition:
        # Handle special cases like "Gr Sport" -> "GR-Sport"
        if edition.lower().startswith('gr'):
            edition = 'GR-Sport'
        else:
            edition = edition.title()

    return edition if edition else model


def compare_prices(matches: List[Tuple[dict, Optional[dict], Optional[dict]]]) -> List[PriceComparison]:
    """Generate price comparisons for all matched models."""
    comparisons = []

    # Leasys only supports mileages up to 20000 km
    LEASYS_MILEAGES = [5000, 10000, 15000, 20000]

    for toyota, ayvens, leasys in matches:
        toyota_prices = toyota.get('price_matrix', {})
        ayvens_prices = ayvens.get('price_matrix', {}) if ayvens else {}
        leasys_prices = leasys.get('price_matrix', {}) if leasys else {}

        # Get URLs for this edition
        toyota_url = toyota.get('configurator_url', '')
        if not toyota_url:
            model_slug = toyota.get('model', '').lower().replace(' ', '-')
            toyota_url = f"https://www.toyota.nl/private-lease/modellen#?model[]={model_slug}&durationMonths=72&yearlyKilometers=5000"
        ayvens_url = ayvens.get('offer_url', '') if ayvens else ''
        leasys_url = leasys.get('offer_url', '') if leasys else ''

        for duration in DURATIONS:
            for km in MILEAGES:
                key = f"{duration}_{km}"

                toyota_price = toyota_prices.get(key)
                ayvens_price = ayvens_prices.get(key)
                # Leasys only has prices for km <= 20000
                leasys_price = leasys_prices.get(key) if km in LEASYS_MILEAGES else None

                # Filter out invalid prices
                if not is_valid_price(toyota_price):
                    toyota_price = None
                if not is_valid_price(ayvens_price):
                    ayvens_price = None
                if not is_valid_price(leasys_price):
                    leasys_price = None

                comparison = PriceComparison(
                    model=toyota.get('model', 'Unknown'),
                    toyota_variant=extract_toyota_display_name(toyota),
                    ayvens_variant=extract_ayvens_display_name(ayvens) if ayvens else '',
                    leasys_variant=extract_leasys_display_name(leasys) if leasys else '',
                    duration=duration,
                    km_per_year=km,
                    toyota_price=toyota_price,
                    ayvens_price=ayvens_price,
                    leasys_price=leasys_price,
                    toyota_url=toyota_url,
                    ayvens_url=ayvens_url,
                    leasys_url=leasys_url,
                )
                comparisons.append(comparison)

    return comparisons


def generate_report(comparisons: List[PriceComparison]) -> str:
    """Generate a text report of the price comparison."""
    report_lines = [
        "=" * 100,
        "TOYOTA PRIVATE LEASE PRICE COMPARISON: TOYOTA.NL vs AYVENS vs LEASYS",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "=" * 100,
        "",
        "NOTE: Please verify matches using the URLs below. Toyota editions are matched",
        "      with supplier editions by model - verify edition names match at each URL.",
        "      Leasys only offers mileages up to 20,000 km/year.",
        "",
    ]

    # Summary statistics
    valid_comparisons = [c for c in comparisons if c.toyota_price and (c.ayvens_price or c.leasys_price)]

    if valid_comparisons:
        toyota_cheapest = sum(1 for c in valid_comparisons if c.cheapest_supplier == 'Toyota')
        ayvens_cheapest = sum(1 for c in valid_comparisons if c.cheapest_supplier == 'Ayvens')
        leasys_cheapest = sum(1 for c in valid_comparisons if c.cheapest_supplier == 'Leasys')

        spreads = [c.price_spread for c in valid_comparisons if c.price_spread]
        avg_spread = sum(spreads) / len(spreads) if spreads else 0
        max_spread = max(spreads) if spreads else 0

        report_lines.extend([
            "OVERALL SUMMARY",
            "-" * 100,
            f"Total price points compared: {len(valid_comparisons)}",
            f"Toyota.nl cheapest: {toyota_cheapest} ({100*toyota_cheapest/len(valid_comparisons):.1f}%)",
            f"Ayvens cheapest: {ayvens_cheapest} ({100*ayvens_cheapest/len(valid_comparisons):.1f}%)",
            f"Leasys cheapest: {leasys_cheapest} ({100*leasys_cheapest/len(valid_comparisons):.1f}%)",
            f"Average price spread: €{avg_spread:.0f}/mo",
            f"Maximum price spread: €{max_spread:.0f}/mo",
            "",
        ])

    # Group by model and edition
    model_editions = {}
    edition_urls = {}
    for c in comparisons:
        # Only include if Toyota price and at least one supplier price
        if not c.toyota_price:
            continue
        if not (c.ayvens_price or c.leasys_price):
            continue

        key = (c.model, c.toyota_variant, c.ayvens_variant, c.leasys_variant)
        if key not in model_editions:
            model_editions[key] = []
            edition_urls[key] = (c.toyota_url, c.ayvens_url, c.leasys_url)
        model_editions[key].append(c)

    # Sort by model name
    sorted_keys = sorted(model_editions.keys(), key=lambda x: (x[0], x[1]))

    current_model = None
    for (model, toyota_variant, ayvens_variant, leasys_variant), edition_comparisons in [(k, model_editions[k]) for k in sorted_keys]:
        if not edition_comparisons:
            continue

        # Model header
        if model != current_model:
            current_model = model
            report_lines.extend([
                "",
                "=" * 100,
                f"MODEL: {model.upper()}",
                "=" * 100,
            ])

        # Determine display edition name
        display_variant = ayvens_variant or leasys_variant or toyota_variant
        ayvens_edition = ModelMatcher.extract_edition(display_variant)
        if ayvens_edition:
            display_variant = ayvens_edition

        # Get URLs
        toyota_url, ayvens_url, leasys_url = edition_urls.get((model, toyota_variant, ayvens_variant, leasys_variant), ('', '', ''))

        # Edition header with URLs
        report_lines.extend([
            "",
            f"  Edition: {display_variant}",
            f"  Toyota variant: {toyota_variant}",
        ])
        if ayvens_variant:
            report_lines.append(f"  Ayvens variant: {ayvens_variant}")
        if leasys_variant:
            report_lines.append(f"  Leasys variant: {leasys_variant}")
        report_lines.append("")
        report_lines.append(f"  Toyota URL: {toyota_url}" if toyota_url else "  Toyota URL: N/A")
        if ayvens_variant:
            report_lines.append(f"  Ayvens URL: {ayvens_url}" if ayvens_url else "  Ayvens URL: N/A")
        if leasys_variant:
            report_lines.append(f"  Leasys URL: {leasys_url}" if leasys_url else "  Leasys URL: N/A")
        report_lines.append("")

        # Price comparison table
        header = f"    {'Duration':<8} {'KM/Year':<10} {'Toyota':<10} {'Ayvens':<10} {'Leasys':<10} {'Spread':<10} {'Cheapest':<10}"
        report_lines.append(header)
        report_lines.append("    " + "-" * 78)

        for c in edition_comparisons:
            toyota_str = f"€{c.toyota_price:.0f}" if c.toyota_price else "N/A"
            ayvens_str = f"€{c.ayvens_price:.0f}" if c.ayvens_price else "N/A"
            leasys_str = f"€{c.leasys_price:.0f}" if c.leasys_price else "N/A"
            spread_str = f"€{c.price_spread:.0f}" if c.price_spread else "N/A"
            cheapest = c.cheapest_supplier or "N/A"

            report_lines.append(
                f"    {c.duration:<8} {c.km_per_year:<10} {toyota_str:<10} {ayvens_str:<10} {leasys_str:<10} {spread_str:<10} {cheapest:<10}"
            )

        # Edition summary
        edition_spreads = [c.price_spread for c in edition_comparisons if c.price_spread]
        if edition_spreads:
            avg_spread = sum(edition_spreads) / len(edition_spreads)
            toyota_wins = sum(1 for c in edition_comparisons if c.cheapest_supplier == 'Toyota')
            ayvens_wins = sum(1 for c in edition_comparisons if c.cheapest_supplier == 'Ayvens')
            leasys_wins = sum(1 for c in edition_comparisons if c.cheapest_supplier == 'Leasys')
            report_lines.append("")
            report_lines.append(f"    Summary: Avg spread €{avg_spread:.0f}/mo | Cheapest: Toyota {toyota_wins}x, Ayvens {ayvens_wins}x, Leasys {leasys_wins}x")

    report_lines.extend([
        "",
        "=" * 100,
        "LEGEND:",
        "  - Spread = difference between highest and lowest price",
        "  - Cheapest = supplier with lowest price for that configuration",
        "  - N/A for Leasys at 25000/30000 km = Leasys doesn't offer these mileages",
        "=" * 100,
        "END OF REPORT",
        "=" * 100,
    ])

    return "\n".join(report_lines)


def generate_csv(comparisons: List[PriceComparison], filename: str):
    """Generate CSV file with comparison data."""
    data = []
    for c in comparisons:
        data.append({
            'model': c.model,
            'toyota_variant': c.toyota_variant,
            'ayvens_variant': c.ayvens_variant,
            'leasys_variant': c.leasys_variant,
            'duration_months': c.duration,
            'km_per_year': c.km_per_year,
            'toyota_price': c.toyota_price,
            'ayvens_price': c.ayvens_price,
            'leasys_price': c.leasys_price,
            'price_spread': c.price_spread,
            'cheapest_supplier': c.cheapest_supplier,
            'toyota_url': c.toyota_url,
            'ayvens_url': c.ayvens_url,
            'leasys_url': c.leasys_url,
        })

    df = pd.DataFrame(data)
    df.to_csv(filename, index=False)
    logger.info(f"Saved comparison to {filename}")


def main(use_cache: bool = True, scrape_fresh: bool = False):
    """Main comparison function."""
    os.makedirs("output", exist_ok=True)

    toyota_data = None
    ayvens_data = None
    leasys_data = None

    # Load or scrape data
    if use_cache and not scrape_fresh:
        toyota_data, ayvens_data, leasys_data = load_cached_data()

    if toyota_data is None or ayvens_data is None or leasys_data is None or scrape_fresh:
        logger.info("Scraping fresh data...")
        # Use smart caching if we have cached data and just need a refresh
        use_smart_cache = use_cache and toyota_data is not None
        toyota_editions, ayvens_offers, leasys_offers = scrape_fresh_data(use_cache=use_smart_cache)

        # Convert to dicts for saving
        from dataclasses import asdict
        toyota_data = [asdict(e) for e in toyota_editions]
        ayvens_data = [asdict(o) for o in ayvens_offers]
        leasys_data = [asdict(o) for o in leasys_offers]

        # Save cache
        with open("output/toyota_prices.json", "w") as f:
            json.dump(toyota_data, f, indent=2)
        with open("output/ayvens_toyota_prices.json", "w") as f:
            json.dump(ayvens_data, f, indent=2)
        with open("output/leasys_toyota_prices.json", "w") as f:
            json.dump(leasys_data, f, indent=2)

    # Match and compare
    matches = match_editions(toyota_data, ayvens_data, leasys_data or [])
    comparisons = compare_prices(matches)

    # Generate reports
    report = generate_report(comparisons)
    print(report)

    # Save report
    report_file = "output/comparison_report.txt"
    with open(report_file, "w") as f:
        f.write(report)
    logger.info(f"Saved report to {report_file}")

    # Save CSV
    generate_csv(comparisons, "output/comparison_data.csv")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Compare Toyota.nl and Ayvens private lease prices")
    parser.add_argument("--fresh", action="store_true", help="Scrape fresh data (ignore cache)")
    parser.add_argument("--no-cache", action="store_true", help="Don't use cached data")

    args = parser.parse_args()

    main(use_cache=not args.no_cache, scrape_fresh=args.fresh)
