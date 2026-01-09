"""
Incremental scraping queue system.

This module provides smart scraping through:
- Overview-only scraping to get vehicle inventory without full prices
- Change detection to identify new/changed/stale vehicles
- Prioritized queue for processing vehicles that need price updates
- Queue persistence for resumable scraping sessions
"""

import json
import hashlib
import logging
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any, Set
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class Priority(int, Enum):
    """Scrape priority levels."""
    CRITICAL = 1   # New vehicles - scrape immediately
    HIGH = 2       # Changed vehicles - scrape soon
    NORMAL = 3     # Stale vehicles - scrape when idle
    LOW = 4        # Refresh existing - lowest priority


class QueueItemStatus(str, Enum):
    """Status of a queue item."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class VehicleFingerprint(BaseModel):
    """
    Lightweight vehicle identifier from overview scraping.

    Used to detect changes without full price scraping.
    """
    provider: str
    brand: str
    model: str
    edition_name: str = ""
    variant_slug: str = ""
    url: str = ""

    # Additional identifying attributes
    extra_attributes: Dict[str, Any] = Field(default_factory=dict)

    @property
    def unique_key(self) -> str:
        """Generate unique key for this vehicle."""
        parts = [
            self.provider,
            self.brand.lower().replace(' ', '-'),
            self.model.lower().replace(' ', '-'),
            self.edition_name.lower().replace(' ', '-') if self.edition_name else '',
            self.variant_slug.lower() if self.variant_slug else '',
        ]
        return '_'.join(filter(None, parts))

    @property
    def fingerprint_hash(self) -> str:
        """Generate hash for change detection."""
        data = {
            'key': self.unique_key,
            'url': self.url,
            'extra': self.extra_attributes,
        }
        hash_str = json.dumps(data, sort_keys=True)
        return hashlib.md5(hash_str.encode()).hexdigest()[:12]

    @classmethod
    def from_vehicle_dict(cls, vehicle: Dict[str, Any], provider: str) -> 'VehicleFingerprint':
        """Create fingerprint from scraper's vehicle dictionary."""
        return cls(
            provider=provider,
            brand=vehicle.get('brand', ''),
            model=vehicle.get('model', vehicle.get('model_name', '')),
            edition_name=vehicle.get('edition_name', vehicle.get('edition', '')),
            variant_slug=vehicle.get('variant_slug', vehicle.get('edition_slug', '')),
            url=vehicle.get('url', vehicle.get('source_url', '')),
            extra_attributes={
                k: v for k, v in vehicle.items()
                if k not in ('brand', 'model', 'model_name', 'edition_name', 'edition',
                            'variant_slug', 'edition_slug', 'url', 'source_url')
            }
        )


class QueueItem(BaseModel):
    """A single item in the scrape queue."""
    fingerprint: VehicleFingerprint
    vehicle_data: Dict[str, Any]  # Original vehicle dict for scraping

    priority: Priority = Priority.NORMAL
    status: QueueItemStatus = QueueItemStatus.PENDING

    # Timestamps
    added_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Tracking
    attempt_count: int = 0
    max_attempts: int = 3
    last_error: Optional[str] = None

    # Reason for queueing
    reason: str = ""  # e.g., "new_vehicle", "changed", "stale", "refresh"

    @property
    def unique_key(self) -> str:
        return self.fingerprint.unique_key

    def mark_in_progress(self):
        """Mark item as being processed."""
        self.status = QueueItemStatus.IN_PROGRESS
        self.started_at = datetime.utcnow()
        self.attempt_count += 1

    def mark_completed(self):
        """Mark item as successfully completed."""
        self.status = QueueItemStatus.COMPLETED
        self.completed_at = datetime.utcnow()

    def mark_failed(self, error: str):
        """Mark item as failed."""
        self.last_error = error
        if self.attempt_count >= self.max_attempts:
            self.status = QueueItemStatus.FAILED
        else:
            self.status = QueueItemStatus.PENDING  # Will retry


