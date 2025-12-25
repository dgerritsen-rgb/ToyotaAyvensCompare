#!/usr/bin/env python3
"""
Toyota Private Lease Price Scraper

Smart scraping with change detection and incremental updates.

Usage:
    python scrape.py check              # Quick overview check
    python scrape.py all                # Smart scrape (only changed)
    python scrape.py all --force        # Force full scrape
    python scrape.py --supplier toyota  # Scrape one supplier
    python scrape.py --model yaris      # Scrape one model
    python scrape.py all --parallel     # Parallel scraping
"""

import argparse
import logging
import time
import sys
from datetime import datetime, timedelta
from dataclasses import asdict
from typing import Dict, List, Optional, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

from cache_manager import (
    load_metadata, save_metadata, get_cache_age, is_cache_fresh,
    needs_refresh, update_supplier_metadata, get_model_metadata,
    load_cached_prices, save_cached_prices, merge_cached_prices,
    format_cache_age, print_cache_status, CACHE_TTL_HOURS,
    compute_hash
)
from toyota_scraper import ToyotaScraper, ToyotaEdition
from ayvens_scraper import AyvensScraper, AyvensOffer
from leasys_scraper import LeasysScraper, LeasysOffer


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def check_changes(force: bool = False) -> Dict[str, Dict[str, str]]:
    """
    Check all suppliers for changes without full scraping.

    Args:
        force: If True, check even if cache is fresh

    Returns:
        Dict of {supplier: {model: reason}} for models needing refresh
    """
    changes = {
        'toyota': {},
        'ayvens': {},
        'leasys': {},
    }

    cache_age = get_cache_age()
    print(f"\nCache age: {format_cache_age(cache_age)}")

    if not force and is_cache_fresh():
        print(f"Cache is fresh (within {CACHE_TTL_HOURS}h TTL)")
        print("Use --force to check anyway")
        return changes

    print(f"Cache exceeds {CACHE_TTL_HOURS}h TTL - checking for changes...\n")

    metadata = load_metadata()

    # Check Toyota
    print("Checking Toyota.nl...")
    try:
        scraper = ToyotaScraper(headless=True)
        current = scraper.get_overview_metadata()

        cached_toyota = metadata.get('toyota', {}).get('models', {})

        for model, current_meta in current.items():
            cached_meta = cached_toyota.get(model)
            reason = needs_refresh(cached_meta, current_meta, model)
            if reason:
                changes['toyota'][model] = reason
                print(f"  {model}: {reason}")

        if not changes['toyota']:
            print("  No changes detected")

    except Exception as e:
        logger.error(f"Error checking Toyota: {e}")
        print(f"  Error: {e}")

    # Check Ayvens
    print("\nChecking Ayvens...")
    try:
        scraper = AyvensScraper(headless=True)
        current = scraper.get_overview_metadata()

        cached_ayvens = metadata.get('ayvens', {})
        cached_hash = cached_ayvens.get('vehicle_ids_hash', '')
        cached_count = cached_ayvens.get('vehicle_count', 0)
        cached_price = cached_ayvens.get('cheapest_price')

        current_hash = current.get('vehicle_ids_hash', '')
        current_count = current.get('vehicle_count', 0)
        current_price = current.get('cheapest_price')

        if cached_count != current_count:
            changes['ayvens']['all'] = f"Vehicle count changed: {cached_count} -> {current_count}"
        elif cached_hash != current_hash:
            changes['ayvens']['all'] = "Vehicle list changed"
        elif cached_price != current_price:
            changes['ayvens']['all'] = f"Price changed: €{cached_price} -> €{current_price}"

        if changes['ayvens']:
            for model, reason in changes['ayvens'].items():
                print(f"  {reason}")
        else:
            print(f"  No changes detected ({current_count} vehicles)")

    except Exception as e:
        logger.error(f"Error checking Ayvens: {e}")
        print(f"  Error: {e}")

    # Check Leasys
    print("\nChecking Leasys...")
    try:
        scraper = LeasysScraper(headless=True)
        current = scraper.get_overview_metadata()

        cached_leasys = metadata.get('leasys', {}).get('models', {})

        for model, current_meta in current.items():
            cached_meta = cached_leasys.get(model)
            reason = needs_refresh(cached_meta, current_meta, model)
            if reason:
                changes['leasys'][model] = reason
                print(f"  {model}: {reason}")

        if not changes['leasys']:
            print("  No changes detected")

    except Exception as e:
        logger.error(f"Error checking Leasys: {e}")
        print(f"  Error: {e}")

    # Summary
    total_changes = sum(len(c) for c in changes.values())
    print(f"\nSummary: {total_changes} model(s) need refresh")

    if total_changes > 0:
        print("\nTo update changed models, run:")
        if changes['toyota']:
            models = ', '.join(changes['toyota'].keys())
            print(f"  python scrape.py --supplier toyota  # {models}")
        if changes['ayvens']:
            print(f"  python scrape.py --supplier ayvens")
        if changes['leasys']:
            models = ', '.join(changes['leasys'].keys())
            print(f"  python scrape.py --supplier leasys  # {models}")
        print(f"\nOr run 'python scrape.py all' to update all changes")

    return changes


