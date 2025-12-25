#!/usr/bin/env python3
"""
Cache Manager for Toyota Private Lease Price Comparison

Handles metadata tracking, change detection, and cache validation
to minimize unnecessary website requests.
"""

import json
import os
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict

# Cache configuration
CACHE_DIR = "output"
METADATA_FILE = os.path.join(CACHE_DIR, "cache_metadata.json")
CACHE_TTL_HOURS = 48  # Skip overview check if cache is newer than this

# Price data files
TOYOTA_CACHE = os.path.join(CACHE_DIR, "toyota_prices.json")
AYVENS_CACHE = os.path.join(CACHE_DIR, "ayvens_toyota_prices.json")
LEASYS_CACHE = os.path.join(CACHE_DIR, "leasys_toyota_prices.json")


@dataclass
class ModelMetadata:
    """Metadata for a single model/edition group."""
    edition_count: int
    editions_hash: str
    cheapest_price: Optional[float]
    last_scraped: str  # ISO format datetime


@dataclass
class SupplierMetadata:
    """Metadata for a supplier."""
    last_check: str  # ISO format datetime
    models: Dict[str, Dict[str, Any]]  # model_name -> ModelMetadata as dict


@dataclass
class CacheMetadata:
    """Full cache metadata structure."""
    last_full_scrape: Optional[str]
    toyota: Optional[Dict[str, Any]]
    ayvens: Optional[Dict[str, Any]]
    leasys: Optional[Dict[str, Any]]


def compute_hash(items: List[str]) -> str:
    """Compute a hash of a list of strings (e.g., edition names/slugs)."""
    sorted_items = sorted(items)
    content = "|".join(sorted_items)
    return hashlib.md5(content.encode()).hexdigest()[:12]


def get_now_iso() -> str:
    """Get current datetime in ISO format."""
    return datetime.now().isoformat()


def parse_iso_datetime(iso_str: str) -> datetime:
    """Parse ISO format datetime string."""
    try:
        return datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return datetime.min


def load_metadata() -> Dict[str, Any]:
    """Load cache metadata from file."""
    if os.path.exists(METADATA_FILE):
        try:
            with open(METADATA_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    # Return empty metadata structure
    return {
        "last_full_scrape": None,
        "toyota": None,
        "ayvens": None,
        "leasys": None,
    }


def save_metadata(metadata: Dict[str, Any]):
    """Save cache metadata to file."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(METADATA_FILE, 'w') as f:
        json.dump(metadata, f, indent=2)


def get_cache_age() -> Optional[timedelta]:
    """Get the age of the cache since last full scrape."""
    metadata = load_metadata()
    last_scrape = metadata.get("last_full_scrape")
    if last_scrape:
        return datetime.now() - parse_iso_datetime(last_scrape)
    return None


def is_cache_fresh(hours: int = CACHE_TTL_HOURS) -> bool:
    """Check if cache is fresh (within TTL)."""
    age = get_cache_age()
    if age is None:
        return False
    return age < timedelta(hours=hours)


def get_supplier_cache_age(supplier: str) -> Optional[timedelta]:
    """Get the age of a specific supplier's cache."""
    metadata = load_metadata()
    supplier_meta = metadata.get(supplier)
    if supplier_meta and supplier_meta.get("last_check"):
        return datetime.now() - parse_iso_datetime(supplier_meta["last_check"])
    return None


def needs_refresh(
    cached_meta: Optional[Dict[str, Any]],
    current_meta: Dict[str, Any],
    model: str
) -> Optional[str]:
    """
    Check if a model needs to be refreshed.

    Args:
        cached_meta: Cached metadata for this model (or None if not cached)
        current_meta: Current metadata from overview scrape
        model: Model name for logging

    Returns:
        Reason string if refresh needed, None if no changes detected
    """
    if cached_meta is None:
        return "Not in cache"

    # Check if cache is fresh (within 48 hours)
    last_scraped = cached_meta.get('last_scraped')
    if last_scraped:
        age = datetime.now() - parse_iso_datetime(last_scraped)
        if age < timedelta(hours=CACHE_TTL_HOURS):
            return None  # Recent enough, skip check

    # Check vehicle/edition counts
    cached_count = cached_meta.get('edition_count', 0)
    current_count = current_meta.get('edition_count', 0)
    if cached_count != current_count:
        return f"Edition count changed: {cached_count} -> {current_count}"

    # Check editions hash (new/removed editions)
    cached_hash = cached_meta.get('editions_hash', '')
    current_hash = current_meta.get('editions_hash', '')
    if cached_hash != current_hash:
        return "Edition list changed"

    # Check ANY price change
    cached_price = cached_meta.get('cheapest_price')
    current_price = current_meta.get('cheapest_price')
    if cached_price != current_price:
        return f"Price changed: €{cached_price} -> €{current_price}"

    return None  # No changes detected


def update_supplier_metadata(
    supplier: str,
    models_metadata: Dict[str, Dict[str, Any]]
):
    """
    Update metadata for a supplier after scraping.

    Args:
        supplier: Supplier name ('toyota', 'ayvens', 'leasys')
        models_metadata: Dict of model_name -> metadata dict
    """
    metadata = load_metadata()

    now = get_now_iso()

    if metadata.get(supplier) is None:
        metadata[supplier] = {"last_check": now, "models": {}}

    metadata[supplier]["last_check"] = now

    for model_name, model_meta in models_metadata.items():
        model_meta["last_scraped"] = now
        metadata[supplier]["models"][model_name] = model_meta

    # Update last full scrape time
    metadata["last_full_scrape"] = now

    save_metadata(metadata)


def get_model_metadata(supplier: str, model: str) -> Optional[Dict[str, Any]]:
    """Get cached metadata for a specific model."""
    metadata = load_metadata()
    supplier_meta = metadata.get(supplier, {})
    models = supplier_meta.get("models", {})
    return models.get(model)


def load_cached_prices(supplier: str) -> Optional[List[Dict[str, Any]]]:
    """Load cached price data for a supplier."""
    cache_files = {
        "toyota": TOYOTA_CACHE,
        "ayvens": AYVENS_CACHE,
        "leasys": LEASYS_CACHE,
    }

    cache_file = cache_files.get(supplier)
    if cache_file and os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return None


def save_cached_prices(supplier: str, data: List[Dict[str, Any]]):
    """Save cached price data for a supplier."""
    cache_files = {
        "toyota": TOYOTA_CACHE,
        "ayvens": AYVENS_CACHE,
        "leasys": LEASYS_CACHE,
    }

    cache_file = cache_files.get(supplier)
    if cache_file:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, 'w') as f:
            json.dump(data, f, indent=2)


