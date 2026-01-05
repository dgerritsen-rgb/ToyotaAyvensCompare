#!/usr/bin/env python3
"""
Private Lease Price Comparison Tool

Compares Toyota and Suzuki private lease prices across suppliers:
- Toyota.nl vs Ayvens vs Leasys (for Toyota)
- Ayvens vs Leasys (for Suzuki - suzuki.nl has no configurator)

Reads from cached data - run 'python scrape.py' first to collect price data.

Usage:
    python compare.py           # Compare cached data
    python compare.py --fresh   # Deprecated - use scrape.py instead
"""

import json
import os
import logging
import sys
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
import pandas as pd

from toyota_scraper import DURATIONS, MILEAGES
from cache_manager import (
    load_metadata, get_cache_age, format_cache_age, CACHE_TTL_HOURS,
    TOYOTA_CACHE, AYVENS_CACHE, LEASYS_CACHE,
    SUZUKI_CACHE, AYVENS_SUZUKI_CACHE, LEASYS_SUZUKI_CACHE
)


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class PriceComparison:
    """Price comparison between OEM site, Ayvens, and Leasys."""
    brand: str  # 'toyota' or 'suzuki'
    model: str
    oem_variant: str  # Variant from OEM site (toyota.nl or suzuki.nl)
    ayvens_variant: str
    leasys_variant: str
    duration: int
    km_per_year: int
    oem_price: Optional[float]  # Price from OEM site
    ayvens_price: Optional[float]
    leasys_price: Optional[float]
    oem_url: Optional[str] = None
    ayvens_url: Optional[str] = None
    leasys_url: Optional[str] = None

    @property
    def cheapest_supplier(self) -> Optional[str]:
        """Which supplier has the lowest price."""
        prices = []
        oem_label = self.brand.title()  # 'Toyota' or 'Suzuki'
        if self.oem_price:
            prices.append((oem_label, self.oem_price))
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
        prices = [p for p in [self.oem_price, self.ayvens_price, self.leasys_price] if p]
        if len(prices) < 2:
            return None
        return max(prices) - min(prices)