def scrape_supplier(
    supplier: str,
    models: Optional[List[str]] = None,
    force: bool = False
) -> Tuple[List[Any], Dict[str, Dict[str, Any]]]:
    """
    Scrape a single supplier.

    Args:
        supplier: 'toyota', 'ayvens', or 'leasys'
        models: Optional list of models to scrape (None = all)
        force: Force scrape even if no changes detected

    Returns:
        Tuple of (offers list, metadata dict)
    """
    offers = []
    metadata = {}

    if supplier == 'toyota':
        scraper = ToyotaScraper(headless=True)
        if models:
            for model in models:
                model_editions = scraper.scrape_model(model)
                offers.extend(model_editions)
                # Re-create scraper for next model (it closes after each)
                if model != models[-1]:
                    scraper = ToyotaScraper(headless=True)
        else:
            offers = scraper.scrape_all(use_cache=False)

        # Build metadata
        for edition in offers:
            model = edition.model if hasattr(edition, 'model') else edition.get('model', '')
            if model not in metadata:
                metadata[model] = {'edition_count': 0, 'editions': [], 'prices': []}
            metadata[model]['edition_count'] += 1
            metadata[model]['editions'].append(
                edition.edition_name if hasattr(edition, 'edition_name') else edition.get('edition_name', '')
            )
            # Get cheapest price
            price_matrix = edition.price_matrix if hasattr(edition, 'price_matrix') else edition.get('price_matrix', {})
            if price_matrix:
                metadata[model]['prices'].append(min(price_matrix.values()))

        # Compute hashes and cheapest prices
        for model in metadata:
            metadata[model]['editions_hash'] = compute_hash(metadata[model]['editions'])
            metadata[model]['cheapest_price'] = min(metadata[model]['prices']) if metadata[model]['prices'] else None
            del metadata[model]['editions']
            del metadata[model]['prices']

    elif supplier == 'ayvens':
        scraper = AyvensScraper(headless=True)
        offers = scraper.scrape_all()

        # Build metadata
        vehicle_ids = []
        prices = []
        for offer in offers:
            vid = offer.vehicle_id if hasattr(offer, 'vehicle_id') else offer.get('vehicle_id', '')
            vehicle_ids.append(vid)
            price_matrix = offer.price_matrix if hasattr(offer, 'price_matrix') else offer.get('price_matrix', {})
            if price_matrix:
                prices.append(min(price_matrix.values()))

        metadata = {
            'vehicle_count': len(offers),
            'vehicle_ids_hash': compute_hash(vehicle_ids),
            'cheapest_price': min(prices) if prices else None,
        }

    elif supplier == 'leasys':
        scraper = LeasysScraper(headless=True)
        if models:
            for model in models:
                model_offers = scraper.scrape_model(model)
                offers.extend(model_offers)
                if model != models[-1]:
                    scraper = LeasysScraper(headless=True)
        else:
            offers = scraper.scrape_all()

        # Build metadata
        for offer in offers:
            model = offer.model if hasattr(offer, 'model') else offer.get('model', '')
            if model not in metadata:
                metadata[model] = {'edition_count': 0, 'editions': []}
            metadata[model]['edition_count'] += 1
            edition_name = offer.edition_name if hasattr(offer, 'edition_name') else offer.get('edition_name', '')
            metadata[model]['editions'].append(edition_name)

        # Compute hashes
        for model in metadata:
            metadata[model]['editions_hash'] = compute_hash(metadata[model]['editions'])
            del metadata[model]['editions']

    return offers, metadata


