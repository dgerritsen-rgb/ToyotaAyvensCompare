#!/usr/bin/env python3
"""
Incremental scraping CLI.

This script provides commands for smart, incremental scraping that reduces
traffic to provider websites by only scraping vehicles that have changed.

Usage:
    # Quick overview scan (fast, low traffic)
    python queue_scrape.py overview --provider toyota_nl

    # Detect changes and show what needs updating
    python queue_scrape.py detect --provider toyota_nl

    # Build queue from detected changes
    python queue_scrape.py build --provider toyota_nl

    # Process queued items (full price scrapes)
    python queue_scrape.py process --provider toyota_nl --max-items 10

    # Show queue status
    python queue_scrape.py status --provider toyota_nl

    # Clear queue
    python queue_scrape.py clear --provider toyota_nl
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

# Import providers to trigger registration
import src.providers  # noqa: F401

from src.core import (
    ScrapeQueue,
    ChangeDetector,
    Priority,
    list_providers,
)
from src.core.registry import ScraperRegistry

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def cmd_overview(args):
    """Run overview-only scrape (discover vehicles without prices)."""
    scraper_class = ScraperRegistry.get_scraper_class(args.provider)
    if not scraper_class:
        print(f"Error: Unknown provider '{args.provider}'")
        print(f"Available providers: {', '.join(list_providers())}")
        return 1

    print(f"\n=== Overview Scan: {args.provider} ===")
    print(f"Brand filter: {args.brand or 'all'}")
    print(f"Model filter: {args.model or 'all'}")
    print()

    try:
        scraper = scraper_class(headless=not args.visible)
        vehicles = scraper.scrape_overview(model=args.model, brand=args.brand)

        print(f"\nDiscovered {len(vehicles)} vehicles:")
        for v in vehicles[:20]:  # Show first 20
            brand = v.get('brand', '')
            model = v.get('model', v.get('model_name', ''))
            edition = v.get('edition_name', v.get('edition', ''))
            print(f"  - {brand} {model} {edition}".strip())

        if len(vehicles) > 20:
            print(f"  ... and {len(vehicles) - 20} more")

        # Save overview to file
        if args.output:
            output_file = Path(args.output)
            with open(output_file, 'w') as f:
                json.dump(vehicles, f, indent=2, default=str)
            print(f"\nSaved overview to {output_file}")

        return 0

    except Exception as e:
        logger.error(f"Overview scan failed: {e}")
        return 1


def cmd_detect(args):
    """Detect changes compared to cached data."""
    scraper_class = ScraperRegistry.get_scraper_class(args.provider)
    if not scraper_class:
        print(f"Error: Unknown provider '{args.provider}'")
        return 1

    print(f"\n=== Change Detection: {args.provider} ===")
    print(f"Freshness threshold: {args.freshness_days} days")
    print()

    try:
        scraper = scraper_class(headless=not args.visible)
        result = scraper.detect_changes(
            model=args.model,
            brand=args.brand,
            freshness_days=args.freshness_days,
        )

        print("\n--- Results ---")
        print(f"New vehicles:       {len(result.new_vehicles)}")
        print(f"Changed vehicles:   {len(result.changed_vehicles)}")
        print(f"Stale vehicles:     {len(result.stale_vehicles)}")
        print(f"Removed vehicles:   {len(result.removed_vehicles)}")
        print(f"Unchanged vehicles: {len(result.unchanged_vehicles)}")

        if result.new_vehicles:
            print("\nNew vehicles:")
            for fp in result.new_vehicles[:10]:
                print(f"  + {fp.brand} {fp.model} {fp.edition_name}".strip())
            if len(result.new_vehicles) > 10:
                print(f"  ... and {len(result.new_vehicles) - 10} more")

        if result.changed_vehicles:
            print("\nChanged vehicles:")
            for fp in result.changed_vehicles[:10]:
                print(f"  ~ {fp.brand} {fp.model} {fp.edition_name}".strip())

        if result.stale_vehicles:
            print("\nStale vehicles:")
            for fp in result.stale_vehicles[:10]:
                print(f"  * {fp.brand} {fp.model} {fp.edition_name}".strip())
            if len(result.stale_vehicles) > 10:
                print(f"  ... and {len(result.stale_vehicles) - 10} more")

        total_to_scrape = len(result.needs_scraping)
        print(f"\nTotal needing price scrape: {total_to_scrape}")

        return 0

    except Exception as e:
        logger.error(f"Change detection failed: {e}")
        return 1


def cmd_build(args):
    """Build scrape queue from change detection."""
    scraper_class = ScraperRegistry.get_scraper_class(args.provider)
    if not scraper_class:
        print(f"Error: Unknown provider '{args.provider}'")
        return 1

    print(f"\n=== Building Queue: {args.provider} ===")
    print()

    try:
        scraper = scraper_class(headless=not args.visible)
        queue = scraper.build_queue(
            model=args.model,
            brand=args.brand,
            freshness_days=args.freshness_days,
        )

        stats = queue.get_stats(args.provider)
        print("\n--- Queue Status ---")
        print(f"Pending:     {stats['pending']}")
        print(f"In Progress: {stats['in_progress']}")
        print(f"Completed:   {stats['completed']}")
        print(f"Failed:      {stats['failed']}")
        print(f"Total:       {stats['total']}")

        return 0

    except Exception as e:
        logger.error(f"Queue building failed: {e}")
        return 1


def cmd_process(args):
    """Process items from the scrape queue."""
    scraper_class = ScraperRegistry.get_scraper_class(args.provider)
    if not scraper_class:
        print(f"Error: Unknown provider '{args.provider}'")
        return 1

    queue = ScrapeQueue()
    pending = queue.get_pending_count(args.provider)

    if pending == 0:
        print(f"\nNo pending items in queue for {args.provider}")
        print("Run 'build' command first to populate the queue.")
        return 0

    print(f"\n=== Processing Queue: {args.provider} ===")
    print(f"Pending items: {pending}")
    print(f"Max items to process: {args.max_items or 'all'}")
    print()

    try:
        scraper = scraper_class(headless=not args.visible)
        offers = scraper.process_queue(queue=queue, max_items=args.max_items)

        print(f"\n--- Results ---")
        print(f"Successfully scraped: {len(offers)} offers")

        remaining = queue.get_pending_count(args.provider)
        print(f"Remaining in queue: {remaining}")

        # Save offers to output file
        if offers and args.output:
            output_file = Path(args.output)
            data = [offer.to_legacy_dict() for offer in offers]
            with open(output_file, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            print(f"Saved offers to {output_file}")

        return 0

    except Exception as e:
        logger.error(f"Queue processing failed: {e}")
        return 1


def cmd_status(args):
    """Show queue status."""
    queue = ScrapeQueue()

    if args.provider:
        providers = [args.provider]
    else:
        # Get all providers with queue files
        queue_dir = Path("output/queue")
        if queue_dir.exists():
            providers = [
                f.stem.replace('queue_', '')
                for f in queue_dir.glob("queue_*.json")
            ]
        else:
            providers = []

    if not providers:
        print("\nNo queues found.")
        return 0

    print("\n=== Queue Status ===\n")

    for provider in providers:
        stats = queue.get_stats(provider)
        if stats['total'] > 0:
            print(f"{provider}:")
            print(f"  Pending:     {stats['pending']}")
            print(f"  In Progress: {stats['in_progress']}")
            print(f"  Completed:   {stats['completed']}")
            print(f"  Failed:      {stats['failed']}")
            print()

    return 0


def cmd_clear(args):
    """Clear the scrape queue."""
    queue = ScrapeQueue()

    if args.provider:
        pending = queue.get_pending_count(args.provider)
        if pending == 0:
            print(f"Queue for {args.provider} is already empty.")
            return 0

        if not args.force:
            response = input(f"Clear {pending} items from {args.provider} queue? [y/N] ")
            if response.lower() != 'y':
                print("Cancelled.")
                return 0

        queue.clear(args.provider)
        print(f"Cleared queue for {args.provider}")
    else:
        if not args.force:
            response = input("Clear ALL queues? [y/N] ")
            if response.lower() != 'y':
                print("Cancelled.")
                return 0

        queue.clear()
        print("Cleared all queues")

    return 0


def cmd_add(args):
    """Manually add items to the queue (for testing)."""
    scraper_class = ScraperRegistry.get_scraper_class(args.provider)
    if not scraper_class:
        print(f"Error: Unknown provider '{args.provider}'")
        return 1

    print(f"\n=== Adding to Queue: {args.provider} ===")
    print(f"Priority: {args.priority}")
    print()

    try:
        # Get overview to find vehicles
        scraper = scraper_class(headless=not args.visible)
        vehicles = scraper.scrape_overview(model=args.model, brand=args.brand)

        if not vehicles:
            print("No vehicles found matching criteria.")
            return 0

        # Add to queue
        priority = Priority[args.priority.upper()]
        queue = ScrapeQueue()
        added = queue.add_batch(vehicles, args.provider, priority=priority, reason="manual")

        print(f"Added {added} items to queue")
        print(f"Total pending: {queue.get_pending_count(args.provider)}")

        return 0

    except Exception as e:
        logger.error(f"Failed to add to queue: {e}")
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="Incremental scraping with change detection and queue management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # Common arguments
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--provider', '-p', required=True,
                       help='Provider to scrape (e.g., toyota_nl, leasys_nl)')
    common.add_argument('--brand', '-b',
                       help='Filter by brand (for multi-brand providers)')
    common.add_argument('--model', '-m',
                       help='Filter by model name')
    common.add_argument('--visible', '-v', action='store_true',
                       help='Run browser in visible mode (not headless)')

    # Overview command
    overview = subparsers.add_parser('overview', parents=[common],
                                     help='Run overview-only scan')
    overview.add_argument('--output', '-o',
                         help='Save overview to JSON file')
    overview.set_defaults(func=cmd_overview)

    # Detect command
    detect = subparsers.add_parser('detect', parents=[common],
                                   help='Detect changes vs cached data')
    detect.add_argument('--freshness-days', '-f', type=int, default=7,
                       help='Days before data is considered stale (default: 7)')
    detect.set_defaults(func=cmd_detect)

    # Build command
    build = subparsers.add_parser('build', parents=[common],
                                  help='Build scrape queue from changes')
    build.add_argument('--freshness-days', '-f', type=int, default=7,
                      help='Days before data is considered stale (default: 7)')
    build.set_defaults(func=cmd_build)

    # Process command
    process = subparsers.add_parser('process', parents=[common],
                                    help='Process items from queue')
    process.add_argument('--max-items', '-n', type=int,
                        help='Maximum items to process')
    process.add_argument('--output', '-o',
                        help='Save scraped offers to JSON file')
    process.set_defaults(func=cmd_process)

    # Status command
    status = subparsers.add_parser('status', help='Show queue status')
    status.add_argument('--provider', '-p',
                       help='Filter by provider (optional)')
    status.set_defaults(func=cmd_status)

    # Clear command
    clear = subparsers.add_parser('clear', help='Clear the queue')
    clear.add_argument('--provider', '-p',
                      help='Provider to clear (omit for all)')
    clear.add_argument('--force', '-f', action='store_true',
                      help='Skip confirmation')
    clear.set_defaults(func=cmd_clear)

    # Add command (for testing)
    add = subparsers.add_parser('add', parents=[common],
                                help='Manually add items to queue')
    add.add_argument('--priority', default='normal',
                    choices=['critical', 'high', 'normal', 'low'],
                    help='Priority level (default: normal)')
    add.set_defaults(func=cmd_add)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
