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
        'corolla touring sports': ['corolla touring', 'corolla ts'],
        'corolla cross': ['corolla cross', 'corolla-cross'],
        'c-hr': ['c-hr', 'chr'],
        'rav4': ['rav4', 'rav-4'],
        'bz4x': ['bz4x', 'bz-4x'],
        'land cruiser': ['land cruiser', 'landcruiser'],
    }

    @classmethod
    def normalize_model(cls, model: str) -> str:
        """Normalize model name for matching."""
        return model.lower().strip().replace('-', ' ')

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
    logger.info("Scraping fresh data from Toyota.nl and Ayvens...")

    # Scrape Toyota
    toyota_scraper = ToyotaScraper(headless=True)
    toyota_editions = toyota_scraper.scrape_all()

    # Scrape Ayvens
    ayvens_scraper = AyvensScraper(headless=True)
    ayvens_offers = ayvens_scraper.scrape_all()

    return toyota_editions, ayvens_offers


def match_editions(toyota_editions: List[dict], ayvens_offers: List[dict]) -> List[Tuple[dict, dict]]:
    """Match Toyota editions with Ayvens offers."""
    matches = []

    for toyota in toyota_editions:
        toyota_model = toyota.get('model', '')

        for ayvens in ayvens_offers:
            ayvens_model = ayvens.get('model', '')

            if ModelMatcher.models_match(toyota_model, ayvens_model):
                matches.append((toyota, ayvens))

    logger.info(f"Found {len(matches)} model matches")
    return matches


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
        "=" * 70,
        "TOYOTA.NL vs AYVENS PRIVATE LEASE PRICE COMPARISON",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "=" * 70,
        "",
    ]

    # Summary statistics
    valid_comparisons = [c for c in comparisons if c.difference is not None]

    if valid_comparisons:
        toyota_cheaper = len([c for c in valid_comparisons if c.difference > 1])
        ayvens_cheaper = len([c for c in valid_comparisons if c.difference < -1])
        same_price = len(valid_comparisons) - toyota_cheaper - ayvens_cheaper

        avg_diff = sum(c.difference for c in valid_comparisons) / len(valid_comparisons)

        report_lines.extend([
            "SUMMARY",
            "-" * 70,
            f"Total price points compared: {len(valid_comparisons)}",
            f"Toyota.nl cheaper: {toyota_cheaper} ({100*toyota_cheaper/len(valid_comparisons):.1f}%)",
            f"Ayvens cheaper: {ayvens_cheaper} ({100*ayvens_cheaper/len(valid_comparisons):.1f}%)",
            f"Same price (±€1): {same_price}",
            f"Average difference: €{avg_diff:+.2f}/mo (positive = Toyota cheaper)",
            "",
        ])

    # Group by model
    models = {}
    for c in comparisons:
        if c.model not in models:
            models[c.model] = []
        models[c.model].append(c)

    for model, model_comparisons in sorted(models.items()):
        report_lines.extend([
            "",
            f"MODEL: {model.upper()}",
            "-" * 70,
        ])

        # Show price matrix
        report_lines.append("\nPrice Comparison (€/month):")
        report_lines.append(f"{'Duration':<10} {'KM/Year':<10} {'Toyota':<12} {'Ayvens':<12} {'Diff':<10} {'Winner':<10}")
        report_lines.append("-" * 64)

        for c in model_comparisons:
            if c.toyota_price or c.ayvens_price:
                toyota_str = f"€{c.toyota_price:.0f}" if c.toyota_price else "N/A"
                ayvens_str = f"€{c.ayvens_price:.0f}" if c.ayvens_price else "N/A"
                diff_str = f"€{c.difference:+.0f}" if c.difference else "N/A"
                winner = c.cheaper_at or "N/A"

                report_lines.append(
                    f"{c.duration:<10} {c.km_per_year:<10} {toyota_str:<12} {ayvens_str:<12} {diff_str:<10} {winner:<10}"
                )

    report_lines.extend([
        "",
        "=" * 70,
        "END OF REPORT",
        "=" * 70,
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
