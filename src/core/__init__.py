"""Core module for the lease price scraping framework."""

from .schema import (
    LeaseOffer,
    PriceMatrix,
    Provider,
    Country,
    Currency,
    FuelType,
    Transmission,
    VehicleCondition,
    create_offer_from_toyota,
    create_offer_from_suzuki,
    create_offer_from_ayvens,
    create_offer_from_leasys,
    convert_legacy_offers,
)

from .loader import (
    load_all_offers,
    load_offers_by_brand,
    load_toyota_offers,
    load_suzuki_offers,
    load_ayvens_toyota_offers,
    load_ayvens_suzuki_offers,
    load_leasys_toyota_offers,
    load_leasys_suzuki_offers,
    export_unified_json,
    export_all_unified,
    get_offer_stats,
)

from .browser import (
    BrowserManager,
    browser_session,
)

from .base_scraper import (
    BaseScraper,
    MultiModelScraper,
    MultiBrandScraper,
)

from .registry import (
    ScraperRegistry,
    register_scraper,
    get_scraper,
    list_providers,
    scrape_provider,
)

from .config import (
    ProviderConfig,
    RateLimitConfig,
    PriceMatrixConfig,
    UrlConfig,
    BrandConfig,
    ScraperType,
    UpdateFrequency,
    ConfigManager,
    get_config_manager,
    get_provider_config,
    list_configured_providers,
    get_default_configs,
    initialize_default_configs,
)

from .queue import (
    ScrapeQueue,
    QueueItem,
    VehicleFingerprint,
    ChangeDetector,
    ChangeDetectionResult,
    Priority,
    QueueItemStatus,
)

__all__ = [
    # Schema classes
    "LeaseOffer",
    "PriceMatrix",
    "Provider",
    "Country",
    "Currency",
    "FuelType",
    "Transmission",
    "VehicleCondition",
    # Converters
    "create_offer_from_toyota",
    "create_offer_from_suzuki",
    "create_offer_from_ayvens",
    "create_offer_from_leasys",
    "convert_legacy_offers",
    # Loaders
    "load_all_offers",
    "load_offers_by_brand",
    "load_toyota_offers",
    "load_suzuki_offers",
    "load_ayvens_toyota_offers",
    "load_ayvens_suzuki_offers",
    "load_leasys_toyota_offers",
    "load_leasys_suzuki_offers",
    "export_unified_json",
    "export_all_unified",
    "get_offer_stats",
    # Browser utilities
    "BrowserManager",
    "browser_session",
    # Base scraper classes
    "BaseScraper",
    "MultiModelScraper",
    "MultiBrandScraper",
    # Registry
    "ScraperRegistry",
    "register_scraper",
    "get_scraper",
    "list_providers",
    "scrape_provider",
    # Config
    "ProviderConfig",
    "RateLimitConfig",
    "PriceMatrixConfig",
    "UrlConfig",
    "BrandConfig",
    "ScraperType",
    "UpdateFrequency",
    "ConfigManager",
    "get_config_manager",
    "get_provider_config",
    "list_configured_providers",
    "get_default_configs",
    "initialize_default_configs",
    # Queue system
    "ScrapeQueue",
    "QueueItem",
    "VehicleFingerprint",
    "ChangeDetector",
    "ChangeDetectionResult",
    "Priority",
    "QueueItemStatus",
]