def scrape_all_smart(force: bool = False, parallel: bool = False):
    """
    Smart scrape - only scrape suppliers/models that have changed.

    Args:
        force: Force full scrape even if no changes
        parallel: Run supplier scrapes in parallel
    """
    start_time = time.time()

    print("\n" + "="*70)
    print("TOYOTA PRIVATE LEASE PRICE SCRAPER")
    print("="*70)

    # Check what needs updating
    if not force:
        changes = check_changes(force=True)  # Always check when running 'all'
        total_changes = sum(len(c) for c in changes.values())

        if total_changes == 0:
            print("\nNo changes detected. Cache is up to date.")
            print("Use --force to scrape anyway.")
            return
    else:
        print("\nForce mode: scraping all suppliers...")
        changes = {
            'toyota': {'all': 'forced'},
            'ayvens': {'all': 'forced'},
            'leasys': {'all': 'forced'},
        }

    # Determine what to scrape
    suppliers_to_scrape = []
    if changes['toyota']:
        suppliers_to_scrape.append(('toyota', list(changes['toyota'].keys()) if 'all' not in changes['toyota'] else None))
    if changes['ayvens']:
        suppliers_to_scrape.append(('ayvens', None))
    if changes['leasys']:
        suppliers_to_scrape.append(('leasys', list(changes['leasys'].keys()) if 'all' not in changes['leasys'] else None))

    if not suppliers_to_scrape:
        print("\nNothing to scrape.")
        return

    print(f"\nScraping {len(suppliers_to_scrape)} supplier(s)...")

    results = {}

    if parallel and len(suppliers_to_scrape) > 1:
        print("Mode: Parallel\n")
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {}
            for supplier, models in suppliers_to_scrape:
                future = executor.submit(scrape_supplier, supplier, models, force)
                futures[future] = supplier

            with tqdm(total=len(futures), desc="Suppliers", unit="supplier") as pbar:
                for future in as_completed(futures):
                    supplier = futures[future]
                    try:
                        offers, metadata = future.result()
                        results[supplier] = (offers, metadata)
                        pbar.set_postfix_str(f"{supplier}: {len(offers)} items")
                    except Exception as e:
                        logger.error(f"Error scraping {supplier}: {e}")
                    pbar.update(1)
    else:
        print("Mode: Sequential\n")
        for supplier, models in suppliers_to_scrape:
            print(f"\n>>> Scraping {supplier.upper()} <<<")
            try:
                offers, metadata = scrape_supplier(supplier, models, force)
                results[supplier] = (offers, metadata)
                print(f"  Completed: {len(offers)} items")
            except Exception as e:
                logger.error(f"Error scraping {supplier}: {e}")

    # Save results
    print("\nSaving results...")
    meta = load_metadata()

    for supplier, (offers, supplier_meta) in results.items():
        # Convert dataclass objects to dicts if needed
        data = []
        for o in offers:
            if hasattr(o, '__dataclass_fields__'):
                data.append(asdict(o))
            else:
                data.append(o)

        # Merge with existing cache if doing incremental update
        models_updated = None
        if supplier in ['toyota', 'leasys'] and supplier in changes:
            if 'all' not in changes[supplier]:
                models_updated = list(changes[supplier].keys())

        if models_updated:
            data = merge_cached_prices(supplier, data, models_updated)

        save_cached_prices(supplier, data)

        # Update metadata
        if supplier == 'ayvens':
            if meta.get('ayvens') is None:
                meta['ayvens'] = {}
            meta['ayvens'].update(supplier_meta)
            meta['ayvens']['last_check'] = datetime.now().isoformat()
        else:
            if meta.get(supplier) is None:
                meta[supplier] = {'models': {}}
            for model, model_meta in supplier_meta.items():
                model_meta['last_scraped'] = datetime.now().isoformat()
                meta[supplier]['models'][model] = model_meta
            meta[supplier]['last_check'] = datetime.now().isoformat()

    meta['last_full_scrape'] = datetime.now().isoformat()
    save_metadata(meta)

    # Summary
    elapsed = time.time() - start_time
    elapsed_min = int(elapsed // 60)
    elapsed_sec = int(elapsed % 60)

    print("\n" + "="*70)
    print("SCRAPING COMPLETE")
    print("="*70)
    print(f"Time: {elapsed_min}m {elapsed_sec}s")

    for supplier, (offers, _) in results.items():
        print(f"  {supplier.title()}: {len(offers)} items")

    print(f"\nCache updated. Run 'python compare.py' to generate comparison report.")


def main():
    parser = argparse.ArgumentParser(
        description="Toyota Private Lease Price Scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scrape.py check              # Quick check for changes
  python scrape.py all                # Smart scrape (only changed)
  python scrape.py all --force        # Force full scrape
  python scrape.py --supplier toyota  # Scrape Toyota only
  python scrape.py --model yaris      # Scrape Yaris from all suppliers
  python scrape.py all --parallel     # Parallel scraping
        """
    )

    parser.add_argument(
        'command',
        nargs='?',
        choices=['check', 'all', 'status'],
        help='Command: check (detect changes), all (smart scrape), status (show cache)'
    )

    parser.add_argument(
        '--supplier',
        choices=['toyota', 'ayvens', 'leasys'],
        help='Scrape specific supplier only'
    )

    parser.add_argument(
        '--model',
        help='Scrape specific model only (e.g., "yaris", "aygo x")'
    )

    parser.add_argument(
        '--force',
        action='store_true',
        help='Force scrape even if cache is fresh'
    )

    parser.add_argument(
        '--parallel',
        action='store_true',
        help='Run scrapers in parallel'
    )

    args = parser.parse_args()

    # Handle commands
    if args.command == 'status':
        print("\n" + "="*50)
        print("CACHE STATUS")
        print("="*50 + "\n")
        print_cache_status()
        return

    if args.command == 'check':
        check_changes(force=args.force)
        return

    if args.supplier:
        # Scrape specific supplier
        print(f"\nScraping {args.supplier}...")
        models = [args.model] if args.model else None
        offers, metadata = scrape_supplier(args.supplier, models, args.force)

        # Save
        data = [asdict(o) if hasattr(o, '__dataclass_fields__') else o for o in offers]
        save_cached_prices(args.supplier, data)

        # Update metadata
        meta = load_metadata()
        if args.supplier == 'ayvens':
            if meta.get('ayvens') is None:
                meta['ayvens'] = {}
            meta['ayvens'].update(metadata)
            meta['ayvens']['last_check'] = datetime.now().isoformat()
        else:
            if meta.get(args.supplier) is None:
                meta[args.supplier] = {'models': {}}
            for model, model_meta in metadata.items():
                model_meta['last_scraped'] = datetime.now().isoformat()
                meta[args.supplier]['models'][model] = model_meta
            meta[args.supplier]['last_check'] = datetime.now().isoformat()

        meta['last_full_scrape'] = datetime.now().isoformat()
        save_metadata(meta)

        print(f"\nDone: {len(offers)} items scraped")
        return

    if args.model and not args.supplier:
        # Scrape specific model from all suppliers
        print(f"\nScraping model '{args.model}' from all suppliers...")

        for supplier in ['toyota', 'leasys']:  # Ayvens doesn't support per-model
            print(f"\n>>> {supplier.upper()} <<<")
            try:
                offers, metadata = scrape_supplier(supplier, [args.model], args.force)
                if offers:
                    data = [asdict(o) if hasattr(o, '__dataclass_fields__') else o for o in offers]
                    merged = merge_cached_prices(supplier, data, [args.model])
                    save_cached_prices(supplier, merged)
                    print(f"  {len(offers)} items scraped")
                else:
                    print(f"  Model not found")
            except Exception as e:
                print(f"  Error: {e}")

        return

    if args.command == 'all' or (not args.command and not args.supplier and not args.model):
        # Default: smart scrape all
        scrape_all_smart(force=args.force, parallel=args.parallel)
        return

    # No valid command
    parser.print_help()


if __name__ == "__main__":
    main()