class ModelMatcher:
    """Matches models between OEM sites and suppliers (Ayvens, Leasys)."""

    # Model name mappings (OEM name -> supplier patterns)
    MODEL_ALIASES = {
        # Toyota models
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
        # Suzuki models
        'swift': ['swift'],
        's-cross': ['s-cross', 's cross', 'scross'],
        'vitara': ['vitara'],
        'across': ['across'],
        'swace': ['swace'],
        'e-vitara': ['e-vitara', 'e vitara', 'evitara'],
    }

    # Edition name mappings (OEM edition -> supplier patterns)
    EDITION_ALIASES = {
        # Common editions
        'active': ['active'],
        'comfort': ['comfort'],
        'dynamic': ['dynamic'],
        'executive': ['executive'],
        'gr-sport': ['gr-sport', 'gr sport', 'grsport'],
        'gr-sport plus pack': ['gr-sport plus pack', 'gr sport plus pack', 'grsport plus pack', 'gr-sport-plus-pack', 'gr sport-plus-pack'],
        'style': ['style'],
        'first edition': ['first edition', 'first'],
        'premium': ['premium'],
        'lounge': ['lounge'],
        # Toyota Aygo X specific editions
        'play': ['play'],
        'pulse': ['pulse'],
        'envy': ['envy'],
        'jbl': ['jbl'],
        # Suzuki specific editions
        'select': ['select'],
        'select pro': ['select pro', 'selectpro'],
        'allgrip': ['allgrip', 'all grip', 'all-grip'],
        'allgrip-e select': ['allgrip-e select', 'allgrip e select', 'all-grip-e select'],
        'allgrip-e style': ['allgrip-e style', 'allgrip e style', 'all-grip-e style'],
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

        # Check for "plus pack" modifier first (before base edition matching)
        has_plus_pack = 'plus-pack' in variant_lower or 'plus pack' in variant_lower

        # Look for known edition names (check longer/more specific ones first)
        for edition, aliases in sorted(cls.EDITION_ALIASES.items(), key=lambda x: -len(x[0])):
            for alias in aliases:
                if alias in variant_lower:
                    return edition

        # Try to extract from patterns like "1.5 Hybrid Active" or "140 Active"
        patterns = [
            r'\b(active|comfort|dynamic|executive|gr[ -]?sport|style|first|premium|lounge|play|pulse|envy|jbl|select|select pro|allgrip)\b',
        ]
        for pattern in patterns:
            match = re.search(pattern, variant_lower)
            if match:
                edition = match.group(1).replace(' ', '-')
                if edition.startswith('gr'):
                    # Check if this is the Plus Pack variant
                    if has_plus_pack:
                        return 'gr-sport plus pack'
                    return 'gr-sport'
                return edition

        return ""

    @classmethod
    def models_match(cls, toyota_model: str, ayvens_model: str) -> bool:
        """Check if two model names match.

        Requires exact match or alias match - no partial string matching
        to avoid "Yaris" matching "Yaris Cross" incorrectly.
        """
        toyota_norm = cls.normalize_model(toyota_model)
        ayvens_norm = cls.normalize_model(ayvens_model)

        # Direct match
        if toyota_norm == ayvens_norm:
            return True

        # Check aliases - both must be in the same alias group
        for base_model, aliases in cls.MODEL_ALIASES.items():
            toyota_matches = (toyota_norm in aliases or toyota_norm == base_model)
            ayvens_matches = (ayvens_norm in aliases or ayvens_norm == base_model)
            if toyota_matches and ayvens_matches:
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


def load_cached_data() -> dict:
    """Load cached price data from files.

    Returns:
        Dictionary with keys: 'toyota', 'ayvens_toyota', 'leasys_toyota',
                              'suzuki', 'ayvens_suzuki', 'leasys_suzuki'
    """
    data = {
        'toyota': None,
        'ayvens_toyota': None,
        'leasys_toyota': None,
        'suzuki': None,
        'ayvens_suzuki': None,
        'leasys_suzuki': None,
    }

    # Toyota data
    if os.path.exists(TOYOTA_CACHE):
        with open(TOYOTA_CACHE, 'r') as f:
            data['toyota'] = json.load(f)
        logger.info(f"Loaded {len(data['toyota'])} Toyota editions from cache")

    if os.path.exists(AYVENS_CACHE):
        with open(AYVENS_CACHE, 'r') as f:
            data['ayvens_toyota'] = json.load(f)
        logger.info(f"Loaded {len(data['ayvens_toyota'])} Ayvens Toyota offers from cache")

    if os.path.exists(LEASYS_CACHE):
        with open(LEASYS_CACHE, 'r') as f:
            data['leasys_toyota'] = json.load(f)
        logger.info(f"Loaded {len(data['leasys_toyota'])} Leasys Toyota offers from cache")

    # Suzuki data
    if os.path.exists(SUZUKI_CACHE):
        with open(SUZUKI_CACHE, 'r') as f:
            data['suzuki'] = json.load(f)
        logger.info(f"Loaded {len(data['suzuki'])} Suzuki editions from cache")

    if os.path.exists(AYVENS_SUZUKI_CACHE):
        with open(AYVENS_SUZUKI_CACHE, 'r') as f:
            data['ayvens_suzuki'] = json.load(f)
        logger.info(f"Loaded {len(data['ayvens_suzuki'])} Ayvens Suzuki offers from cache")

    if os.path.exists(LEASYS_SUZUKI_CACHE):
        with open(LEASYS_SUZUKI_CACHE, 'r') as f:
            data['leasys_suzuki'] = json.load(f)
        logger.info(f"Loaded {len(data['leasys_suzuki'])} Leasys Suzuki offers from cache")

    return data


def match_editions(
    oem_editions: List[dict],
    ayvens_offers: List[dict],
    leasys_offers: List[dict],
    brand: str = 'toyota',
    exclude_used: bool = True
) -> List[Tuple[dict, Optional[dict], Optional[dict]]]:
    """Match OEM editions with Ayvens and Leasys offers.

    Args:
        oem_editions: List of OEM editions (primary source - from toyota.nl or suzuki.nl)
        ayvens_offers: List of Ayvens offers
        leasys_offers: List of Leasys offers
        brand: 'toyota' or 'suzuki'
        exclude_used: If True, exclude vehicles that are clearly used

    Returns:
        List of tuples: (oem_edition, ayvens_match_or_none, leasys_match_or_none)
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

    def get_oem_edition_key(oem: dict) -> str:
        """Extract edition key from OEM edition_name or edition_slug for matching."""
        edition_name = oem.get('edition_name', '')

        # First try to extract from edition_name if it's a real name (not "Edition N")
        if edition_name and not edition_name.startswith('Edition '):
            extracted = ModelMatcher.extract_edition(edition_name)
            if extracted:
                return extracted

        # Try to extract from edition_slug (e.g., "toyota-yaris-hybrid-130-gr-sport-1" -> "gr-sport")
        edition_slug = oem.get('edition_slug', '')
        if edition_slug:
            extracted = ModelMatcher.extract_edition(edition_slug)
            if extracted:
                return extracted

        # Fallback: use the full edition_name if available
        return edition_name if edition_name else ''

    def find_best_match(oem: dict, supplier_offers: List[dict], matched_ids: set, get_id_func, get_edition_func) -> Optional[dict]:
        """Find best matching supplier offer for an OEM edition.

        Prioritizes exact edition matches over fallback matches.
        """
        oem_edition_key = get_oem_edition_key(oem)
        oem_edition_valid = ModelMatcher.is_valid_edition_name(oem_edition_key)

        exact_match = None
        fallback_match = None

        for offer in supplier_offers:
            offer_id = get_id_func(offer)
            if offer_id in matched_ids:
                continue

            supplier_edition = get_edition_func(offer)
            supplier_edition_valid = ModelMatcher.is_valid_edition_name(supplier_edition)

            # Check for exact edition match
            if oem_edition_valid and supplier_edition_valid:
                if ModelMatcher.editions_match(oem_edition_key, supplier_edition):
                    exact_match = offer
                    break  # Found exact match, use it

            # Track first available as fallback (but keep looking for exact match)
            if fallback_match is None:
                fallback_match = offer

        # Only use fallback if no exact match found AND the model has no other OEM editions
        # that could match this supplier edition (to avoid incorrect pairings)
        if exact_match:
            return exact_match

        # Don't return fallback if we have valid editions that don't match
        # This prevents incorrect pairings like "GR Sport" -> "Dynamic"
        if fallback_match and oem_edition_valid:
            fallback_edition = get_edition_func(fallback_match)
            if ModelMatcher.is_valid_edition_name(fallback_edition):
                # Both have valid editions but they don't match - don't pair them
                return None

        return fallback_match

    for oem in oem_editions:
        oem_model = oem.get('model', '')

        # Find matching Ayvens offers (same model)
        matching_ayvens = []
        for ayvens_model_norm, ayvens_list in ayvens_by_model.items():
            if ModelMatcher.models_match(oem_model, ayvens_list[0].get('model', '')):
                matching_ayvens.extend(ayvens_list)

        # Find matching Leasys offers (same model)
        matching_leasys = []
        for leasys_model_norm, leasys_list in leasys_by_model.items():
            if ModelMatcher.models_match(oem_model, leasys_list[0].get('model', '')):
                matching_leasys.extend(leasys_list)

        # Find best Ayvens match
        ayvens_match = find_best_match(
            oem,
            matching_ayvens,
            matched_ayvens_ids,
            lambda a: a.get('vehicle_id', id(a)),
            lambda a: a.get('edition_name', '') or ModelMatcher.extract_edition(a.get('variant', ''))
        )
        if ayvens_match:
            matched_ayvens_ids.add(ayvens_match.get('vehicle_id', id(ayvens_match)))

        # Find best Leasys match
        def get_leasys_edition(l: dict) -> str:
            """Extract Leasys edition, checking URL for plus-pack info."""
            edition = l.get('edition_name', '') or l.get('variant', '')
            offer_url = l.get('offer_url', '').lower()
            # Check if URL indicates Plus Pack variant
            if 'plus-pack' in offer_url or 'plus pack' in offer_url:
                edition_lower = edition.lower()
                if 'gr' in edition_lower and 'sport' in edition_lower and 'plus' not in edition_lower:
                    return edition + ' Plus Pack'
            return edition

        leasys_match = find_best_match(
            oem,
            matching_leasys,
            matched_leasys_ids,
            lambda l: l.get('offer_url', id(l)),
            get_leasys_edition
        )
        if leasys_match:
            matched_leasys_ids.add(leasys_match.get('offer_url', id(leasys_match)))

        # Only add if at least one supplier match
        if ayvens_match or leasys_match:
            matches.append((oem, ayvens_match, leasys_match))

    logger.info(f"Found {len(matches)} {brand.title()} editions with supplier matches")
    logger.info(f"  - {sum(1 for _, a, _ in matches if a)} with Ayvens match")
    logger.info(f"  - {sum(1 for _, _, l in matches if l)} with Leasys match")
    return matches


def is_valid_price(price: Optional[float]) -> bool:
    """Check if a price is valid (within reasonable range for private lease)."""
    if price is None:
        return False
    # Private lease prices typically range from €150-€2000/month
    return 100 <= price <= 2000


def extract_oem_display_name(oem: dict, brand: str = 'toyota') -> str:
    """Extract a clean display name for OEM variant from edition_slug.

    For Toyota: Converts slugs like 'toyota-yaris-cross-toyota-yaris-cross-hybrid-115-active-automaat-1'
    to 'Hybrid 115 Active'
    For Suzuki: Uses edition_name directly
    """
    import re

    # For Suzuki, use edition_name directly (simpler structure)
    if brand == 'suzuki':
        edition_name = oem.get('edition_name', '') or oem.get('variant', '')
        if edition_name:
            return edition_name.title()
        return oem.get('model', '').title()

    # Toyota logic
    slug = oem.get('edition_slug', '')

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
    edition_name = oem.get('edition_name', '')
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

    # First check if edition_name is available and valid (preferred source)
    edition_name = ayvens.get('edition_name', '')
    if edition_name and edition_name.strip():
        name = edition_name.strip().title()
        # Add AllGrip-e prefix if present in variant but not in edition_name (Suzuki AWD)
        if 'allgrip' in variant.lower() and 'allgrip' not in edition_name.lower():
            name = 'AllGrip-e ' + name
        # Add Automaat suffix if present in variant but not in edition_name
        if 'automaat' in variant.lower() and 'automaat' not in edition_name.lower():
            name += ' Automaat'
        return name

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

    # Try Suzuki "Smart Hybrid" pattern: "X.X Bstjet Smart Hybrid {edition} [Automaat] {doors}d"
    match = re.search(r'Smart\s+Hybrid\s+([\w-]+)(?:\s+Automaat)?\s+(\d)d', variant, re.IGNORECASE)
    if match:
        edition = match.group(1).title()
        automaat = ' Automaat' if 'automaat' in variant.lower() else ''
        doors = match.group(2)
        return f"Smart Hybrid {edition}{automaat} {doors}d"

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
    offer_url = leasys.get('offer_url', '').lower()

    # Check if URL indicates Plus Pack variant
    has_plus_pack = 'plus-pack' in offer_url or 'plus pack' in offer_url

    # Capitalize edition name properly
    if edition:
        # Handle special cases like "Gr Sport" -> "GR-Sport"
        if edition.lower().startswith('gr'):
            if has_plus_pack and 'plus' not in edition.lower():
                edition = 'GR-Sport Plus Pack'
            else:
                edition = 'GR-Sport'
        else:
            edition = edition.title()

    return edition if edition else model


def compare_prices(matches: List[Tuple[dict, Optional[dict], Optional[dict]]], brand: str = 'toyota') -> List[PriceComparison]:
    """Generate price comparisons for all matched models."""
    comparisons = []

    # Leasys only supports mileages up to 20000 km
    LEASYS_MILEAGES = [5000, 10000, 15000, 20000]

    for oem, ayvens, leasys in matches:
        oem_prices = oem.get('price_matrix', {})
        ayvens_prices = ayvens.get('price_matrix', {}) if ayvens else {}
        leasys_prices = leasys.get('price_matrix', {}) if leasys else {}

        # Get URLs for this edition
        oem_url = oem.get('configurator_url', '')
        if not oem_url:
            model_slug = oem.get('model', '').lower().replace(' ', '-')
            if brand == 'toyota':
                oem_url = f"https://www.toyota.nl/private-lease/modellen#?model[]={model_slug}&durationMonths=72&yearlyKilometers=5000"
            else:
                oem_url = f"https://www.suzuki.nl/auto/private-lease/{model_slug}/"
        ayvens_url = ayvens.get('offer_url', '') if ayvens else ''
        leasys_url = leasys.get('offer_url', '') if leasys else ''

        for duration in DURATIONS:
            for km in MILEAGES:
                key = f"{duration}_{km}"

                oem_price = oem_prices.get(key)
                ayvens_price = ayvens_prices.get(key)
                # Leasys only has prices for km <= 20000
                leasys_price = leasys_prices.get(key) if km in LEASYS_MILEAGES else None

                # Filter out invalid prices
                if not is_valid_price(oem_price):
                    oem_price = None
                if not is_valid_price(ayvens_price):
                    ayvens_price = None
                if not is_valid_price(leasys_price):
                    leasys_price = None

                comparison = PriceComparison(
                    brand=brand,
                    model=oem.get('model', 'Unknown'),
                    oem_variant=extract_oem_display_name(oem, brand),
                    ayvens_variant=extract_ayvens_display_name(ayvens) if ayvens else '',
                    leasys_variant=extract_leasys_display_name(leasys) if leasys else '',
                    duration=duration,
                    km_per_year=km,
                    oem_price=oem_price,
                    ayvens_price=ayvens_price,
                    leasys_price=leasys_price,
                    oem_url=oem_url,
                    ayvens_url=ayvens_url,
                    leasys_url=leasys_url,
                )
                comparisons.append(comparison)

    return comparisons


def match_suzuki_editions(
    ayvens_offers: List[dict],
    leasys_offers: List[dict]
) -> List[Tuple[dict, Optional[dict]]]:
    """Match Suzuki Ayvens offers with Leasys offers (no OEM source).

    Args:
        ayvens_offers: List of Ayvens Suzuki offers
        leasys_offers: List of Leasys Suzuki offers

    Returns:
        List of tuples: (ayvens_offer, leasys_match_or_none)
    """
    matches = []

    def get_ayvens_edition(ayvens: dict) -> str:
        """Get edition from Ayvens, including AllGrip-e modifier if present."""
        edition = ayvens.get('edition_name', '') or ModelMatcher.extract_edition(ayvens.get('variant', ''))
        variant = ayvens.get('variant', '').lower()
        # Check if variant indicates AllGrip-e (Suzuki AWD) but edition doesn't
        if 'allgrip' in variant and edition and 'allgrip' not in edition.lower():
            return 'allgrip-e ' + edition
        return edition

    # Group Leasys by model
    leasys_by_model = {}
    for l in leasys_offers:
        model = ModelMatcher.normalize_model(l.get('model', ''))
        if model not in leasys_by_model:
            leasys_by_model[model] = []
        leasys_by_model[model].append(l)

    # Track matched Leasys offers
    matched_leasys_ids = set()

    for ayvens in ayvens_offers:
        ayvens_model = ayvens.get('model', '')
        ayvens_edition = get_ayvens_edition(ayvens)

        # Find matching Leasys offers
        leasys_match = None
        for leasys_model_norm, leasys_list in leasys_by_model.items():
            if ModelMatcher.models_match(ayvens_model, leasys_list[0].get('model', '')):
                for leasys in leasys_list:
                    leasys_id = leasys.get('offer_url', id(leasys))
                    if leasys_id in matched_leasys_ids:
                        continue

                    leasys_edition = leasys.get('edition_name', '') or leasys.get('variant', '')

                    # Check for edition match
                    if ModelMatcher.editions_match(ayvens_edition, leasys_edition):
                        leasys_match = leasys
                        matched_leasys_ids.add(leasys_id)
                        break

                if leasys_match:
                    break

        matches.append((ayvens, leasys_match))

    logger.info(f"Found {len(matches)} Suzuki Ayvens offers")
    logger.info(f"  - {sum(1 for _, l in matches if l)} with Leasys match")
    return matches


def compare_suzuki_prices(matches: List[Tuple[dict, Optional[dict]]]) -> List[PriceComparison]:
    """Generate price comparisons for Suzuki (Ayvens vs Leasys only)."""
    comparisons = []

    # Leasys only supports mileages up to 20000 km
    LEASYS_MILEAGES = [5000, 10000, 15000, 20000]

    for ayvens, leasys in matches:
        ayvens_prices = ayvens.get('price_matrix', {})
        leasys_prices = leasys.get('price_matrix', {}) if leasys else {}

        # Get URLs
        ayvens_url = ayvens.get('offer_url', '')
        leasys_url = leasys.get('offer_url', '') if leasys else ''

        for duration in DURATIONS:
            for km in MILEAGES:
                key = f"{duration}_{km}"

                ayvens_price = ayvens_prices.get(key)
                # Leasys only has prices for km <= 20000
                leasys_price = leasys_prices.get(key) if km in LEASYS_MILEAGES else None

                # Filter out invalid prices
                if not is_valid_price(ayvens_price):
                    ayvens_price = None
                if not is_valid_price(leasys_price):
                    leasys_price = None

                comparison = PriceComparison(
                    brand='suzuki',
                    model=ayvens.get('model', 'Unknown'),
                    oem_variant='',  # No OEM source for Suzuki
                    ayvens_variant=extract_ayvens_display_name(ayvens),
                    leasys_variant=extract_leasys_display_name(leasys) if leasys else '',
                    duration=duration,
                    km_per_year=km,
                    oem_price=None,  # No OEM price for Suzuki
                    ayvens_price=ayvens_price,
                    leasys_price=leasys_price,
                    oem_url='',
                    ayvens_url=ayvens_url,
                    leasys_url=leasys_url,
                )
                comparisons.append(comparison)

    return comparisons


def generate_report(comparisons: List[PriceComparison]) -> str:
    """Generate a text report of the price comparison."""
    # Separate by brand
    toyota_comparisons = [c for c in comparisons if c.brand == 'toyota']
    suzuki_comparisons = [c for c in comparisons if c.brand == 'suzuki']

    report_lines = [
        "=" * 100,
        "PRIVATE LEASE PRICE COMPARISON: OEM vs AYVENS vs LEASYS",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "=" * 100,
        "",
        "NOTE: Please verify matches using the URLs below. Editions are matched",
        "      with supplier editions by model - verify edition names match at each URL.",
        "      Leasys only offers mileages up to 20,000 km/year.",
        "",
    ]

    # Overall summary statistics (across all brands)
    valid_comparisons = [c for c in comparisons if c.oem_price or c.ayvens_price or c.leasys_price]
    valid_with_multiple = [c for c in valid_comparisons if sum([c.oem_price is not None, c.ayvens_price is not None, c.leasys_price is not None]) >= 2]

    if valid_with_multiple:
        oem_cheapest = sum(1 for c in valid_with_multiple if c.cheapest_supplier in ['Toyota', 'Suzuki'])
        ayvens_cheapest = sum(1 for c in valid_with_multiple if c.cheapest_supplier == 'Ayvens')
        leasys_cheapest = sum(1 for c in valid_with_multiple if c.cheapest_supplier == 'Leasys')

        spreads = [c.price_spread for c in valid_with_multiple if c.price_spread]
        avg_spread = sum(spreads) / len(spreads) if spreads else 0
        max_spread = max(spreads) if spreads else 0

        report_lines.extend([
            "OVERALL SUMMARY (ALL BRANDS)",
            "-" * 100,
            f"Total price points compared: {len(valid_with_multiple)}",
            f"OEM (Toyota/Suzuki) cheapest: {oem_cheapest} ({100*oem_cheapest/len(valid_with_multiple):.1f}%)" if valid_with_multiple else "",
            f"Ayvens cheapest: {ayvens_cheapest} ({100*ayvens_cheapest/len(valid_with_multiple):.1f}%)" if valid_with_multiple else "",
            f"Leasys cheapest: {leasys_cheapest} ({100*leasys_cheapest/len(valid_with_multiple):.1f}%)" if valid_with_multiple else "",
            f"Average price spread: {avg_spread:.0f}/mo",
            f"Maximum price spread: {max_spread:.0f}/mo",
            "",
        ])

    # Generate report sections for each brand
    for brand, brand_comparisons in [('Toyota', toyota_comparisons), ('Suzuki', suzuki_comparisons)]:
        if not brand_comparisons:
            continue

        report_lines.extend([
            "",
            "#" * 100,
            f"# {brand.upper()} COMPARISONS",
            "#" * 100,
        ])

        # Group by model and edition
        model_editions = {}
        edition_urls = {}
        for c in brand_comparisons:
            has_oem = c.oem_price is not None
            has_ayvens = c.ayvens_price is not None
            has_leasys = c.leasys_price is not None

            # For Toyota: require at least two prices to compare
            # For Suzuki: include if we have at least one price (show availability)
            if brand == 'Toyota':
                if sum([has_oem, has_ayvens, has_leasys]) < 2:
                    continue
            else:  # Suzuki
                if not (has_ayvens or has_leasys):
                    continue

            key = (c.model, c.oem_variant, c.ayvens_variant, c.leasys_variant)
            if key not in model_editions:
                model_editions[key] = []
                edition_urls[key] = (c.oem_url, c.ayvens_url, c.leasys_url)
            model_editions[key].append(c)

        # Sort by model name
        sorted_keys = sorted(model_editions.keys(), key=lambda x: (x[0], x[1]))

        current_model = None
        for (model, oem_variant, ayvens_variant, leasys_variant), edition_comparisons in [(k, model_editions[k]) for k in sorted_keys]:
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
            display_variant = ayvens_variant or leasys_variant or oem_variant
            extracted_edition = ModelMatcher.extract_edition(display_variant)
            if extracted_edition:
                display_variant = extracted_edition

            # Get URLs
            oem_url, ayvens_url, leasys_url = edition_urls.get((model, oem_variant, ayvens_variant, leasys_variant), ('', '', ''))

            # Edition header with URLs
            report_lines.extend([
                "",
                f"  Edition: {display_variant}",
            ])
            if oem_variant:
                report_lines.append(f"  {brand} variant: {oem_variant}")
            if ayvens_variant:
                report_lines.append(f"  Ayvens variant: {ayvens_variant}")
            if leasys_variant:
                report_lines.append(f"  Leasys variant: {leasys_variant}")
            report_lines.append("")
            if oem_variant and oem_url:
                report_lines.append(f"  {brand} URL: {oem_url}")
            if ayvens_variant:
                report_lines.append(f"  Ayvens URL: {ayvens_url}" if ayvens_url else "  Ayvens URL: N/A")
            if leasys_variant:
                report_lines.append(f"  Leasys URL: {leasys_url}" if leasys_url else "  Leasys URL: N/A")
            report_lines.append("")

            # Price comparison table - adjust column header based on brand
            oem_col = brand[:7]  # "Toyota" or "Suzuki"
            header = f"    {'Duration':<8} {'KM/Year':<10} {oem_col:<10} {'Ayvens':<10} {'Leasys':<10} {'Spread':<10} {'Cheapest':<10}"
            report_lines.append(header)
            report_lines.append("    " + "-" * 78)

            for c in edition_comparisons:
                oem_str = f"{c.oem_price:.0f}" if c.oem_price else "N/A"
                ayvens_str = f"{c.ayvens_price:.0f}" if c.ayvens_price else "N/A"
                leasys_str = f"{c.leasys_price:.0f}" if c.leasys_price else "N/A"
                spread_str = f"{c.price_spread:.0f}" if c.price_spread else "N/A"
                cheapest = c.cheapest_supplier or "N/A"

                report_lines.append(
                    f"    {c.duration:<8} {c.km_per_year:<10} {oem_str:<10} {ayvens_str:<10} {leasys_str:<10} {spread_str:<10} {cheapest:<10}"
                )

            # Edition summary
            edition_spreads = [c.price_spread for c in edition_comparisons if c.price_spread]
            if edition_spreads:
                avg_spread = sum(edition_spreads) / len(edition_spreads)
                oem_wins = sum(1 for c in edition_comparisons if c.cheapest_supplier == brand)
                ayvens_wins = sum(1 for c in edition_comparisons if c.cheapest_supplier == 'Ayvens')
                leasys_wins = sum(1 for c in edition_comparisons if c.cheapest_supplier == 'Leasys')
                report_lines.append("")
                report_lines.append(f"    Summary: Avg spread {avg_spread:.0f}/mo | Cheapest: {brand} {oem_wins}x, Ayvens {ayvens_wins}x, Leasys {leasys_wins}x")

    report_lines.extend([
        "",
        "=" * 100,
        "LEGEND:",
        "  - Spread = difference between highest and lowest price",
        "  - Cheapest = supplier with lowest price for that configuration",
        "  - N/A for Leasys at 25000/30000 km = Leasys doesn't offer these mileages",
        "  - N/A for Suzuki OEM = suzuki.nl has no interactive configurator",
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
            'brand': c.brand,
            'model': c.model,
            'oem_variant': c.oem_variant,
            'ayvens_variant': c.ayvens_variant,
            'leasys_variant': c.leasys_variant,
            'duration_months': c.duration,
            'km_per_year': c.km_per_year,
            'oem_price': c.oem_price,
            'ayvens_price': c.ayvens_price,
            'leasys_price': c.leasys_price,
            'price_spread': c.price_spread,
            'cheapest_supplier': c.cheapest_supplier,
            'oem_url': c.oem_url,
            'ayvens_url': c.ayvens_url,
            'leasys_url': c.leasys_url,
        })

    df = pd.DataFrame(data)
    df.to_csv(filename, index=False)
    logger.info(f"Saved comparison to {filename}")


def main():
    """Main comparison function - reads from cached data only."""
    os.makedirs("output", exist_ok=True)

    # Show cache info
    cache_age = get_cache_age()
    metadata = load_metadata()

    print("\n" + "="*70)
    print("PRIVATE LEASE PRICE COMPARISON (Toyota & Suzuki)")
    print("="*70)

    if cache_age:
        print(f"Using cached data from: {format_cache_age(cache_age)}")
        if cache_age > timedelta(hours=CACHE_TTL_HOURS):
            print(f"Warning: Cache is older than {CACHE_TTL_HOURS} hours.")
            print("Consider running 'python scrape.py check' to update.\n")
    else:
        print("Cache age: unknown")

    # Load cached data
    data = load_cached_data()

    # Check Toyota data
    toyota_data = data['toyota']
    ayvens_toyota_data = data['ayvens_toyota']
    leasys_toyota_data = data['leasys_toyota']

    # Check Suzuki data
    suzuki_data = data['suzuki']  # May be None (suzuki.nl has no configurator)
    ayvens_suzuki_data = data['ayvens_suzuki']
    leasys_suzuki_data = data['leasys_suzuki']

    # Show what's loaded
    print("\nLoaded data:")
    if toyota_data:
        print(f"  Toyota.nl: {len(toyota_data)} editions")
    if ayvens_toyota_data:
        print(f"  Ayvens Toyota: {len(ayvens_toyota_data)} offers")
    if leasys_toyota_data:
        print(f"  Leasys Toyota: {len(leasys_toyota_data)} offers")
    if suzuki_data:
        print(f"  Suzuki.nl: {len(suzuki_data)} editions")
    if ayvens_suzuki_data:
        print(f"  Ayvens Suzuki: {len(ayvens_suzuki_data)} offers")
    if leasys_suzuki_data:
        print(f"  Leasys Suzuki: {len(leasys_suzuki_data)} offers")

    # Check if we have minimum data for comparison
    has_toyota_comparison = toyota_data and (ayvens_toyota_data or leasys_toyota_data)
    has_suzuki_comparison = ayvens_suzuki_data and leasys_suzuki_data  # Suzuki compares Ayvens vs Leasys only

    if not has_toyota_comparison and not has_suzuki_comparison:
        print("\nError: Insufficient cached data for comparison.")
        print("Run 'python scrape.py all' first to collect price data.")
        sys.exit(1)

    all_comparisons = []

    # Toyota comparisons (OEM vs Ayvens vs Leasys)
    if has_toyota_comparison:
        print("\nMatching Toyota editions...")
        toyota_matches = match_editions(
            toyota_data,
            ayvens_toyota_data or [],
            leasys_toyota_data or [],
            brand='toyota'
        )
        toyota_comparisons = compare_prices(toyota_matches, brand='toyota')
        all_comparisons.extend(toyota_comparisons)

    # Suzuki comparisons (Ayvens vs Leasys - no OEM configurator)
    if has_suzuki_comparison:
        print("Matching Suzuki editions...")
        # For Suzuki, we directly compare Ayvens to Leasys without OEM reference
        suzuki_matches = match_suzuki_editions(
            ayvens_suzuki_data,
            leasys_suzuki_data or []
        )

        # Check if there are any matches
        matches_with_leasys = sum(1 for _, l in suzuki_matches if l)
        if matches_with_leasys == 0:
            # Get unique models from each provider
            ayvens_models = set(a.get('model', '').lower() for a in ayvens_suzuki_data)
            leasys_models = set(l.get('model', '').lower() for l in (leasys_suzuki_data or []))
            print(f"\n  Note: No matching Suzuki models between providers:")
            print(f"    Ayvens offers: {', '.join(sorted(m.title() for m in ayvens_models))}")
            print(f"    Leasys offers: {', '.join(sorted(m.title() for m in leasys_models))}")
            print("    (Different models - no price comparison possible)\n")

        suzuki_comparisons = compare_suzuki_prices(suzuki_matches)
        all_comparisons.extend(suzuki_comparisons)

    # Generate reports
    report = generate_report(all_comparisons)
    print(report)

    # Save report
    report_file = "output/comparison_report.txt"
    with open(report_file, "w") as f:
        f.write(report)
    logger.info(f"Saved report to {report_file}")

    # Save CSV
    generate_csv(all_comparisons, "output/comparison_data.csv")

    print("\nDone. Reports saved to output/")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Compare Toyota and Suzuki private lease prices across OEM, Ayvens, and Leasys",
        epilog="Note: This script reads from cached data. Run 'python scrape.py' first to collect prices."
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="DEPRECATED: Use 'python scrape.py all' instead"
    )

    args = parser.parse_args()

    if args.fresh:
        print("Warning: --fresh is deprecated.")
        print("To scrape fresh data, use: python scrape.py all")
        print("Then run: python compare.py")
        print("\nContinuing with cached data comparison...\n")

    main()