class ChangeDetectionResult(BaseModel):
    """Result of comparing overview with cache."""
    provider: str
    detected_at: datetime = Field(default_factory=datetime.utcnow)

    # Categorized vehicles
    new_vehicles: List[VehicleFingerprint] = Field(default_factory=list)
    changed_vehicles: List[VehicleFingerprint] = Field(default_factory=list)
    stale_vehicles: List[VehicleFingerprint] = Field(default_factory=list)
    removed_vehicles: List[VehicleFingerprint] = Field(default_factory=list)
    unchanged_vehicles: List[VehicleFingerprint] = Field(default_factory=list)

    @property
    def needs_scraping(self) -> List[VehicleFingerprint]:
        """All vehicles that need price scraping."""
        return self.new_vehicles + self.changed_vehicles + self.stale_vehicles

    @property
    def summary(self) -> str:
        """Human-readable summary."""
        return (
            f"New: {len(self.new_vehicles)}, "
            f"Changed: {len(self.changed_vehicles)}, "
            f"Stale: {len(self.stale_vehicles)}, "
            f"Removed: {len(self.removed_vehicles)}, "
            f"Unchanged: {len(self.unchanged_vehicles)}"
        )


class ScrapeQueue:
    """
    Persistent queue for incremental scraping.

    Manages a prioritized queue of vehicles to scrape, with persistence
    to allow resuming interrupted scrape sessions.
    """

    def __init__(self, queue_dir: str = "output/queue"):
        """
        Initialize scrape queue.

        Args:
            queue_dir: Directory for queue persistence files
        """
        self.queue_dir = Path(queue_dir)
        self.queue_dir.mkdir(parents=True, exist_ok=True)

        self._items: Dict[str, QueueItem] = {}
        self._load_queue()

    def _get_queue_file(self, provider: Optional[str] = None) -> Path:
        """Get path to queue file."""
        if provider:
            return self.queue_dir / f"queue_{provider}.json"
        return self.queue_dir / "queue_all.json"

    def _load_queue(self):
        """Load queue from disk."""
        # Load all provider queue files
        for queue_file in self.queue_dir.glob("queue_*.json"):
            try:
                with open(queue_file) as f:
                    data = json.load(f)
                for item_data in data.get('items', []):
                    item = QueueItem.model_validate(item_data)
                    # Only load pending/in_progress items
                    if item.status in (QueueItemStatus.PENDING, QueueItemStatus.IN_PROGRESS):
                        # Reset in_progress to pending on load (interrupted session)
                        if item.status == QueueItemStatus.IN_PROGRESS:
                            item.status = QueueItemStatus.PENDING
                        self._items[item.unique_key] = item
            except Exception as e:
                logger.warning(f"Error loading queue file {queue_file}: {e}")

    def _save_queue(self, provider: Optional[str] = None):
        """Save queue to disk."""
        # Group items by provider
        by_provider: Dict[str, List[QueueItem]] = {}
        for item in self._items.values():
            prov = item.fingerprint.provider
            if provider and prov != provider:
                continue
            if prov not in by_provider:
                by_provider[prov] = []
            by_provider[prov].append(item)

        # Save each provider's queue
        for prov, items in by_provider.items():
            queue_file = self._get_queue_file(prov)
            data = {
                'provider': prov,
                'updated_at': datetime.utcnow().isoformat(),
                'items': [item.model_dump(mode='json') for item in items]
            }
            with open(queue_file, 'w') as f:
                json.dump(data, f, indent=2, default=str)

    def add(
        self,
        vehicle: Dict[str, Any],
        provider: str,
        priority: Priority = Priority.NORMAL,
        reason: str = ""
    ) -> QueueItem:
        """
        Add a vehicle to the queue.

        Args:
            vehicle: Vehicle dictionary from scraper
            provider: Provider identifier
            priority: Scrape priority
            reason: Why this vehicle is being queued

        Returns:
            Created or updated QueueItem
        """
        fingerprint = VehicleFingerprint.from_vehicle_dict(vehicle, provider)
        key = fingerprint.unique_key

        if key in self._items:
            # Update existing item if higher priority
            existing = self._items[key]
            if priority.value < existing.priority.value:
                existing.priority = priority
                existing.reason = reason
            return existing

        item = QueueItem(
            fingerprint=fingerprint,
            vehicle_data=vehicle,
            priority=priority,
            reason=reason,
        )
        self._items[key] = item
        self._save_queue(provider)
        return item

    def add_batch(
        self,
        vehicles: List[Dict[str, Any]],
        provider: str,
        priority: Priority = Priority.NORMAL,
        reason: str = ""
    ) -> int:
        """
        Add multiple vehicles to the queue.

        Args:
            vehicles: List of vehicle dictionaries
            provider: Provider identifier
            priority: Scrape priority
            reason: Why these vehicles are being queued

        Returns:
            Number of items added
        """
        added = 0
        for vehicle in vehicles:
            fingerprint = VehicleFingerprint.from_vehicle_dict(vehicle, provider)
            key = fingerprint.unique_key

            if key not in self._items:
                item = QueueItem(
                    fingerprint=fingerprint,
                    vehicle_data=vehicle,
                    priority=priority,
                    reason=reason,
                )
                self._items[key] = item
                added += 1

        if added > 0:
            self._save_queue(provider)
        return added

    def get_next(self, provider: Optional[str] = None) -> Optional[QueueItem]:
        """
        Get next item to process (highest priority, oldest first).

        Args:
            provider: Optional filter by provider

        Returns:
            Next QueueItem or None if queue is empty
        """
        pending = [
            item for item in self._items.values()
            if item.status == QueueItemStatus.PENDING
            and (provider is None or item.fingerprint.provider == provider)
        ]

        if not pending:
            return None

        # Sort by priority (lower = higher priority), then by added_at
        pending.sort(key=lambda x: (x.priority.value, x.added_at))

        item = pending[0]
        item.mark_in_progress()
        self._save_queue(item.fingerprint.provider)
        return item

    def complete(self, item: QueueItem):
        """Mark item as completed and remove from queue."""
        item.mark_completed()
        del self._items[item.unique_key]
        self._save_queue(item.fingerprint.provider)

    def fail(self, item: QueueItem, error: str):
        """Mark item as failed."""
        item.mark_failed(error)
        self._save_queue(item.fingerprint.provider)

    def get_pending_count(self, provider: Optional[str] = None) -> int:
        """Get count of pending items."""
        return sum(
            1 for item in self._items.values()
            if item.status == QueueItemStatus.PENDING
            and (provider is None or item.fingerprint.provider == provider)
        )

    def get_stats(self, provider: Optional[str] = None) -> Dict[str, int]:
        """Get queue statistics."""
        stats = {status.value: 0 for status in QueueItemStatus}
        stats['total'] = 0

        for item in self._items.values():
            if provider and item.fingerprint.provider != provider:
                continue
            stats[item.status.value] += 1
            stats['total'] += 1

        return stats

    def clear(self, provider: Optional[str] = None):
        """Clear all items from queue."""
        if provider:
            self._items = {
                k: v for k, v in self._items.items()
                if v.fingerprint.provider != provider
            }
            queue_file = self._get_queue_file(provider)
            if queue_file.exists():
                queue_file.unlink()
        else:
            self._items.clear()
            for queue_file in self.queue_dir.glob("queue_*.json"):
                queue_file.unlink()