def merge_cached_prices(
    supplier: str,
    new_data: List[Dict[str, Any]],
    models_to_update: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Merge new price data with existing cache.

    If models_to_update is specified, only those models are replaced.
    Otherwise, all data is replaced.

    Args:
        supplier: Supplier name
        new_data: New scraped data
        models_to_update: List of model names to update (None = replace all)

    Returns:
        Merged data list
    """
    if models_to_update is None:
        # Replace everything
        return new_data

    # Load existing cache
    existing = load_cached_prices(supplier) or []

    # Create a dict of existing data by model
    existing_by_model = {}
    for item in existing:
        model = item.get('model', '')
        if model not in existing_by_model:
            existing_by_model[model] = []
        existing_by_model[model].append(item)

    # Replace specified models with new data
    new_by_model = {}
    for item in new_data:
        model = item.get('model', '')
        if model not in new_by_model:
            new_by_model[model] = []
        new_by_model[model].append(item)

    # Merge
    for model in models_to_update:
        if model in new_by_model:
            existing_by_model[model] = new_by_model[model]

    # Flatten back to list
    result = []
    for items in existing_by_model.values():
        result.extend(items)

    return result


def format_cache_age(age: Optional[timedelta]) -> str:
    """Format cache age as human-readable string."""
    if age is None:
        return "never"

    total_seconds = int(age.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60

    if days > 0:
        return f"{days}d {hours}h ago"
    elif hours > 0:
        return f"{hours}h {minutes}m ago"
    else:
        return f"{minutes}m ago"


def print_cache_status():
    """Print current cache status."""
    metadata = load_metadata()

    age = get_cache_age()
    print(f"Cache age: {format_cache_age(age)}")

    if is_cache_fresh():
        print(f"Status: Fresh (within {CACHE_TTL_HOURS}h TTL)")
    else:
        print(f"Status: Stale (exceeds {CACHE_TTL_HOURS}h TTL)")

    print()

    for supplier in ["toyota", "ayvens", "leasys"]:
        supplier_meta = metadata.get(supplier)
        if supplier_meta:
            models = supplier_meta.get("models", {})
            print(f"{supplier.title()}: {len(models)} models cached")
            for model, model_meta in models.items():
                count = model_meta.get("edition_count", "?")
                price = model_meta.get("cheapest_price", "?")
                print(f"  - {model}: {count} editions, cheapest €{price}")
        else:
            print(f"{supplier.title()}: No cache")
