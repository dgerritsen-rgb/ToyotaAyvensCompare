#!/usr/bin/env python3
"""
Toyota vs Ayvens Price Comparison Tool

Compares Toyota.nl private lease prices with Ayvens Toyota prices.
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


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class PriceComparison:
    """Price comparison between Toyota.nl and Ayvens."""
    model: str
    toyota_variant: str
    ayvens_variant: str
    duration: int
    km_per_year: int
    toyota_price: Optional[float]
    ayvens_price: Optional[float]
    difference: Optional[float]  # Ayvens - Toyota (negative = Ayvens cheaper)
    difference_pct: Optional[float]

    @property
    def cheaper_at(self) -> Optional[str]:
        """Which site is cheaper."""
        if self.difference is None:
            return None
        if self.difference < -1:  # More than €1 cheaper
            return "Ayvens"
        elif self.difference > 1:  # More than €1 cheaper
            return "Toyota"
        return "Same"


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
        """Check if edition name is valid (not a price or empty)."""
        if not edition:
            return False
        # Skip if it looks like a price
        price_patterns = [r'€', r'\d+,-', r'\d+,\d{2}', r'vanaf', r'per maand', r'p/m']
        import re
        for pattern in price_patterns:
            if re.search(pattern, edition, re.IGNORECASE):
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


def load_cached_data() -> Tuple[Optional[List[dict]], Optional[List[dict]]]:
    """Load cached price data if available."""
    toyota_data = None
    ayvens_data = None

    toyota_path = "output/toyota_prices.json"
    ayvens_path = "output/ayvens_toyota_prices.json"

    if os.path.exists(toyota_path):
        with open(toyota_path, 'r') as f:
            toyota_data = json.load(f)
        logger.info(f"Loaded {len(toyota_data)} Toyota editions from cache")

    if os.path.exists(ayvens_path):
        with open(ayvens_path, 'r') as f:
            ayvens_data = json.load(f)
        logger.info(f"Loaded {len(ayvens_data)} Ayvens offers from cache")

    return toyota_data, ayvens_data


def scrape_fresh_data() -> Tuple[List[ToyotaEdition], List[AyvensOffer]]:
    """Scrape fresh data from both sites."""
    print("\n" + "="*70)
    print("TOYOTA VS AYVENS PRICE COMPARISON - DATA COLLECTION")
    print("="*70)

    # Scrape Toyota
    print("\n>>> PHASE 1: SCRAPING TOYOTA.NL <<<\n")
    toyota_scraper = ToyotaScraper(headless=True)
    toyota_editions = toyota_scraper.scrape_all()
    print(f"\nToyota scraping complete: {len(toyota_editions)} editions\n")

    # Scrape Ayvens
    print("\n>>> PHASE 2: SCRAPING AYVENS <<<\n")
    ayvens_scraper = AyvensScraper(headless=True)
    ayvens_offers = ayvens_scraper.scrape_all()
    print(f"\nAyvens scraping complete: {len(ayvens_offers)} offers\n")

    print("\n>>> DATA COLLECTION COMPLETE <<<")
    print("="*70 + "\n")

    return toyota_editions, ayvens_offers


def match_editions(toyota_editions: List[dict], ayvens_offers: List[dict], new_only: bool = True) -> List[Tuple[dict, dict]]:
    """Match Toyota editions with Ayvens offers.

    Args:
        toyota_editions: List of Toyota editions
        ayvens_offers: List of Ayvens offers
        new_only: If True, only include new (build-to-order) Ayvens vehicles
    """
    matches = []

    # Filter Ayvens to new cars only if requested
    if new_only:
        filtered_ayvens = []
        for ayvens in ayvens_offers:
            # Check using is_new field if available, otherwise use variant text detection
            is_new = ayvens.get('is_new', True)  # Default to True for backward compatibility
            variant = ayvens.get('variant', '')

            # Double-check with variant text
            if ModelMatcher.is_used_car(variant):
                is_new = False

            if is_new:
                filtered_ayvens.append(ayvens)

        logger.info(f"Filtered to {len(filtered_ayvens)} new Ayvens vehicles (from {len(ayvens_offers)} total)")
        ayvens_offers = filtered_ayvens

    # Group by model first to avoid cross-matching
    toyota_by_model = {}
    for t in toyota_editions:
        model = ModelMatcher.normalize_model(t.get('model', ''))
        if model not in toyota_by_model:
            toyota_by_model[model] = []
        toyota_by_model[model].append(t)

    ayvens_by_model = {}
    for a in ayvens_offers:
        model = ModelMatcher.normalize_model(a.get('model', ''))
        if model not in ayvens_by_model:
            ayvens_by_model[model] = []
        ayvens_by_model[model].append(a)

    # Track which Ayvens offers have been matched to avoid duplicates
    matched_ayvens_ids = set()

    for toyota in toyota_editions:
        toyota_model = toyota.get('model', '')
        toyota_model_norm = ModelMatcher.normalize_model(toyota_model)
        toyota_edition = toyota.get('edition_name', '')
        toyota_edition_valid = ModelMatcher.is_valid_edition_name(toyota_edition)

        # Find matching Ayvens model group
        matching_ayvens = []
        for ayvens_model_norm, ayvens_list in ayvens_by_model.items():
            if ModelMatcher.models_match(toyota_model, ayvens_list[0].get('model', '')):
                matching_ayvens.extend(ayvens_list)

        if not matching_ayvens:
            continue

        # Try to find the best match
        best_match = None

        for ayvens in matching_ayvens:
            ayvens_id = ayvens.get('vehicle_id', id(ayvens))
            if ayvens_id in matched_ayvens_ids:
                continue  # Already matched

            ayvens_variant = ayvens.get('variant', '')
            ayvens_edition = ayvens.get('edition_name', '') or ModelMatcher.extract_edition(ayvens_variant)
            ayvens_edition_valid = ModelMatcher.is_valid_edition_name(ayvens_edition)

            # If both have valid edition names, only match if they match
            if toyota_edition_valid and ayvens_edition_valid:
                if ModelMatcher.editions_match(toyota_edition, ayvens_edition):
                    best_match = ayvens
                    break
            elif ayvens_edition_valid:
                # Toyota invalid, Ayvens valid - take first available Ayvens
                best_match = ayvens
                break
            elif not best_match:
                # Both invalid - take first available
                best_match = ayvens

        if best_match:
            matches.append((toyota, best_match))
            matched_ayvens_ids.add(best_match.get('vehicle_id', id(best_match)))

    logger.info(f"Found {len(matches)} model+edition matches")
    return matches


def is_valid_price(price: Optional[float]) -> bool:
    """Check if a price is valid (within reasonable range for private lease)."""
    if price is None:
        return False
    # Private lease prices typically range from €150-€2000/month
    return 100 <= price <= 2000


def compare_prices(matches: List[Tuple[dict, dict]]) -> List[PriceComparison]:
    """Generate price comparisons for all matched models."""
    comparisons = []

    for toyota, ayvens in matches:
        toyota_prices = toyota.get('price_matrix', {})
        ayvens_prices = ayvens.get('price_matrix', {})

        for duration in DURATIONS:
            for km in MILEAGES:
                key = f"{duration}_{km}"

                toyota_price = toyota_prices.get(key)
                ayvens_price = ayvens_prices.get(key)

                # Filter out invalid prices (like 1.0 from slider issues)
                if not is_valid_price(toyota_price):
                    toyota_price = None
                if not is_valid_price(ayvens_price):
                    ayvens_price = None

                difference = None
                difference_pct = None

                if toyota_price and ayvens_price:
                    difference = ayvens_price - toyota_price
                    if toyota_price > 0:
                        difference_pct = (difference / toyota_price) * 100

                comparison = PriceComparison(
                    model=toyota.get('model', 'Unknown'),
                    toyota_variant=toyota.get('edition_name', ''),
                    ayvens_variant=ayvens.get('variant', ''),
                    duration=duration,
                    km_per_year=km,
                    toyota_price=toyota_price,
                    ayvens_price=ayvens_price,
                    difference=difference,
                    difference_pct=difference_pct,
                )
                comparisons.append(comparison)

    return comparisons


def generate_report(comparisons: List[PriceComparison]) -> str:
    """Generate a text report of the price comparison."""
    report_lines = [
        "=" * 80,
        "TOYOTA.NL vs AYVENS PRIVATE LEASE PRICE COMPARISON",
        "NEW VEHICLES ONLY (Build-to-Order)",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "=" * 80,
        "",
    ]

    # Summary statistics
    valid_comparisons = [c for c in comparisons if c.difference is not None]

    if valid_comparisons:
        toyota_cheaper = len([c for c in valid_comparisons if c.difference > 1])
        ayvens_cheaper = len([c for c in valid_comparisons if c.difference < -1])
        same_price = len(valid_comparisons) - toyota_cheaper - ayvens_cheaper

        avg_diff = sum(c.difference for c in valid_comparisons) / len(valid_comparisons)
        max_toyota_saving = max((c.difference for c in valid_comparisons if c.difference > 0), default=0)
        max_ayvens_saving = min((c.difference for c in valid_comparisons if c.difference < 0), default=0)

        report_lines.extend([
            "OVERALL SUMMARY",
            "-" * 80,
            f"Total price points compared: {len(valid_comparisons)}",
            f"Toyota.nl cheaper: {toyota_cheaper} ({100*toyota_cheaper/len(valid_comparisons):.1f}%)",
            f"Ayvens cheaper: {ayvens_cheaper} ({100*ayvens_cheaper/len(valid_comparisons):.1f}%)",
            f"Same price (±€1): {same_price}",
            f"Average difference: €{avg_diff:+.2f}/mo (positive = Toyota cheaper)",
            f"Max Toyota saving: €{max_toyota_saving:.0f}/mo",
            f"Max Ayvens saving: €{abs(max_ayvens_saving):.0f}/mo",
            "",
        ])

    # Group by model and edition, filtering to only those with valid comparisons
    model_editions = {}
    for c in comparisons:
        # Only include if both prices are valid
        if not (c.toyota_price and c.ayvens_price):
            continue
        key = (c.model, c.toyota_variant, c.ayvens_variant)
        if key not in model_editions:
            model_editions[key] = []
        model_editions[key].append(c)

    # Sort by model name, then by Ayvens variant (which has the actual edition name)
    sorted_keys = sorted(model_editions.keys(), key=lambda x: (x[0], x[2], x[1]))

    current_model = None
    for (model, toyota_variant, ayvens_variant), edition_comparisons in [(k, model_editions[k]) for k in sorted_keys]:
        # Skip if no valid comparisons
        if not edition_comparisons:
            continue

        # Model header (only print when model changes)
        if model != current_model:
            current_model = model
            report_lines.extend([
                "",
                "=" * 80,
                f"MODEL: {model.upper()}",
                "=" * 80,
            ])

        # Extract clean edition name from Ayvens variant
        ayvens_edition = ModelMatcher.extract_edition(ayvens_variant)
        display_variant = ayvens_edition if ayvens_edition else ayvens_variant[:60]

        # Edition header
        report_lines.extend([
            "",
            f"  Ayvens Edition: {display_variant}",
            "",
        ])

        # Price comparison table
        report_lines.append(f"    {'Duration':<8} {'KM/Year':<10} {'Toyota':<10} {'Ayvens':<10} {'Diff':<10} {'Winner':<10}")
        report_lines.append("    " + "-" * 58)

        for c in edition_comparisons:
            toyota_str = f"€{c.toyota_price:.0f}"
            ayvens_str = f"€{c.ayvens_price:.0f}"
            diff_str = f"€{c.difference:+.0f}"
            winner = c.cheaper_at or "Same"

            report_lines.append(
                f"    {c.duration:<8} {c.km_per_year:<10} {toyota_str:<10} {ayvens_str:<10} {diff_str:<10} {winner:<10}"
            )

        # Edition summary
        edition_avg = sum(c.difference for c in edition_comparisons) / len(edition_comparisons)
        cheaper_count = sum(1 for c in edition_comparisons if c.difference > 0)
        report_lines.append("")
        report_lines.append(f"    Summary: Avg diff €{edition_avg:+.0f}/mo | Toyota cheaper in {cheaper_count}/{len(edition_comparisons)} cases")

    report_lines.extend([
        "",
        "=" * 80,
        "LEGEND:",
        "  - Positive difference = Toyota is MORE expensive (Ayvens saves money)",
        "  - Negative difference = Ayvens is MORE expensive (Toyota saves money)",
        "=" * 80,
        "END OF REPORT",
        "=" * 80,
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
            'duration_months': c.duration,
            'km_per_year': c.km_per_year,
            'toyota_price': c.toyota_price,
            'ayvens_price': c.ayvens_price,
            'difference_eur': c.difference,
            'difference_pct': c.difference_pct,
            'cheaper_at': c.cheaper_at,
        })

    df = pd.DataFrame(data)
    df.to_csv(filename, index=False)
    logger.info(f"Saved comparison to {filename}")


def main(use_cache: bool = True, scrape_fresh: bool = False):
    """Main comparison function."""
    os.makedirs("output", exist_ok=True)

    toyota_data = None
    ayvens_data = None

    # Load or scrape data
    if use_cache and not scrape_fresh:
        toyota_data, ayvens_data = load_cached_data()

    if toyota_data is None or ayvens_data is None or scrape_fresh:
        logger.info("Scraping fresh data...")
        toyota_editions, ayvens_offers = scrape_fresh_data()

        # Convert to dicts for saving
        from dataclasses import asdict
        toyota_data = [asdict(e) for e in toyota_editions]
        ayvens_data = [asdict(o) for o in ayvens_offers]

        # Save cache
        with open("output/toyota_prices.json", "w") as f:
            json.dump(toyota_data, f, indent=2)
        with open("output/ayvens_toyota_prices.json", "w") as f:
            json.dump(ayvens_data, f, indent=2)

    # Match and compare
    matches = match_editions(toyota_data, ayvens_data)
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
