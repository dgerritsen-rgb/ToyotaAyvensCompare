# Improvement Plan for ToyAyPricecompare

## Current State Summary

- **Implemented:** 4 scrapers (Toyota.nl, Suzuki.nl, Ayvens.com, Leasys.com)
- **Coverage:** Netherlands only, Toyota and Suzuki brands focused
- **Architecture:** Standalone scripts with repeated patterns
- **Data Storage:** JSON cache files with basic metadata
- **Testing:** Exploratory debug scripts (not automated tests)

---

## Phase 1: Unified Data Schema & Normalization

| Task | Priority | Description |
|------|----------|-------------|
| 1.1 Create unified `LeaseOffer` schema | High | Pydantic model with all required fields (provider, country, make, model, version, contract params, price, currency, timestamp, source_url) |
| 1.2 Add data provenance fields | High | `scraped_at`, `source_hash`, `raw_data_reference` |
| 1.3 Normalize existing scrapers | High | Refactor Toyota/Suzuki/Ayvens/Leasys to use unified schema |
| 1.4 Add data validation layer | Medium | Pydantic validators for price ranges, required fields |
| 1.5 Create schema versioning | Low | Track schema changes for historical compatibility |

---

## Phase 2: Modular Scraping Framework

| Task | Priority | Description |
|------|----------|-------------|
| 2.1 Create `BaseScraper` abstract class | High | Common interface: `discover_vehicles()`, `extract_prices()`, `get_price_matrix()` |
| 2.2 Extract Selenium utilities to core module | High | `core/browser.py` - WebDriver setup, cookie handling, waiting utilities |
| 2.3 Create provider registry system | High | Dynamic loading of provider scrapers |
| 2.4 Refactor Toyota scraper to new architecture | Medium | Extend BaseScraper, move config to separate file |
| 2.5 Refactor remaining scrapers | Medium | Ayvens, Leasys, Suzuki to new architecture |
| 2.6 Add scraper plugin system | Low | Allow adding scrapers without modifying core code |

### Proposed Structure

```
src/
├── core/
│   ├── __init__.py
│   ├── base_scraper.py      # Abstract base class
│   ├── browser.py           # Selenium utilities
│   ├── schema.py            # Unified data models
│   └── registry.py          # Provider registration
├── providers/
│   ├── __init__.py
│   ├── nl/
│   │   ├── toyota.py
│   │   ├── suzuki.py
│   │   ├── ayvens.py
│   │   └── leasys.py
│   └── de/                  # Future: Germany
├── config/
│   ├── providers.yaml       # Provider configurations
│   └── schema.yaml          # Schema definitions
└── main.py                  # Entry point
```

---

## Phase 3: Provider Configuration Model

| Task | Priority | Description |
|------|----------|-------------|
| 3.1 Design provider config schema | High | YAML/JSON format for provider definitions |
| 3.2 Implement config loader | High | Parse and validate provider configs |
| 3.3 Add entry points configuration | Medium | URLs, discovery paths per provider |
| 3.4 Add scraping frequency settings | Medium | Per-provider update intervals |
| 3.5 Create provider templates | Low | Templates for HTML-based vs API-based scrapers |

### Example Provider Config

```yaml
providers:
  toyota_nl:
    country: NL
    brand: Toyota
    entry_point: https://www.toyota.nl/private-lease/modellen
    scraper_type: selenium
    update_frequency: daily
    rate_limit:
      requests_per_minute: 10
      delay_between_pages: 3
    price_matrix:
      durations: [24, 36, 48, 60, 72]
      mileages: [5000, 10000, 15000, 20000, 25000, 30000]
```

---

## Phase 4: Historical Data Tracking & Change Detection

| Task | Priority | Description |
|------|----------|-------------|
| 4.1 Design historical data storage | High | SQLite or Parquet for time-series data |
| 4.2 Implement price change detection | High | Track price changes per vehicle over time |
| 4.3 Add availability tracking | High | Detect when vehicles appear/disappear |
| 4.4 Create trend analysis utilities | Medium | Price trends, average changes |
| 4.5 Add structural change detection | Medium | Detect when website structure changes |
| 4.6 Build historical data export | Low | Export trends to CSV/API |