class ChangeDetector:
    """
    Detects changes between overview scan and cached data.

    Compares lightweight overview data with existing cache to determine
    which vehicles need full price scraping.
    """

    def __init__(
        self,
        cache_dir: str = "output",
        freshness_days: int = 7
    ):
        """
        Initialize change detector.

        Args:
            cache_dir: Directory containing cached price data
            freshness_days: Days before cached data is considered stale
        """
        self.cache_dir = Path(cache_dir)
        self.freshness_threshold = timedelta(days=freshness_days)

    def _load_cached_offers(self, provider: str) -> Dict[str, Dict[str, Any]]:
        """Load cached offers for a provider."""
        # Map provider to cache file
        cache_files = {
            'toyota_nl': 'toyota_prices.json',
            'suzuki_nl': 'suzuki_prices.json',
            'ayvens_nl': 'ayvens_toyota_prices.json',  # or ayvens_suzuki_prices.json
            'leasys_nl': 'leasys_toyota_prices.json',  # or leasys_suzuki_prices.json
        }

        cached = {}

        # Try main cache file
        cache_file = cache_files.get(provider)
        if cache_file:
            self._load_cache_file(self.cache_dir / cache_file, provider, cached)

        # For multi-brand providers, also check brand-specific files
        if provider in ('ayvens_nl', 'leasys_nl'):
            for brand in ('toyota', 'suzuki'):
                brand_file = self.cache_dir / f"{provider.split('_')[0]}_{brand}_prices.json"
                self._load_cache_file(brand_file, provider, cached)

        return cached

    def _load_cache_file(
        self,
        cache_file: Path,
        provider: str,
        cached: Dict[str, Dict[str, Any]]
    ):
        """Load a single cache file into the cached dict."""
        if not cache_file.exists():
            return

        try:
            with open(cache_file) as f:
                data = json.load(f)

            for offer in data:
                fp = VehicleFingerprint.from_vehicle_dict(offer, provider)
                cached[fp.unique_key] = {
                    'fingerprint': fp,
                    'offer': offer,
                    'scraped_at': offer.get('scraped_at'),
                }
        except Exception as e:
            logger.warning(f"Error loading cache file {cache_file}: {e}")

    def _get_scraped_at(self, offer: Dict[str, Any]) -> Optional[datetime]:
        """Extract scraped_at timestamp from offer."""
        scraped_at = offer.get('scraped_at')
        if scraped_at:
            if isinstance(scraped_at, str):
                try:
                    return datetime.fromisoformat(scraped_at.replace('Z', '+00:00'))
                except ValueError:
                    pass
            elif isinstance(scraped_at, datetime):
                return scraped_at
        return None

    def detect_changes(
        self,
        overview_vehicles: List[Dict[str, Any]],
        provider: str,
        brand: Optional[str] = None,
    ) -> ChangeDetectionResult:
        """
        Compare overview scan with cached data.

        Args:
            overview_vehicles: Vehicles from overview scrape
            provider: Provider identifier
            brand: Optional brand filter for multi-brand providers

        Returns:
            ChangeDetectionResult with categorized vehicles
        """
        result = ChangeDetectionResult(provider=provider)

        # Load cached data
        cached = self._load_cached_offers(provider)

        # Create fingerprints for overview vehicles
        overview_keys: Set[str] = set()
        overview_by_key: Dict[str, VehicleFingerprint] = {}

        for vehicle in overview_vehicles:
            # Apply brand filter if specified
            if brand and vehicle.get('brand', '').lower() != brand.lower():
                continue

            fp = VehicleFingerprint.from_vehicle_dict(vehicle, provider)
            overview_keys.add(fp.unique_key)
            overview_by_key[fp.unique_key] = fp

        cached_keys = set(cached.keys())
        now = datetime.utcnow()

        # Categorize vehicles
        for key in overview_keys:
            fp = overview_by_key[key]

            if key not in cached_keys:
                # New vehicle
                result.new_vehicles.append(fp)
            else:
                # Existing vehicle - check for changes
                cached_entry = cached[key]
                cached_fp = cached_entry['fingerprint']

                # Check if fingerprint changed (e.g., URL structure changed)
                if fp.fingerprint_hash != cached_fp.fingerprint_hash:
                    result.changed_vehicles.append(fp)
                else:
                    # Check staleness
                    scraped_at = self._get_scraped_at(cached_entry['offer'])
                    if scraped_at and (now - scraped_at) > self.freshness_threshold:
                        result.stale_vehicles.append(fp)
                    else:
                        result.unchanged_vehicles.append(fp)

        # Find removed vehicles
        for key in cached_keys - overview_keys:
            cached_fp = cached[key]['fingerprint']
            # Apply brand filter
            if brand and cached_fp.brand.lower() != brand.lower():
                continue
            result.removed_vehicles.append(cached_fp)

        return result

    def create_queue_from_changes(
        self,
        result: ChangeDetectionResult,
        overview_vehicles: List[Dict[str, Any]],
        provider: str,
    ) -> ScrapeQueue:
        """
        Create a scrape queue from change detection results.

        Args:
            result: ChangeDetectionResult from detect_changes
            overview_vehicles: Original vehicle dictionaries
            provider: Provider identifier

        Returns:
            Populated ScrapeQueue
        """
        queue = ScrapeQueue()

        # Index vehicles by key for lookup
        vehicles_by_key = {}
        for vehicle in overview_vehicles:
            fp = VehicleFingerprint.from_vehicle_dict(vehicle, provider)
            vehicles_by_key[fp.unique_key] = vehicle

        # Add new vehicles (highest priority)
        for fp in result.new_vehicles:
            if fp.unique_key in vehicles_by_key:
                queue.add(
                    vehicles_by_key[fp.unique_key],
                    provider,
                    Priority.CRITICAL,
                    reason="new_vehicle"
                )

        # Add changed vehicles (high priority)
        for fp in result.changed_vehicles:
            if fp.unique_key in vehicles_by_key:
                queue.add(
                    vehicles_by_key[fp.unique_key],
                    provider,
                    Priority.HIGH,
                    reason="changed"
                )

        # Add stale vehicles (normal priority)
        for fp in result.stale_vehicles:
            if fp.unique_key in vehicles_by_key:
                queue.add(
                    vehicles_by_key[fp.unique_key],
                    provider,
                    Priority.NORMAL,
                    reason="stale"
                )

        return queue
