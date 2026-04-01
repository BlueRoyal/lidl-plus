**This python package is unofficial and is not related in any way to Lidl. It was developed by reversed engineered requests and can stop working at anytime!**

# Python Lidl Plus API
[![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/Andre0512/lidl-plus/python-check.yml?branch=main&label=checks)](https://github.com/Andre0512/lidl-plus/actions/workflows/python-check.yml)
[![PyPI - Status](https://img.shields.io/pypi/status/lidl-plus)](https://pypi.org/project/lidl-plus)
[![PyPI](https://img.shields.io/pypi/v/lidl-plus?color=blue)](https://pypi.org/project/lidl-plus)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/lidl-plus)](https://www.python.org/)
[![PyPI - License](https://img.shields.io/pypi/l/lidl-plus)](https://github.com/Andre0512/lidl-plus/blob/main/LICENCE)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/lidl-plus)](https://pypistats.org/packages/lidl-plus)

Fetch receipts, analytics and more from Lidl Plus.

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
Enter the verify code you received via phone: 123456
------------------------- refresh token ------------------------
2D4FC2A699AC703CAB8D017012658234917651203746021A4AA3F735C8A53B7F
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

Use `sync` on a schedule to keep the cache up to date, then read `stats` or the cache JSON directly as a sensor:

```bash
# Run daily via cron or HA shell command
lidl-plus -c DE -l de -r "TOKEN" --cache /config/lidlplus_cache.json sync
```

The cache file is plain JSON and can be read by HA's `rest` or `file` sensor integrations.

## Changelog

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
