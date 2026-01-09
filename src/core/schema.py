"""
Unified data schema for lease price scraping.

This module defines the normalized data models used across all scrapers,
ensuring consistent data structure regardless of the source provider.
"""

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field, field_validator, model_validator
import hashlib
import json


class Provider(str, Enum):
    """Supported lease providers."""
    TOYOTA_NL = "toyota_nl"
    SUZUKI_NL = "suzuki_nl"
    AYVENS_NL = "ayvens_nl"
    LEASYS_NL = "leasys_nl"
    # Future providers
    TOYOTA_DE = "toyota_de"
    TOYOTA_BE = "toyota_be"


class Country(str, Enum):
    """Supported countries."""
    NL = "NL"  # Netherlands
    DE = "DE"  # Germany
    BE = "BE"  # Belgium
    FR = "FR"  # France
    GB = "GB"  # United Kingdom


class Currency(str, Enum):
    """Supported currencies."""
    EUR = "EUR"
    GBP = "GBP"
    CHF = "CHF"


class FuelType(str, Enum):
    """Vehicle fuel types."""
    PETROL = "petrol"
    DIESEL = "diesel"
    HYBRID = "hybrid"
    PLUGIN_HYBRID = "plugin_hybrid"
    ELECTRIC = "electric"
    UNKNOWN = "unknown"


class Transmission(str, Enum):
    """Vehicle transmission types."""
    MANUAL = "manual"
    AUTOMATIC = "automatic"
    UNKNOWN = "unknown"


class VehicleCondition(str, Enum):
    """Vehicle condition (new or used)."""
    NEW = "new"
    USED = "used"
    UNKNOWN = "unknown"


class PricePoint(BaseModel):
    """A single price point for a specific duration/mileage combination."""
    duration_months: int = Field(..., ge=12, le=84, description="Contract duration in months")
    km_per_year: int = Field(..., ge=5000, le=50000, description="Annual mileage allowance")
    monthly_price: float = Field(..., gt=0, description="Monthly lease price in local currency")

    @property
    def key(self) -> str:
        """Generate a key for this price point (e.g., '36_10000')."""
        return f"{self.duration_months}_{self.km_per_year}"


class PriceMatrix(BaseModel):
    """
    Complete price matrix for a vehicle across all duration/mileage combinations.

    Stores prices in a dictionary format compatible with existing scrapers:
    {"36_10000": 399.0, "48_15000": 449.0, ...}
    """
    prices: Dict[str, float] = Field(default_factory=dict)

    @field_validator('prices')
    @classmethod
    def validate_prices(cls, v: Dict[str, float]) -> Dict[str, float]:
        """Validate price matrix format and values."""
        for key, price in v.items():
            # Validate key format: "duration_mileage"
            parts = key.split('_')
            if len(parts) != 2:
                raise ValueError(f"Invalid price key format: {key}. Expected 'duration_mileage'")
            try:
                duration = int(parts[0])
                mileage = int(parts[1])
            except ValueError:
                raise ValueError(f"Invalid price key format: {key}. Duration and mileage must be integers")

            # Validate reasonable ranges
            if duration < 12 or duration > 84:
                raise ValueError(f"Duration {duration} outside valid range (12-84 months)")
            if mileage < 5000 or mileage > 50000:
                raise ValueError(f"Mileage {mileage} outside valid range (5000-50000 km)")
            if price <= 0 or price > 5000:
                raise ValueError(f"Price {price} outside valid range (0-5000 EUR)")
        return v

    def get_price(self, duration: int, km: int) -> Optional[float]:
        """Get price for specific duration/km combination."""
        key = f"{duration}_{km}"
        return self.prices.get(key)

    def get_all_prices(self) -> List[PricePoint]:
        """Convert matrix to list of PricePoint objects."""
        points = []
        for key, price in self.prices.items():
            parts = key.split('_')
            points.append(PricePoint(
                duration_months=int(parts[0]),
                km_per_year=int(parts[1]),
                monthly_price=price
            ))
        return sorted(points, key=lambda p: (p.duration_months, p.km_per_year))

    def get_cheapest(self) -> Optional[PricePoint]:
        """Get the cheapest price point."""
        if not self.prices:
            return None
        min_key = min(self.prices, key=self.prices.get)
        parts = min_key.split('_')
        return PricePoint(
            duration_months=int(parts[0]),
            km_per_year=int(parts[1]),
            monthly_price=self.prices[min_key]
        )

    @property
    def min_price(self) -> Optional[float]:
        """Get minimum price in matrix."""
        return min(self.prices.values()) if self.prices else None

    @property
    def max_price(self) -> Optional[float]:
        """Get maximum price in matrix."""
        return max(self.prices.values()) if self.prices else None


