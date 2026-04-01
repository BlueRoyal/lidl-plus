**This project is unofficial and is not related in any way to Lidl. It was developed by reversed engineered requests and can stop working at anytime!**

# Lidl Plus — Python API & Home Assistant Integration

[![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/Andre0512/lidl-plus/python-check.yml?branch=main&label=checks)](https://github.com/Andre0512/lidl-plus/actions/workflows/python-check.yml)
[![PyPI - Status](https://img.shields.io/pypi/status/lidl-plus)](https://pypi.org/project/lidl-plus)
[![PyPI](https://img.shields.io/pypi/v/lidl-plus?color=blue)](https://pypi.org/project/lidl-plus)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/lidl-plus)](https://www.python.org/)
[![PyPI - License](https://img.shields.io/pypi/l/lidl-plus)](https://github.com/Andre0512/lidl-plus/blob/main/LICENCE)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/lidl-plus)](https://pypistats.org/packages/lidl-plus)
[![HA Integration](https://img.shields.io/badge/Home%20Assistant-Integration-41BDF5?logo=homeassistant)](custom_components/lidl_plus)

This repository provides two things:

- **Python library & CLI** — fetch receipts, analytics and coupons from the Lidl Plus API
- **Home Assistant custom integration** — 20+ sensors, a sidebar panel with charts, receipt browser and product tracker

## Installation
```bash
pip install "lidl-plus[auth]"
```

### Python 3.14 compatibility
Python 3.14 requires specific package versions:
```bash
pip install "setuptools==71.0.4" "blinker==1.5"
```

## Authentication
To log in to Lidl Plus we simulate the app login using a browser. After receiving the token once, it can be reused without a browser.

#### Prerequisites
* One of the supported browsers installed:
  - Google Chrome / Chromium / Microsoft Edge
  - Mozilla Firefox
* Additional packages: `pip install "lidl-plus[auth]"`

#### Commandline-Tool
```bash
$ lidl-plus --language=de --country=DE --user=your@email.com auth
Enter your lidl plus password:
------------------------- refresh token ------------------------
B7E3A1F9C2D4E8B0A5F1C3D7E2B4A9F0C6D1E5B8A2F7C0D3E6B9A4F2C5D8E1B3
----------------------------------------------------------------
```

#### Python
```python
from lidlplus import LidlPlusApi

lidl = LidlPlusApi(language="de", country="DE")
lidl.login(email="your@email.com", password="password", verify_token_func=lambda: input("Insert code: "))
print(lidl.refresh_token)
```

## Usage

### Receipts

#### Commandline-Tool
```bash
# Last receipt
lidl-plus --language=de --country=DE --refresh-token=XXXXX receipt

# All receipts
lidl-plus --language=de --country=DE --refresh-token=XXXXX receipt --all
```

#### Python
```python
from lidlplus import LidlPlusApi

lidl = LidlPlusApi("de", "DE", refresh_token="XXXXXXXXXX")

# List of receipts (metadata only)
for receipt in lidl.tickets():
    print(receipt["id"], receipt["date"], receipt["totalAmount"])

# Full receipt detail
ticket = lidl.ticket("TICKET_ID")

# Parse items as structured list
items = lidl.parse_ticket_items(ticket)
# [{"id": "0082052", "name": "Feldsalat", "unit_price": "1,49", "quantity": 1, "tax_type": "A"}, ...]
```

### Cache & Analytics

Sync tickets locally for fast analytics without repeated API calls:

#### Commandline-Tool
```bash
# Sync new tickets to cache (only fetches new ones on subsequent runs)
lidl-plus --language=de --country=DE --refresh-token=XXXXX --cache lidlplus_cache.json sync

# Show analytics
lidl-plus --language=de --country=DE --refresh-token=XXXXX --cache lidlplus_cache.json stats
```

#### Python
```python
from lidlplus import LidlPlusApi

lidl = LidlPlusApi("de", "DE", refresh_token="XXXXXXXXXX", cache_file="lidlplus_cache.json")

# Sync only new tickets (fast after first run)
new_count = lidl.sync()

# All items across all receipts (flat list with date + store)
items = lidl.all_ticket_items()

# Price history for a specific article id
history = lidl.price_history("0082052")

# Top 10 most frequently bought items
top = lidl.frequently_bought(limit=10)

# Spending grouped by month {"2026-03": 142.50, ...}
by_month = lidl.spending_by_month()

# Spending grouped by store
by_store = lidl.spending_by_store()

# When was an item last bought
last = lidl.last_seen("0082052")

# Current month spending
this_month = lidl.current_month_spending()

# Average basket value
avg = lidl.average_basket()

# Average days between shopping trips
freq = lidl.shopping_frequency_days()

# Items overdue for restocking (based on average purchase interval)
restock = lidl.restock_suggestions(min_purchases=3)
```

### Coupons

```bash
# List all coupons
lidl-plus --language=de --country=DE --refresh-token=XXXXX coupon

# Activate all available coupons
lidl-plus --language=de --country=DE --refresh-token=XXXXX coupon --all
```

```python
from lidlplus import LidlPlusApi

lidl = LidlPlusApi("de", "DE", refresh_token="XXXXXXXXXX")
for section in lidl.coupons()["sections"]:
    for coupon in section["coupons"]:
        print(coupon["title"], coupon["id"])
```

## CLI Reference
```
options:
  -h, --help                show this help message and exit
  -c CC, --country CC       country (DE, BE, NL, AT, ...)
  -l LANG, --language LANG  language (de, en, fr, it, ...)
  -u USER, --user USER      Lidl Plus login username
  -p XXX, --password XXX    Lidl Plus login password
  --2fa {phone,email}       choose two factor auth method
  -r TOKEN, --refresh-token TOKEN
                            refresh token to authenticate
  --cache FILE              path to local cache file (JSON)
  --skip-verify             skip ssl verification
  --not-accept-legal-terms  not auto accept legal terms updates
  -d, --debug               debug mode (shows browser window)

commands:
  auth                      authenticate and get refresh token
  id                        show loyalty ID
  receipt                   output last receipt as json
  coupon                    list or activate coupons
  sync                      sync new tickets to cache (requires --cache)
  stats                     show analytics from cache (requires --cache)
```

## Home Assistant Integration

A fully featured Home Assistant custom integration is included in `custom_components/lidl_plus/`.

### Features
- **20+ sensors**: spending by month, average basket, food/non-food categories, coupons, price changes, restock suggestions, last receipt, loyalty ID, and more
- **Sidebar panel** with three tabs:
  - **Übersicht**: KPI cards, monthly spending bar chart, food/non-food donut chart, top stores chart
  - **Kassenbons**: all receipts with full item details, filter by store, date range, amount
  - **Artikel**: all products with purchase stats, price trend badges, filter by trend/period, detail modal with price history line chart
- **Services**: `lidl_plus.sync` (force refresh), `lidl_plus.activate_all_coupons`
- Data auto-refreshes every 6 hours; panel updates on each sync

### Setup
1. Copy `custom_components/lidl_plus/` to your HA `/config/custom_components/` directory
2. Copy `www/lidl_plus/` to your HA `/config/www/` directory
3. Add to `configuration.yaml`:
   ```yaml
   panel_custom:
     - name: lidl-plus-panel-element
       sidebar_title: Lidl Plus
       sidebar_icon: mdi:cart
       url_path: lidl-plus
       module_url: /local/lidl_plus/panel.js
   ```
4. Restart Home Assistant
5. Go to **Settings → Devices & Services → Add Integration** and search for *Lidl Plus*
6. Enter your refresh token (obtain via `lidl-plus auth` CLI command)

## Changelog

### 1.1.0 — 2026-04-02
**Home Assistant integration overhaul**
- Redesigned panel UI with Tailwind CSS and Chart.js
- Added **Übersicht** tab: monthly bar chart, food/non-food donut chart, top-stores horizontal bar chart
- Added price trend indicators (↑↓→) on product cards and in detail modal
- Added price history **line chart** in product detail modal
- Added advanced filters: store dropdown, date range, min/max amount (receipts); trend filter, period filter (articles)
- Fixed `sensor.lidl_plus_last_sync` and `sensor.lidl_plus_letzter_einkauf`: now return proper `datetime` objects (HA 2026 compatibility)
- Fixed state class warnings for monetary sensors (now use `SensorStateClass.TOTAL`)
- Extended `data.json` with `spending_by_month`, `spending_by_store`, `food_total`, `nonfood_total`, `avg_basket`, `current_month`
- Fixed panel registration for HA 2026.x (`async_register_panel` removed; now via `panel_custom` in `configuration.yaml`)
- Added `frontend` dependency to `manifest.json`

### 1.0.0
- Initial Home Assistant custom integration
- 19 sensor entities (spending, coupons, price changes, restock suggestions, receipts, products, log, etc.)
- Vendored `_lidlplus` API (no Selenium required in HA environment)
- Panel with receipt browser and product tracker
- Services: `sync`, `activate_all_coupons`
- Fix: German decimal quantities (`1,19`) parsed correctly as float

### 0.4.0
- Fixed Python 3.14 compatibility (`argparse`, `blinker`, `setuptools`)
- Updated ticket detail endpoint to API v3
- Updated login flow for new Lidl accounts page
- Added `parse_ticket_items()` — extract structured items from HTML receipt
- Added cache system (`cache_file` parameter, `sync()`, `cached_tickets()`)
- Added analytics: `all_ticket_items()`, `price_history()`, `frequently_bought()`,
  `spending_by_month()`, `spending_by_store()`, `last_seen()`,
  `current_month_spending()`, `average_basket()`, `shopping_frequency_days()`,
  `restock_suggestions()`
- Added CLI commands: `sync`, `stats`

### 0.3.5
- Initial public release
