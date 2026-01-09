"""
Provider configuration models and loader.

This module defines the configuration schema for lease providers and
provides utilities for loading configs from YAML/JSON files.
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from enum import Enum
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


class ScraperType(str, Enum):
    """Type of scraper implementation."""
    SELENIUM = "selenium"
    API = "api"
    HYBRID = "hybrid"  # Uses both


class UpdateFrequency(str, Enum):
    """How often the provider should be scraped."""
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MANUAL = "manual"


class RateLimitConfig(BaseModel):
    """Rate limiting configuration."""
    requests_per_minute: int = Field(default=30, ge=1, le=120)
    delay_between_pages: float = Field(default=2.0, ge=0.5, le=30.0)
    delay_between_requests: float = Field(default=1.0, ge=0.1, le=10.0)
    max_retries: int = Field(default=3, ge=0, le=10)
    retry_delay: float = Field(default=5.0, ge=1.0, le=60.0)


class PriceMatrixConfig(BaseModel):
    """Price matrix dimensions configuration."""
    durations: List[int] = Field(
        default=[24, 36, 48, 60, 72],
        description="Contract durations in months"
    )
    mileages: List[int] = Field(
        default=[5000, 10000, 15000, 20000, 25000, 30000],
        description="Annual mileage options in km"
    )

    @field_validator('durations')
    @classmethod
    def validate_durations(cls, v: List[int]) -> List[int]:
        for d in v:
            if d < 12 or d > 84:
                raise ValueError(f"Duration {d} outside valid range (12-84 months)")
        return sorted(v)

    @field_validator('mileages')
    @classmethod
    def validate_mileages(cls, v: List[int]) -> List[int]:
        for m in v:
            if m < 5000 or m > 50000:
                raise ValueError(f"Mileage {m} outside valid range (5000-50000 km)")
        return sorted(v)

    @property
    def total_price_points(self) -> int:
        """Total number of price points in the matrix."""
        return len(self.durations) * len(self.mileages)


class SelectorConfig(BaseModel):
    """CSS/XPath selectors for scraping."""
    vehicle_list: Optional[str] = None
    vehicle_card: Optional[str] = None
    price_element: Optional[str] = None
    edition_name: Optional[str] = None
    model_name: Optional[str] = None
    duration_selector: Optional[str] = None
    mileage_selector: Optional[str] = None
    cookie_accept: Optional[List[str]] = None

    class Config:
        extra = "allow"  # Allow additional selectors


class UrlConfig(BaseModel):
    """URL configuration for a provider."""
    base_url: str = Field(..., description="Main website URL")
    overview_url: Optional[str] = Field(None, description="Vehicle listing page")
    api_base: Optional[str] = Field(None, description="API base URL if available")

    # URL templates with placeholders
    model_url_template: Optional[str] = Field(
        None,
        description="Template for model pages, e.g., '{base}/models/{model}'"
    )
    vehicle_url_template: Optional[str] = Field(
        None,
        description="Template for vehicle pages"
    )

    def get_model_url(self, model: str) -> Optional[str]:
        """Generate URL for a specific model."""
        if self.model_url_template:
            return self.model_url_template.format(
                base=self.base_url,
                model=model.lower().replace(' ', '-')
            )
        return None


class BrandConfig(BaseModel):
    """Configuration for a specific brand within a multi-brand provider."""
    name: str
    slug: str  # URL-friendly identifier
    enabled: bool = True
    models: Optional[List[str]] = None  # Known models for this brand


class ProviderConfig(BaseModel):
    """
    Complete configuration for a lease provider.

    This defines all settings needed to scrape a provider, including
    URLs, rate limits, selectors, and supported features.
    """
    # Identification
    id: str = Field(..., description="Unique provider identifier (e.g., 'toyota_nl')")
    name: str = Field(..., description="Human-readable provider name")
    country: str = Field(..., description="ISO country code (e.g., 'NL')")
    currency: str = Field(default="EUR", description="Price currency code")

    # Provider type
    scraper_type: ScraperType = Field(default=ScraperType.SELENIUM)
    is_oem: bool = Field(default=False, description="True if this is an OEM site")
    is_aggregator: bool = Field(default=False, description="True if multi-brand aggregator")

    # URLs
    urls: UrlConfig

    # Supported brands (for aggregators)
    brands: Optional[List[BrandConfig]] = None
    default_brand: Optional[str] = None  # For OEM sites

    # Rate limiting
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)

    # Price matrix
    price_matrix: PriceMatrixConfig = Field(default_factory=PriceMatrixConfig)

    # Update schedule
    update_frequency: UpdateFrequency = Field(default=UpdateFrequency.DAILY)
    cache_ttl_hours: int = Field(default=48, ge=1, le=168)

    # Selectors (optional - can be hardcoded in scraper)
    selectors: Optional[SelectorConfig] = None

    # Feature flags
    features: Dict[str, bool] = Field(default_factory=lambda: {
        "supports_filtering": True,
        "has_price_configurator": True,
        "requires_javascript": True,
        "has_api": False,
    })

    # Additional metadata
    notes: Optional[str] = None
    robots_txt_checked: bool = Field(default=False)
    last_structure_change: Optional[str] = None

    @model_validator(mode='after')
    def validate_brand_config(self) -> 'ProviderConfig':
        """Validate brand configuration consistency."""
        if self.is_aggregator and not self.brands:
            logger.warning(f"Aggregator {self.id} has no brands configured")
        if self.is_oem and not self.default_brand:
            logger.warning(f"OEM {self.id} has no default_brand set")
        return self

    @property
    def request_delay(self) -> float:
        """Get request delay for backward compatibility."""
        return self.rate_limit.delay_between_pages

    def get_brand_config(self, brand_name: str) -> Optional[BrandConfig]:
        """Get configuration for a specific brand."""
        if not self.brands:
            return None
        for brand in self.brands:
            if brand.name.lower() == brand_name.lower():
                return brand
        return None

    def get_enabled_brands(self) -> List[str]:
        """Get list of enabled brand names."""
        if not self.brands:
            return [self.default_brand] if self.default_brand else []
        return [b.name for b in self.brands if b.enabled]


class ConfigManager:
    """
    Manages provider configurations.

    Loads configs from YAML/JSON files and provides lookup by provider ID.
    """

    def __init__(self, config_dir: Optional[str] = None):
        """
        Initialize config manager.

        Args:
            config_dir: Directory containing config files. Defaults to 'config/'
        """
        if config_dir is None:
            # Default to config/ in project root
            config_dir = Path(__file__).parent.parent.parent / "config"
        self.config_dir = Path(config_dir)
        self._configs: Dict[str, ProviderConfig] = {}
        self._loaded = False

    def load_all(self) -> None:
        """Load all configuration files from config directory."""
        if not self.config_dir.exists():
            logger.warning(f"Config directory not found: {self.config_dir}")
            return

        # Load YAML files
        for yaml_file in self.config_dir.glob("*.yaml"):
            self._load_file(yaml_file)
        for yml_file in self.config_dir.glob("*.yml"):
            self._load_file(yml_file)

        # Load JSON files
        for json_file in self.config_dir.glob("*.json"):
            if json_file.name != "providers.json":  # Skip combined file
                self._load_file(json_file)

        # Load combined providers.json if exists
        combined_file = self.config_dir / "providers.json"
        if combined_file.exists():
            self._load_combined_json(combined_file)

        self._loaded = True
        logger.info(f"Loaded {len(self._configs)} provider configurations")

    def _load_file(self, filepath: Path) -> None:
        """Load a single config file."""
        try:
            with open(filepath, 'r') as f:
                if filepath.suffix in ['.yaml', '.yml']:
                    import yaml
                    data = yaml.safe_load(f)
                else:
                    data = json.load(f)

            if data:
                config = ProviderConfig(**data)
                self._configs[config.id] = config
                logger.debug(f"Loaded config: {config.id} from {filepath.name}")

        except ImportError:
            logger.warning("PyYAML not installed, skipping YAML files")
        except Exception as e:
            logger.error(f"Error loading {filepath}: {e}")

    def _load_combined_json(self, filepath: Path) -> None:
        """Load combined providers.json file."""
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)

            providers = data.get('providers', data)
            if isinstance(providers, list):
                for provider_data in providers:
                    config = ProviderConfig(**provider_data)
                    self._configs[config.id] = config
            elif isinstance(providers, dict):
                for provider_id, provider_data in providers.items():
                    if 'id' not in provider_data:
                        provider_data['id'] = provider_id
                    config = ProviderConfig(**provider_data)
                    self._configs[config.id] = config

        except Exception as e:
            logger.error(f"Error loading {filepath}: {e}")

    def get(self, provider_id: str) -> Optional[ProviderConfig]:
        """Get configuration for a provider."""
        if not self._loaded:
            self.load_all()
        return self._configs.get(provider_id)

    def get_all(self) -> Dict[str, ProviderConfig]:
        """Get all loaded configurations."""
        if not self._loaded:
            self.load_all()
        return self._configs.copy()

    def list_providers(self) -> List[str]:
        """List all configured provider IDs."""
        if not self._loaded:
            self.load_all()
        return list(self._configs.keys())

    def register(self, config: ProviderConfig) -> None:
        """Register a provider configuration programmatically."""
        self._configs[config.id] = config

    def save_config(self, config: ProviderConfig, filepath: Optional[Path] = None) -> Path:
        """
        Save a provider configuration to file.

        Args:
            config: Configuration to save
            filepath: Optional specific path, defaults to config_dir/{id}.json

        Returns:
            Path to saved file
        """
        if filepath is None:
            filepath = self.config_dir / f"{config.id}.json"

        self.config_dir.mkdir(parents=True, exist_ok=True)

        with open(filepath, 'w') as f:
            json.dump(config.model_dump(), f, indent=2, default=str)

        return filepath


# Global config manager instance
_config_manager: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    """Get global config manager instance."""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager


def get_provider_config(provider_id: str) -> Optional[ProviderConfig]:
    """Get configuration for a provider by ID."""
    return get_config_manager().get(provider_id)


def list_configured_providers() -> List[str]:
    """List all configured provider IDs."""
    return get_config_manager().list_providers()


# === Default configurations for existing providers ===

def create_toyota_nl_config() -> ProviderConfig:
    """Create default configuration for Toyota Netherlands."""
    return ProviderConfig(
        id="toyota_nl",
        name="Toyota Netherlands",
        country="NL",
        currency="EUR",
        scraper_type=ScraperType.SELENIUM,
        is_oem=True,
        default_brand="Toyota",
        urls=UrlConfig(
            base_url="https://www.toyota.nl",
            overview_url="https://www.toyota.nl/private-lease/modellen",
            model_url_template="{base}/private-lease/modellen/{model}",
        ),
        rate_limit=RateLimitConfig(
            requests_per_minute=20,
            delay_between_pages=2.0,
        ),
        price_matrix=PriceMatrixConfig(
            durations=[24, 36, 48, 60, 72],
            mileages=[5000, 10000, 15000, 20000, 25000, 30000],
        ),
        update_frequency=UpdateFrequency.DAILY,
        cache_ttl_hours=48,
        features={
            "supports_filtering": True,
            "has_price_configurator": True,
            "requires_javascript": True,
            "has_api": False,
        },
        robots_txt_checked=True,
    )


def create_suzuki_nl_config() -> ProviderConfig:
    """Create default configuration for Suzuki Netherlands."""
    return ProviderConfig(
        id="suzuki_nl",
        name="Suzuki Netherlands",
        country="NL",
        currency="EUR",
        scraper_type=ScraperType.SELENIUM,
        is_oem=True,
        default_brand="Suzuki",
        urls=UrlConfig(
            base_url="https://www.suzuki.nl",
            overview_url="https://www.suzuki.nl/auto/private-lease/modellen",
            model_url_template="{base}/auto/private-lease/{model}",
        ),
        rate_limit=RateLimitConfig(
            requests_per_minute=20,
            delay_between_pages=2.0,
        ),
        price_matrix=PriceMatrixConfig(
            durations=[24, 36, 48, 60, 72],
            mileages=[5000, 10000, 15000, 20000, 25000, 30000],
        ),
        update_frequency=UpdateFrequency.DAILY,
        cache_ttl_hours=48,
        robots_txt_checked=True,
    )


def create_ayvens_nl_config() -> ProviderConfig:
    """Create default configuration for Ayvens Netherlands."""
    return ProviderConfig(
        id="ayvens_nl",
        name="Ayvens Netherlands",
        country="NL",
        currency="EUR",
        scraper_type=ScraperType.SELENIUM,
        is_aggregator=True,
        urls=UrlConfig(
            base_url="https://www.ayvens.com",
            overview_url="https://www.ayvens.com/nl-nl/private-lease-showroom/",
        ),
        brands=[
            BrandConfig(name="Toyota", slug="toyota", enabled=True),
            BrandConfig(name="Suzuki", slug="suzuki", enabled=True),
            BrandConfig(name="Fiat", slug="fiat", enabled=False),
        ],
        rate_limit=RateLimitConfig(
            requests_per_minute=30,
            delay_between_pages=1.5,
        ),
        price_matrix=PriceMatrixConfig(
            durations=[24, 36, 48, 60, 72],
            mileages=[5000, 7500, 10000, 15000, 20000, 25000, 30000],  # 7 options
        ),
        update_frequency=UpdateFrequency.DAILY,
        cache_ttl_hours=48,
        robots_txt_checked=True,
    )


def create_leasys_nl_config() -> ProviderConfig:
    """Create default configuration for Leasys Netherlands."""
    return ProviderConfig(
        id="leasys_nl",
        name="Leasys Netherlands",
        country="NL",
        currency="EUR",
        scraper_type=ScraperType.SELENIUM,
        is_aggregator=True,
        urls=UrlConfig(
            base_url="https://store.leasys.com",
            overview_url="https://store.leasys.com/nl/private/",
        ),
        brands=[
            BrandConfig(name="Toyota", slug="toyota", enabled=True),
            BrandConfig(name="Suzuki", slug="suzuki", enabled=True),
            BrandConfig(name="Fiat", slug="fiat", enabled=False),
            BrandConfig(name="Alfa Romeo", slug="alfa-romeo", enabled=False),
        ],
        rate_limit=RateLimitConfig(
            requests_per_minute=20,
            delay_between_pages=2.0,
        ),
        price_matrix=PriceMatrixConfig(
            durations=[24, 36, 48, 60, 72],
            mileages=[5000, 10000, 15000, 20000],  # Only 4 options
        ),
        update_frequency=UpdateFrequency.DAILY,
        cache_ttl_hours=48,
        robots_txt_checked=True,
        notes="Leasys only offers mileages up to 20,000 km/year",
    )


def get_default_configs() -> Dict[str, ProviderConfig]:
    """Get all default provider configurations."""
    return {
        "toyota_nl": create_toyota_nl_config(),
        "suzuki_nl": create_suzuki_nl_config(),
        "ayvens_nl": create_ayvens_nl_config(),
        "leasys_nl": create_leasys_nl_config(),
    }


def initialize_default_configs(config_manager: Optional[ConfigManager] = None) -> None:
    """Register all default configurations with the config manager."""
    if config_manager is None:
        config_manager = get_config_manager()

    for config in get_default_configs().values():
        config_manager.register(config)