class LeaseOffer(BaseModel):
    """
    Unified lease offer model representing a single vehicle configuration.

    This is the normalized schema used across all providers, enabling
    consistent comparison and analysis regardless of source.
    """
    # === Required identification fields ===
    provider: Provider = Field(..., description="Source provider identifier")
    country: Country = Field(..., description="Country code (ISO 3166-1 alpha-2)")

    # === Vehicle identification ===
    brand: str = Field(..., min_length=1, description="Vehicle manufacturer (e.g., 'Toyota', 'Suzuki')")
    model: str = Field(..., min_length=1, description="Vehicle model name (e.g., 'Yaris', 'Aygo X')")
    variant: str = Field(default="", description="Full variant/trim description from source")
    edition_name: str = Field(default="", description="Normalized edition name for matching (e.g., 'Active', 'GR-Sport')")

    # === Vehicle specifications ===
    fuel_type: FuelType = Field(default=FuelType.UNKNOWN, description="Fuel/powertrain type")
    transmission: Transmission = Field(default=Transmission.UNKNOWN, description="Transmission type")
    power: Optional[str] = Field(default=None, description="Engine power (e.g., '116 HP', '1.5 VVT-i')")
    condition: VehicleCondition = Field(default=VehicleCondition.NEW, description="New or used vehicle")

    # === Pricing ===
    price_matrix: PriceMatrix = Field(default_factory=PriceMatrix, description="Full price matrix")
    currency: Currency = Field(default=Currency.EUR, description="Price currency")

    # === Source reference ===
    source_url: Optional[str] = Field(default=None, description="URL to the offer on provider website")
    vehicle_id: Optional[str] = Field(default=None, description="Provider-specific vehicle identifier")

    # === Provenance/metadata ===
    scraped_at: datetime = Field(default_factory=datetime.utcnow, description="Timestamp when data was scraped")
    source_hash: Optional[str] = Field(default=None, description="Hash of source data for change detection")
    raw_data: Optional[Dict[str, Any]] = Field(default=None, description="Original raw data from scraper")

    # === Schema versioning ===
    schema_version: str = Field(default="1.0", description="Schema version for compatibility tracking")

    @field_validator('brand', 'model')
    @classmethod
    def normalize_names(cls, v: str) -> str:
        """Normalize brand and model names."""
        return v.strip().title() if v else v

    @field_validator('edition_name')
    @classmethod
    def normalize_edition(cls, v: str) -> str:
        """Normalize edition names for consistent matching."""
        if not v:
            return v
        # Common normalizations
        normalized = v.strip()
        # Handle GR-Sport variations
        if normalized.lower() in ['gr sport', 'gr-sport', 'grsport']:
            return 'GR-Sport'
        return normalized

    @model_validator(mode='after')
    def compute_source_hash(self) -> 'LeaseOffer':
        """Compute source hash if not provided."""
        if self.source_hash is None:
            # Create hash from key identifying fields
            hash_data = {
                'provider': self.provider.value,
                'brand': self.brand,
                'model': self.model,
                'variant': self.variant,
                'edition_name': self.edition_name,
                'prices': self.price_matrix.prices
            }
            hash_str = json.dumps(hash_data, sort_keys=True)
            self.source_hash = hashlib.md5(hash_str.encode()).hexdigest()[:12]
        return self

    def get_price(self, duration: int, km: int) -> Optional[float]:
        """Get price for specific duration/km combination."""
        return self.price_matrix.get_price(duration, km)

    @property
    def cheapest_price(self) -> Optional[float]:
        """Get the cheapest monthly price available."""
        return self.price_matrix.min_price

    @property
    def display_name(self) -> str:
        """Human-readable name for this offer."""
        parts = [self.brand, self.model]
        if self.edition_name:
            parts.append(self.edition_name)
        return ' '.join(parts)

    @property
    def unique_id(self) -> str:
        """Generate unique identifier for this offer."""
        parts = [
            self.provider.value,
            self.brand.lower().replace(' ', '-'),
            self.model.lower().replace(' ', '-'),
            self.edition_name.lower().replace(' ', '-') if self.edition_name else 'base'
        ]
        return '_'.join(parts)

    def to_legacy_dict(self) -> Dict[str, Any]:
        """
        Convert to legacy dictionary format for backward compatibility.

        Returns format compatible with existing JSON cache files.
        """
        return {
            'model': self.model,
            'edition_name': self.edition_name,
            'variant': self.variant,
            'fuel_type': self.fuel_type.value.title() if self.fuel_type != FuelType.UNKNOWN else '',
            'transmission': self.transmission.value.title() if self.transmission != Transmission.UNKNOWN else '',
            'power': self.power,
            'offer_url': self.source_url,
            'price_matrix': self.price_matrix.prices,
            'brand': self.brand,
            'is_new': self.condition == VehicleCondition.NEW,
            'vehicle_id': self.vehicle_id,
        }

    def model_dump_json_safe(self) -> Dict[str, Any]:
        """Export to JSON-serializable dictionary with proper enum handling."""
        data = self.model_dump()
        # Convert enums to values
        data['provider'] = self.provider.value
        data['country'] = self.country.value
        data['currency'] = self.currency.value
        data['fuel_type'] = self.fuel_type.value
        data['transmission'] = self.transmission.value
        data['condition'] = self.condition.value
        # Convert datetime
        data['scraped_at'] = self.scraped_at.isoformat()
        # Flatten price_matrix
        data['price_matrix'] = self.price_matrix.prices
        return data