### Proposed History Schema

```python
class PriceHistory:
    offer_id: str           # Unique vehicle identifier
    provider: str
    observed_at: datetime
    price_matrix: dict
    price_change: float     # Delta from previous observation
    availability: str       # "available", "removed", "new"
```

---

## Phase 5: Automated Testing & Monitoring

| Task | Priority | Description |
|------|----------|-------------|
| 5.1 Create pytest test suite | High | Unit tests for scrapers, schema validation |
| 5.2 Add scraper health checks | High | Verify scrapers can connect and extract data |
| 5.3 Implement data quality validation | High | Price ranges, field completeness, outlier detection |
| 5.4 Add structural breakage detection | Medium | Alert when expected elements missing |
| 5.5 Create monitoring dashboard/reports | Medium | Scraper status, last run times, error rates |
| 5.6 Add alerting system | Low | Email/Slack notifications on failures |

### Test Categories

- **Unit tests:** Schema validation, data transformations
- **Integration tests:** Individual scraper functionality
- **Smoke tests:** All scrapers can fetch at least one vehicle
- **Data quality tests:** Prices within expected ranges, no nulls

---

## Phase 6: Multi-Country Support

| Task | Priority | Description |
|------|----------|-------------|
| 6.1 Add country abstraction to schema | Medium | Country code in all data models |
| 6.2 Create country-specific configurations | Medium | Currency, language, URL patterns |
| 6.3 Implement German Toyota scraper | Medium | toyota.de as proof of concept |
| 6.4 Add currency handling | Medium | EUR, GBP, CHF support |
| 6.5 Create localization utilities | Low | Handle German/French/Dutch text parsing |

### Target Countries (by priority)

1. Netherlands (NL) - Implemented
2. Germany (DE) - Large market
3. Belgium (BE) - Similar to NL
4. France (FR) - Large market
5. UK (GB) - Right-hand drive market

---

## Phase 7: Compliance & Performance

| Task | Priority | Description |
|------|----------|-------------|
| 7.1 Implement robots.txt checking | High | Parse and respect robots.txt before scraping |
| 7.2 Add configurable rate limiting | High | Per-provider request throttling |
| 7.3 Implement exponential backoff | Medium | Retry with increasing delays on errors |
| 7.4 Add request caching layer | Medium | Cache HTML responses to reduce requests |
| 7.5 Implement parallel scraping limits | Medium | Max concurrent scrapers |
| 7.6 Add scraper sandboxing | Low | Isolate scraper execution |

---

## Phase 8: Documentation

| Task | Priority | Description |
|------|----------|-------------|
| 8.1 Write architecture overview | High | System design, data flow diagrams |
| 8.2 Create "Adding New Provider" guide | High | Step-by-step instructions |
| 8.3 Document configuration options | Medium | All config parameters explained |
| 8.4 Add API documentation | Medium | If API is planned |
| 8.5 Create troubleshooting guide | Low | Common issues and solutions |

---

## Recommended Implementation Order

```
Week 1-2:  Phase 1 (Schema) + Phase 7.1-7.2 (robots.txt, rate limiting)
Week 3-4:  Phase 2 (Framework refactor)
Week 5-6:  Phase 3 (Config model) + Phase 5.1-5.3 (Testing)
Week 7-8:  Phase 4 (Historical tracking)
Week 9+:   Phase 6 (Multi-country) + Phase 8 (Documentation)
```

---

## Quick Wins (Can implement immediately)

1. **Add robots.txt checking** - Simple utility, high compliance value
2. **Create unified schema** - Pydantic models, improves data quality
3. **Convert test files to pytest** - Structure existing tests properly
4. **Add price range validation** - Catch scraper errors early
5. **Implement exponential backoff** - Better error handling

---

## Success Criteria

- [ ] New provider can be added with minimal manual coding
- [ ] Scraper breakage due to website changes is detected within one run cycle
- [ ] Benchmark dataset is updated within defined freshness targets per provider
- [ ] Customers confirm that benchmark data aligns with observed market pricing
- [ ] Maintenance effort decreases over time as AI assisted generation improves
