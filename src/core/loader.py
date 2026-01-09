"""
Data loader utilities for converting cached data to unified schema.

This module provides functions to load JSON cache files and convert
them to the unified LeaseOffer schema.
"""

import json
import os
from typing import List, Dict, Any, Optional
from datetime import datetime

from .schema import (
    LeaseOffer,
    Provider,
    convert_legacy_offers,
    create_offer_from_toyota,
    create_offer_from_suzuki,
    create_offer_from_ayvens,
    create_offer_from_leasys,
)


# Default cache file paths (relative to project root)
DEFAULT_CACHE_DIR = "output"
CACHE_FILES = {
    "toyota": "toyota_prices.json",
    "suzuki": "suzuki_prices.json",
    "ayvens_toyota": "ayvens_toyota_prices.json",
    "ayvens_suzuki": "ayvens_suzuki_prices.json",
    "leasys_toyota": "leasys_toyota_prices.json",
    "leasys_suzuki": "leasys_suzuki_prices.json",
}


def load_json_cache(filepath: str) -> List[Dict[str, Any]]:
    """Load JSON cache file and return list of offers."""
    if not os.path.exists(filepath):
        return []
    with open(filepath, 'r') as f:
        return json.load(f)


def load_toyota_offers(cache_dir: str = DEFAULT_CACHE_DIR) -> List[LeaseOffer]:
    """Load Toyota.nl offers from cache."""
    filepath = os.path.join(cache_dir, CACHE_FILES["toyota"])
    data = load_json_cache(filepath)
    return [create_offer_from_toyota(d) for d in data]


def load_suzuki_offers(cache_dir: str = DEFAULT_CACHE_DIR) -> List[LeaseOffer]:
    """Load Suzuki.nl offers from cache."""
    filepath = os.path.join(cache_dir, CACHE_FILES["suzuki"])
    data = load_json_cache(filepath)
    return [create_offer_from_suzuki(d) for d in data]


def load_ayvens_toyota_offers(cache_dir: str = DEFAULT_CACHE_DIR) -> List[LeaseOffer]:
    """Load Ayvens Toyota offers from cache."""
    filepath = os.path.join(cache_dir, CACHE_FILES["ayvens_toyota"])
    data = load_json_cache(filepath)
    return [create_offer_from_ayvens(d) for d in data]


def load_ayvens_suzuki_offers(cache_dir: str = DEFAULT_CACHE_DIR) -> List[LeaseOffer]:
    """Load Ayvens Suzuki offers from cache."""
    filepath = os.path.join(cache_dir, CACHE_FILES["ayvens_suzuki"])
    data = load_json_cache(filepath)
    return [create_offer_from_ayvens(d) for d in data]


def load_leasys_toyota_offers(cache_dir: str = DEFAULT_CACHE_DIR) -> List[LeaseOffer]:
    """Load Leasys Toyota offers from cache."""
    filepath = os.path.join(cache_dir, CACHE_FILES["leasys_toyota"])
    data = load_json_cache(filepath)
    return [create_offer_from_leasys(d) for d in data]


def load_leasys_suzuki_offers(cache_dir: str = DEFAULT_CACHE_DIR) -> List[LeaseOffer]:
    """Load Leasys Suzuki offers from cache."""
    filepath = os.path.join(cache_dir, CACHE_FILES["leasys_suzuki"])
    data = load_json_cache(filepath)
    return [create_offer_from_leasys(d) for d in data]


def load_all_offers(cache_dir: str = DEFAULT_CACHE_DIR) -> Dict[str, List[LeaseOffer]]:
    """
    Load all cached offers from all providers.

    Returns:
        Dictionary with provider keys and lists of LeaseOffer objects:
        {
            'toyota': [...],
            'suzuki': [...],
            'ayvens_toyota': [...],
            'ayvens_suzuki': [...],
            'leasys_toyota': [...],
            'leasys_suzuki': [...],
        }
    """
    return {
        'toyota': load_toyota_offers(cache_dir),
        'suzuki': load_suzuki_offers(cache_dir),
        'ayvens_toyota': load_ayvens_toyota_offers(cache_dir),
        'ayvens_suzuki': load_ayvens_suzuki_offers(cache_dir),
        'leasys_toyota': load_leasys_toyota_offers(cache_dir),
        'leasys_suzuki': load_leasys_suzuki_offers(cache_dir),
    }


def load_offers_by_brand(brand: str, cache_dir: str = DEFAULT_CACHE_DIR) -> Dict[str, List[LeaseOffer]]:
    """
    Load offers for a specific brand from all providers.

    Args:
        brand: Brand name ('toyota' or 'suzuki')
        cache_dir: Cache directory path

    Returns:
        Dictionary with provider keys:
        {
            'oem': [...],      # From brand's own website
            'ayvens': [...],
            'leasys': [...],
        }
    """
    brand_lower = brand.lower()

    if brand_lower == 'toyota':
        return {
            'oem': load_toyota_offers(cache_dir),
            'ayvens': load_ayvens_toyota_offers(cache_dir),
            'leasys': load_leasys_toyota_offers(cache_dir),
        }
    elif brand_lower == 'suzuki':
        return {
            'oem': load_suzuki_offers(cache_dir),
            'ayvens': load_ayvens_suzuki_offers(cache_dir),
            'leasys': load_leasys_suzuki_offers(cache_dir),
        }
    else:
        raise ValueError(f"Unknown brand: {brand}")


def export_unified_json(
    offers: List[LeaseOffer],
    filepath: str,
    include_raw: bool = False
) -> None:
    """
    Export list of LeaseOffer objects to unified JSON format.

    Args:
        offers: List of LeaseOffer objects
        filepath: Output file path
        include_raw: Whether to include raw_data field
    """
    data = []
    for offer in offers:
        offer_dict = offer.model_dump_json_safe()
        if not include_raw:
            offer_dict.pop('raw_data', None)
        data.append(offer_dict)

    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2, default=str)


def export_all_unified(cache_dir: str = DEFAULT_CACHE_DIR, output_dir: Optional[str] = None) -> str:
    """
    Export all cached data to a single unified JSON file.

    Args:
        cache_dir: Source cache directory
        output_dir: Output directory (defaults to cache_dir)

    Returns:
        Path to the exported file
    """
    if output_dir is None:
        output_dir = cache_dir

    all_offers = load_all_offers(cache_dir)

    # Flatten all offers into single list
    unified_offers = []
    for offers in all_offers.values():
        unified_offers.extend(offers)

    # Export to unified format
    output_path = os.path.join(output_dir, "unified_offers.json")
    export_unified_json(unified_offers, output_path)

    return output_path


def get_offer_stats(offers: List[LeaseOffer]) -> Dict[str, Any]:
    """
    Calculate statistics for a list of offers.

    Returns:
        Dictionary with stats like count, price ranges, etc.
    """
    if not offers:
        return {
            'count': 0,
            'brands': [],
            'models': [],
            'providers': [],
            'min_price': None,
            'max_price': None,
        }

    prices = [o.cheapest_price for o in offers if o.cheapest_price]

    return {
        'count': len(offers),
        'brands': list(set(o.brand for o in offers)),
        'models': list(set(o.model for o in offers)),
        'providers': list(set(o.provider.value for o in offers)),
        'min_price': min(prices) if prices else None,
        'max_price': max(prices) if prices else None,
        'avg_price': sum(prices) / len(prices) if prices else None,
    }