# === Adapter functions for converting legacy data ===

def fuel_type_from_string(s: str) -> FuelType:
    """Convert string to FuelType enum."""
    if not s:
        return FuelType.UNKNOWN
    s_lower = s.lower()
    if 'electric' in s_lower or s_lower == 'ev':
        return FuelType.ELECTRIC
    if 'plug' in s_lower or 'phev' in s_lower:
        return FuelType.PLUGIN_HYBRID
    if 'hybrid' in s_lower:
        return FuelType.HYBRID
    if 'diesel' in s_lower:
        return FuelType.DIESEL
    if 'petrol' in s_lower or 'benzine' in s_lower or 'gasoline' in s_lower:
        return FuelType.PETROL
    return FuelType.UNKNOWN


def transmission_from_string(s: str) -> Transmission:
    """Convert string to Transmission enum."""
    if not s:
        return Transmission.UNKNOWN
    s_lower = s.lower()
    if 'auto' in s_lower or 'cvt' in s_lower or 'dct' in s_lower:
        return Transmission.AUTOMATIC
    if 'manual' in s_lower or 'handgeschakeld' in s_lower:
        return Transmission.MANUAL
    return Transmission.UNKNOWN


def create_offer_from_toyota(data: Dict[str, Any]) -> LeaseOffer:
    """Create LeaseOffer from Toyota scraper data."""
    return LeaseOffer(
        provider=Provider.TOYOTA_NL,
        country=Country.NL,
        brand="Toyota",
        model=data.get('model', ''),
        variant=data.get('edition_slug', ''),
        edition_name=data.get('edition_name', ''),
        fuel_type=fuel_type_from_string(data.get('fuel_type', '')),
        transmission=transmission_from_string(data.get('transmission', '')),
        power=data.get('power'),
        condition=VehicleCondition.NEW,
        price_matrix=PriceMatrix(prices=data.get('price_matrix', {})),
        currency=Currency.EUR,
        source_url=data.get('configurator_url') or data.get('base_url'),
        raw_data=data,
    )


