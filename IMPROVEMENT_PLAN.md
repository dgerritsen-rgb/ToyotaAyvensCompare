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

## Phase 4: Incremental Scraping & Queue System

| Task | Priority | Description |
|------|----------|-------------|
| 4.1 Create overview-only scrape mode | High | Scrape listing pages to get vehicle inventory without full price matrix |
| 4.2 Implement change detection | High | Compare overview data with cache to identify new/changed/removed vehicles |
| 4.3 Build scrape queue system | High | Queue vehicles needing full price scrape, with priority ordering |
| 4.4 Add queue persistence | High | Persist queue to disk (JSON/SQLite) for resumable scraping |
| 4.5 Implement queue worker | Medium | Process queue items with configurable concurrency and rate limiting |
| 4.6 Add fingerprinting for changes | Medium | Hash vehicle attributes to detect changes (edition name, URL structure) |
| 4.7 Create freshness policy | Medium | Define max age before forced rescrape (e.g., 7 days) |
| 4.8 Build queue monitoring/reporting | Low | Report queue depth, estimated time, scrape efficiency stats |

### Proposed Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Overview       │     │  Change          │     │  Scrape         │
│  Scraper        │────▶│  Detector        │────▶│  Queue          │
│  (lightweight)  │     │  (compare cache) │     │  (prioritized)  │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                                                          ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Price Cache    │◀────│  Queue Worker    │◀────│  Full Price     │
│  (JSON files)   │     │  (rate limited)  │     │  Scraper        │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

### Change Detection Logic

```python
class ChangeDetector:
    def detect_changes(self, overview: List[Vehicle], cache: List[LeaseOffer]) -> ScrapeQueue:
        # New vehicles: in overview but not in cache
        # Removed vehicles: in cache but not in overview
        # Changed vehicles: fingerprint mismatch (edition name, URL)
        # Stale vehicles: last_scraped > freshness_threshold

        return ScrapeQueue(
            new_vehicles=[...],      # Priority 1: scrape immediately
            changed_vehicles=[...],  # Priority 2: scrape soon
            stale_vehicles=[...],    # Priority 3: scrape when idle
        )
```

### Example Usage

```bash
# Step 1: Quick overview scan (fast, low traffic)
python scrape.py --overview-only --provider toyota_nl

# Step 2: Detect what needs updating
python scrape.py --detect-changes --provider toyota_nl

# Step 3: Process the queue (full price scrapes)
python scrape.py --process-queue --provider toyota_nl --max-items 10
```

---

## Phase 5: Historical Data Tracking & Trend Analysis

| Task | Priority | Description |
|------|----------|-------------|
| 5.1 Design historical data storage | High | SQLite or Parquet for time-series data |
| 5.2 Implement price change detection | High | Track price changes per vehicle over time |
| 5.3 Add availability tracking | High | Detect when vehicles appear/disappear |
| 5.4 Create trend analysis utilities | Medium | Price trends, average changes |
| 5.5 Add structural change detection | Medium | Detect when website structure changes |
| 5.6 Build historical data export | Low | Export trends to CSV/API |

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

## Phase 6: Automated Testing & Monitoring

| Task | Priority | Description |
|------|----------|-------------|
| 6.1 Create pytest test suite | High | Unit tests for scrapers, schema validation |
| 6.2 Add scraper health checks | High | Verify scrapers can connect and extract data |
| 6.3 Implement data quality validation | High | Price ranges, field completeness, outlier detection |
| 6.4 Add structural breakage detection | Medium | Alert when expected elements missing |
| 6.5 Create monitoring dashboard/reports | Medium | Scraper status, last run times, error rates |
| 6.6 Add alerting system | Low | Email/Slack notifications on failures |

### Test Categories

- **Unit tests:** Schema validation, data transformations
- **Integration tests:** Individual scraper functionality
- **Smoke tests:** All scrapers can fetch at least one vehicle
- **Data quality tests:** Prices within expected ranges, no nulls

---

## Phase 7: Multi-Country Support

| Task | Priority | Description |
|------|----------|-------------|
| 7.1 Add country abstraction to schema | Medium | Country code in all data models |
| 7.2 Create country-specific configurations | Medium | Currency, language, URL patterns |
| 7.3 Implement German Toyota scraper | Medium | toyota.de as proof of concept |
| 7.4 Add currency handling | Medium | EUR, GBP, CHF support |
| 7.5 Create localization utilities | Low | Handle German/French/Dutch text parsing |

### Target Countries (by priority)

1. Netherlands (NL) - Implemented
2. Germany (DE) - Large market
3. Belgium (BE) - Similar to NL
4. France (FR) - Large market
5. UK (GB) - Right-hand drive market

---

## Phase 8: Compliance & Performance

| Task | Priority | Description |
|------|----------|-------------|
| 8.1 Implement robots.txt checking | High | Parse and respect robots.txt before scraping |
| 8.2 Add configurable rate limiting | High | Per-provider request throttling |
| 8.3 Implement exponential backoff | Medium | Retry with increasing delays on errors |
| 8.4 Add request caching layer | Medium | Cache HTML responses to reduce requests |
| 8.5 Implement parallel scraping limits | Medium | Max concurrent scrapers |
| 8.6 Add scraper sandboxing | Low | Isolate scraper execution |

---

## Phase 9: Documentation

| Task | Priority | Description |
|------|----------|-------------|
| 9.1 Write architecture overview | High | System design, data flow diagrams |
| 9.2 Create "Adding New Provider" guide | High | Step-by-step instructions |
| 9.3 Document configuration options | Medium | All config parameters explained |
| 9.4 Add API documentation | Medium | If API is planned |
| 9.5 Create troubleshooting guide | Low | Common issues and solutions |

---

## Recommended Implementation Order

```
✅ DONE:   Phase 1 (Schema) + Phase 2 (Framework) + Phase 3 (Config) + Phase 4 (Queue)

NEXT:
Phase 8:   Compliance (robots.txt, rate limiting)
Phase 5:   Historical tracking (builds on Phase 4 change detection)
Phase 6:   Automated testing
Phase 7:   Multi-country support
Phase 9:   Documentation
```

---

## Quick Wins (Can implement immediately)

1. ~~Add robots.txt checking~~ - ✅ Done (src/core/robots.py)
2. ~~Create unified schema~~ - ✅ Done
3. **Convert test files to pytest** - Structure existing tests properly
4. **Add price range validation** - Catch scraper errors early
5. **Implement exponential backoff** - Better error handling
6. ~~Overview-only scrape mode~~ - ✅ Done (Phase 4 complete)

---

## Success Criteria

- [x] Unified data schema implemented
- [x] Modular scraping framework in place
- [x] Provider configuration system working
- [ ] New provider can be added with minimal manual coding
- [ ] Scraper breakage due to website changes is detected within one run cycle
- [x] Incremental scraping reduces provider traffic by 80%+
- [ ] Benchmark dataset is updated within defined freshness targets per provider
- [ ] Customers confirm that benchmark data aligns with observed market pricing
- [ ] Maintenance effort decreases over time as AI assisted generation improves