def create_offer_from_suzuki(data: Dict[str, Any]) -> LeaseOffer:
    """Create LeaseOffer from Suzuki scraper data."""
    return LeaseOffer(
        provider=Provider.SUZUKI_NL,
        country=Country.NL,
        brand="Suzuki",
        model=data.get('model', ''),
        variant=data.get('edition_slug', ''),
        edition_name=data.get('edition_name', ''),
        fuel_type=fuel_type_from_string(data.get('fuel_type', '')),
        transmission=transmission_from_string(data.get('transmission', '')),
        power=data.get('power'),
        condition=VehicleCondition.NEW,
        price_matrix=PriceMatrix(prices=data.get('price_matrix', {})),
        currency=Currency.EUR,
        source_url=data.get('configurator_url') or data.get('base_url'),
        raw_data=data,
    )


def create_offer_from_ayvens(data: Dict[str, Any]) -> LeaseOffer:
    """Create LeaseOffer from Ayvens scraper data."""
    is_new = data.get('is_new', True)
    return LeaseOffer(
        provider=Provider.AYVENS_NL,
        country=Country.NL,
        brand=data.get('brand', 'Toyota'),
        model=data.get('model', ''),
        variant=data.get('variant', ''),
        edition_name=data.get('edition_name', ''),
        fuel_type=fuel_type_from_string(data.get('fuel_type', '')),
        transmission=transmission_from_string(data.get('transmission', '')),
        power=data.get('power'),
        condition=VehicleCondition.NEW if is_new else VehicleCondition.USED,
        price_matrix=PriceMatrix(prices=data.get('price_matrix', {})),
        currency=Currency.EUR,
        source_url=data.get('offer_url'),
        vehicle_id=data.get('vehicle_id'),
        raw_data=data,
    )


def create_offer_from_leasys(data: Dict[str, Any]) -> LeaseOffer:
    """Create LeaseOffer from Leasys scraper data."""
    return LeaseOffer(
        provider=Provider.LEASYS_NL,
        country=Country.NL,
        brand=data.get('brand', 'Toyota'),
        model=data.get('model', ''),
        variant=data.get('variant', ''),
        edition_name=data.get('edition_name', ''),
        fuel_type=fuel_type_from_string(data.get('fuel_type', '')),
        transmission=transmission_from_string(data.get('transmission', '')),
        power=None,  # Leasys doesn't provide power info
        condition=VehicleCondition.NEW,
        price_matrix=PriceMatrix(prices=data.get('price_matrix', {})),
        currency=Currency.EUR,
        source_url=data.get('offer_url'),
        raw_data=data,
    )


def convert_legacy_offers(
    offers: List[Dict[str, Any]],
    provider: str
) -> List[LeaseOffer]:
    """
    Convert a list of legacy offer dictionaries to LeaseOffer objects.

    Args:
        offers: List of dictionaries from legacy scraper output
        provider: Provider name ('toyota', 'suzuki', 'ayvens', 'leasys')

    Returns:
        List of LeaseOffer objects
    """
    converters = {
        'toyota': create_offer_from_toyota,
        'suzuki': create_offer_from_suzuki,
        'ayvens': create_offer_from_ayvens,
        'leasys': create_offer_from_leasys,
    }

    converter = converters.get(provider.lower())
    if not converter:
        raise ValueError(f"Unknown provider: {provider}")

    return [converter(offer) for offer in offers]
