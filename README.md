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

- **Python library & CLI** — fetch receipts with every detail, analytics, coupons, the offers of your store and the leaflets (Prospekte), export everything as CSV, JSON or ZIP
- **Home Assistant custom integration** — 25+ sensors, a sidebar panel with charts, receipt browser, product tracker, offers and leaflets, a REST API for AI assistants and other programs

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
# [{"id": "0082052", "name": "Feldsalat", "unit_price": "1,49", "quantity": 1.0, "unit": "", "total": 1.49,
#   "discounts": [{"text": "Lidl Plus Rabatt", "amount": -0.3}], "discount": -0.3, "tax_type": "A", "tax_rate": 7.0,
#   "is_deposit": False}, ...]

# Everything else the receipt contains: deposit returns, VAT, payments, total savings, till and receipt number
from lidlplus import analytics
receipt = analytics.parse_receipt(ticket["htmlPrintedReceipt"])
```

Weighed articles have their weight as `quantity` and `"unit": "kg"`, discounts (Lidl Plus coupons, price advantages) belong to the article above them, so `total + discount` is what was paid for a line.

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

The calculations are also available as pure functions in `lidlplus.analytics` (e.g. `analytics.spending_by_month(tickets)`, `analytics.product_summary(items)`, `analytics.total_savings(items)`), so a list of tickets only has to be loaded once.

The cache keeps the HTML of every receipt. When a newer version parses receipts better, the cached receipts are parsed again automatically, nothing has to be downloaded again.

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

# Activate all currently valid coupons (API v1 and v2)
result = lidl.activate_all_coupons()  # {"activated": [...titles], "failed": [...titles]}
```

The auth server may replace the refresh token when the access token is renewed. Always store `lidl.refresh_token` after using the API, the old token can be invalid afterwards.

### Offers and leaflets

Stores, their offers (the ones of the Lidl Plus app, current and announced) and the leaflets are public, no login is needed.

```bash
# Find the key of your store
lidl-plus stores "Musterstadt"
# DE1234  Musterstadt  Hauptstraße 1, 12345 Musterstadt

# Current and upcoming offers of the store
lidl-plus offers DE1234

# Current and upcoming leaflets (PDF and online links), or search their pages and products
lidl-plus leaflets
lidl-plus leaflets --search "Kaffee"

# Keep a history of all offers and leaflets in the cache
lidl-plus --language=de --country=DE --refresh-token=XXXXX --cache lidlplus_cache.json sync --store DE1234 --leaflets
```

```python
from lidlplus import LidlPlusApi, analytics

lidl = LidlPlusApi("de", "DE", cache_file="lidlplus_cache.json")
offers = [analytics.normalize_offer(offer) for offer in lidl.store_offers("DE1234")]

lidl.sync_offers(["DE1234"])   # offers are never removed from the cache
lidl.sync_leaflets()           # leaflets with the text of their pages and their products
upcoming = [leaflet for leaflet in lidl.cached_leaflets() if leaflet["status"] == "upcoming"]
analytics.search_leaflets(lidl.cached_leaflets(), "kaffee")
```

The weekly leaflets differ between the offer regions of Lidl: `lidl.leaflet_region("DE1234")` finds the region of a store in the public store directory of lidl.de (only Germany, kept in the cache for 30 days) and `lidl.sync_leaflets(region=10)` loads the variants of this region, in the CLI `leaflets --store DE1234` and `sync --store DE1234 --leaflets`. Without a region the national leaflets are used. Food offers are no products of a leaflet, they are only found by the text of their page. Leaflets are often published before they are complete, so upcoming leaflets are loaded again once a day until their offers start. The products of a leaflet are articles of the Lidl online shop, their IDs differ from the article numbers on the receipts. A weekly leaflet with pages and products takes about 0.5 MB in the cache, so the cache grows by roughly 25–35 MB per year.

### Article database

Every article known from the receipts, the offers and the leaflets, merged by article: the receipts and offers share the Lidl article number, the products of the leaflets have their own number in the online shop, and offers of several articles (e.g. all flavours of a brand) are an article of their own. The food offers are only printed on the pages of a leaflet, `articles.offers_of_leaflet()` finds them by the text of the pages.

```python
from lidlplus import analytics, articles

cache = lidl.cached_data()
offers = analytics.archived_offers(cache["offers"])
catalog = articles.article_catalog(cache["tickets"].values(), offers, analytics.archived_leaflets(cache["leaflets"]))
# Details added by hand (barcodes, package size, nutrition values, ingredients, notes), see Home Assistant
merged = articles.merge_user_data(catalog, {"nr:0082052": {"barcodes": ["4056489123453"], "package_size": "150 g"}})
articles.find_articles(merged, "skyr", kind="bought", sort="price-asc")
articles.normalize_barcode("4 056489 123453")  # "4056489123453", None for a wrong check digit
```

### Export

```bash
lidl-plus --cache lidlplus_cache.json export lidl.zip          # every table as CSV and the complete cache
lidl-plus --cache lidlplus_cache.json export items.csv         # all articles of all receipts
lidl-plus --cache lidlplus_cache.json export products.json     # the file name selects the table
lidl-plus --cache lidlplus_cache.json export bons.csv --dataset receipts
```

Tables: `receipts`, `items` (every article line with discounts), `products` (statistics per article), `offers`, `leaflets` (products of the leaflets) and `articles` (the article database, in Home Assistant with the details added by hand). CSV files use semicolons, decimal commas and UTF-8 with BOM, so they open correctly in Excel and LibreOffice with German settings. In Python: `lidlplus.export.export_file(lidl.cached_data(), "items", "csv")`.

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
  sync                      sync new tickets to cache (requires --cache),
                            --store KEY also saves the offers of a store, --leaflets the leaflets
  stats                     show analytics from cache (requires --cache)
  stores                    search stores by city, postal code or street (no login)
  offers                    current and announced offers of a store as json (no login)
  leaflets                  current and upcoming leaflets as json, --search TEXT (no login)
  export                    export the cache as CSV, JSON or ZIP (requires --cache)
```

## Home Assistant Integration

A fully featured Home Assistant custom integration is included in `custom_components/lidl_plus/`.

### Features
- **25+ sensors**: spending by month, average basket, food/non-food categories, savings, coupons, price changes, restock suggestions, current and upcoming offers, offers for articles you bought before, leaflets, last receipt, loyalty ID, and more (names in English and German)
- **Sidebar panel**, added automatically, with six tabs (and an account selector if several accounts are set up):
  - **Übersicht**: KPI cards, monthly spending and savings chart, food/non-food donut chart, top stores chart
  - **Kassenbons**: all receipts with every article, weight, discount, deposit, deposit return and payment, and how long the shopping took; filter by store, date range, amount
  - **Artikel**: the article database, see below
  - **Angebote**: current and upcoming offers of your stores, marked if you bought the article before
  - **Prospekte**: current and upcoming leaflets of the offer region of your store with PDF, pages, the products of the online shop and the offers of the Lidl Plus app printed on the pages, and a search across leaflets, offers and your purchases
  - **Stoßzeiten**: busy hours of your store per weekday and hour like on Google, with its opening hours, the hours at which you went shopping and how long your shopping takes
  - **Export** button: download everything as ZIP or a single table as CSV
- **Article database**: every article you bought, every offer of the Lidl Plus app and every product of the leaflets, with purchases and price history, offers and leaflets. Add barcodes, package size, nutrition values, ingredients (also of non-food articles like cosmetics or cleaning agents), notes and photos, e.g. of the nutrition label. **Scan the barcode** with the Home Assistant app (its scanner opens) or in the browser with the camera (Chrome on Android) or by typing it: a known barcode opens its article, an unknown one is looked up in [Open Food Facts](https://world.openfoodfacts.org), Open Beauty Facts and Open Products Facts (only the barcode is sent) and belongs to the article you search for, or to a new one with the values found. Articles you see on a page of a leaflet are added with "＋ Artikel" below the page. The details belong to the household and are shared by all accounts; they are kept in `/config/.storage/lidl_plus_articles`, the photos in `/config/lidl_plus/images/` (both part of the Home Assistant backups and of the export)
- **Shopping duration**: from the location history of the persons (Home Assistant companion app) the integration sees when you arrived at the store of a receipt and when you left, shown with the receipt, in the tab *Stoßzeiten* (average, by weekday) and as sensor *Last shopping duration*. The app reports its location reliably only when entering or leaving a zone, so create a zone around the store (the panel does it with one click). Home Assistant keeps locations for 10 days, so every visit is saved as soon as it is known; only the times of arrival and departure are kept, no locations
- **Offers and leaflets are kept**: every offer and leaflet seen stays in the cache, also after it ended
- **REST API** for AI assistants and other programs, see below
- **Services**: `lidl_plus.sync` (force refresh), `lidl_plus.activate_all_coupons` (returns the activated coupons), `lidl_plus.export` (writes the data to `/config/lidl_plus_export/`, e.g. for a weekly automation)
- **Configure** chooses the stores whose offers are loaded (from your receipts or by searching a city, postal code or street), without a choice the store you visit most is used. The first store also sets the region of the leaflets and the store of the busy hours. It also chooses the persons for the shopping duration (all persons until the options are saved, none turns it off)
- **Busy hours** (optional): Google offers no interface for its busy hours, so they come from [BestTime.app](https://besttime.app): create an account and enter its private API key under *Configure*. A forecast costs 2 credits, it is renewed every 3 weeks and kept in Home Assistant; the sensor *Store busyness* shows the expected busyness of the current hour. Without key the panel shows the opening hours of the store (from lidl.de) and the hours at which you went shopping
- **Re-authentication**: if Lidl rejects the refresh token, Home Assistant asks for a new one. Country, language and token can be changed with *Reconfigure*
- Refresh tokens replaced by the auth server are saved automatically, several Lidl Plus accounts can be added
- Data auto-refreshes every 6 hours. If Lidl cannot be reached, the receipts from the local cache are shown and the error appears in the *Last error* sensor
- The panel loads its data through the authenticated Home Assistant websocket API and works without internet access (Chart.js and Tailwind are included)

Requires Home Assistant 2025.3 or newer.

### Setup
1. Install the integration
   - **HACS**: add this repository as custom repository (type *Integration*) and install *Lidl Plus*, or
   - **manually**: copy `custom_components/lidl_plus/` to your HA `/config/custom_components/` directory
2. Restart Home Assistant
3. Go to **Settings → Devices & Services → Add Integration** and search for *Lidl Plus*
4. Enter country, language and your refresh token (obtain via `lidl-plus auth` CLI command)

The *Lidl Plus* panel appears in the sidebar, no `configuration.yaml` changes are needed.

### REST API for AI assistants and apps

All data of the integration can be read with a Home Assistant access token, e.g. by your own bot, an AI assistant or an app. The article database can be changed as well (articles, their details, photos, barcodes and leaflet pages); coupons cannot be activated and nothing else can be changed.

1. Create a user for the assistant (*Settings → People → Users*, no administrator) and log in with it once
2. In its profile (*Security → Long-lived access tokens*) create a token
3. Send it with every request: `Authorization: Bearer <token>`

```bash
TOKEN=eyJhbGciOi...
curl -H "Authorization: Bearer $TOKEN" http://homeassistant.local:8123/api/lidl_plus/summary
curl -H "Authorization: Bearer $TOKEN" "http://homeassistant.local:8123/api/lidl_plus/search?q=kaffee"
curl -H "Authorization: Bearer $TOKEN" -OJ "http://homeassistant.local:8123/api/lidl_plus/export?dataset=all"

# The article database: find a barcode, add an article, add details and a photo
curl -H "Authorization: Bearer $TOKEN" "http://homeassistant.local:8123/api/lidl_plus/barcodes/4056489123453"
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"name": "Duschgel Sensitive", "brand": "Cien", "barcodes": ["4056489123453"]}' \
     http://homeassistant.local:8123/api/lidl_plus/articles
curl -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"package_size": "500 g", "nutrition": {"energy_kcal": 63, "protein": 11}, "ingredients": "Magermilch"}' \
     http://homeassistant.local:8123/api/lidl_plus/articles/nr:1009893
curl -H "Authorization: Bearer $TOKEN" -F kind=nutrition -F file=@label.jpg \
     http://homeassistant.local:8123/api/lidl_plus/articles/nr:1009893/images
```

| Endpoint (below `/api/lidl_plus`) | Content |
|---|---|
| `/openapi.json` | OpenAPI 3.1 description of all endpoints, most AI tools turn it into functions directly |
| `/summary` | key figures: spending, savings, most bought articles, price changes, restock suggestions, number of coupons and offers |
| `/search?q=kaffee` | articles bought before, offers and leaflet pages and products that contain every word |
| `/receipts?from=2026-09-01&to=2026-09-30&items=true` | receipts, optionally with their articles; `store=`, `search=`, `limit=`, `offset=` |
| `/receipts/{id}` | a receipt with all details |
| `/products?search=milch&sort=spent` | articles with statistics and price history |
| `/products/{id}` | an article with every purchase and all its offers |
| `/spending?from=2026-01-01&to=2026-06-30` | spending and savings of a period by month, store and category |
| `/offers?status=upcoming` | offers: `active` (default), `current`, `upcoming`, `expired`, `all`; `bought=true` for articles bought before |
| `/leaflets`, `/leaflets/{id}` | leaflets (same `status` values), a single one with the text of every page and its products |
| `/busy_times` | opening hours and busy hours of the store (BestTime.app), `busyness_now`, and the hours of your own receipts |
| `/shopping_duration` | how long the shopping took: average, by weekday, the last visits with arrival and departure |
| `/articles?q=skyr&kind=nutrition` | the article database with barcodes, package size, nutrition values, ingredients and notes; `kind=` `bought`, `offers`, `leaflets`, `own`, `details`, `nutrition`, `no_nutrition`, `ingredients`, `photos`, `barcode`, `sort=` |
| `/articles/{key}`, `/images/{id}` | an article with all details (e.g. `nr:0082052`), a photo of an article |
| `/barcodes/{code}?lookup=true` | the articles with a barcode, for unknown ones the product of Open Food Facts |
| `/coupons`, `/stores`, `/accounts` | coupons, stores of the receipts and of the offers, configured accounts (`entry_id=` selects the account) |
| `/export?dataset=items&format=csv` | download: `all` (ZIP), `receipts`, `items`, `products`, `offers`, `leaflets`, `articles` as CSV or JSON |

Changes of the article database (the answer is the article with all details, errors have a `code` like `barcode_in_use`):

| Request (below `/api/lidl_plus`) | Change |
|---|---|
| `POST /articles` | add an article (JSON with `name` and any details) |
| `PATCH /articles/{key}` | change details: `name`, `brand`, `package_size`, `barcodes`, `nutrition` (per 100 g/ml: `energy_kj`, `energy_kcal`, `fat`, `saturated_fat`, `carbohydrates`, `sugars`, `fiber`, `protein`, `salt`), `nutrition_basis` (`100g`/`100ml`), `ingredients`, `notes`, `image` |
| `DELETE /articles/{key}` | remove the details added by hand, an article added by hand is removed completely |
| `POST /articles/{key}/images?kind=nutrition` | add a photo (JPEG, PNG or WebP up to 2.5 MB) as body or as form field `file`; `kind`: `nutrition`, `ingredients`, `front`, `other` |
| `DELETE /articles/{key}/images/{id}` | remove a photo |
| `POST /articles/{key}/leaflets` | add the article to a page of a leaflet: `{"leaflet_id": "...", "page": 2}` |
| `DELETE /articles/{key}/leaflets/{leaflet_id}?page=2` | remove it from a page, without `page` from the whole leaflet |

A Home Assistant token allows everything its user may do in Home Assistant, not only this API. Give the assistant or app its own user without administrator rights and delete the token when it is not needed anymore. Web apps of other addresses may use the API if their address is in `cors_allowed_origins` of the [`http` configuration](https://www.home-assistant.io/integrations/http/).

### Updating from 1.1.0
The update does not delete any data:
- **Before updating**, create a backup (*Settings → System → Backups*). If you changed files in `/config/custom_components/lidl_plus/`, keep a copy of that folder outside of `custom_components/`, the update replaces it.
- The receipt cache `/config/lidl_plus_cache.json` is **copied** to `/config/.storage/lidl_plus_cache_<entry>.json`. The old file stays as backup, so version 1.1.0 still works with it if you go back.
- `/config/www/lidl_plus/data.json` is moved to `/config/.storage/lidl_plus_panel_data_backup.json`, because files in `www/` can be downloaded **without login**. The other files in `/config/www/lidl_plus/` are not used anymore and can be removed once the new panel works.
- Remove the `panel_custom` entry of the Lidl Plus panel from `configuration.yaml`. The integration registers the panel itself now; until the entry is removed it replaces the old panel and logs a warning.
- Entity IDs, their history and statistics stay the same. The sensor names follow the Home Assistant language now. Country, language and token are changed with *Reconfigure*, *Configure* chooses the stores for the offers.
- The receipts in the cache are parsed again with all details (discounts, weights, deposits, payments) on the first start, the cache keeps the HTML of every receipt, so nothing has to be downloaded again.

Removing the integration keeps the receipt cache as `/config/.storage/lidl_plus_removed_<account>.json`; adding the same account again continues with it. Delete the file if you do not need the receipts anymore.

## Development
```bash
pip install -r requirements.txt -r requirements_dev.txt
pytest                                  # library and command line tool
pip install -r requirements_test_ha.txt
pytest                                  # additionally the Home Assistant integration (Linux/macOS)
cd tests/frontend && npm ci && npm test # the sidebar panel (panel.js and index.html) with jsdom
```

`custom_components/lidl_plus/_lidlplus/` contains a copy of `api.py`, `analytics.py`, `articles.py`, `exceptions.py` and `export.py` for Home Assistant. Change the files in `lidlplus/` and copy them over, `tests/test_vendored_copy.py` fails if they differ.

## Changelog

### 1.3.2 — Home Assistant integration (2026-09-24)
- The scanner of the Home Assistant app did not open after an update to 1.3.1 without a restart of Home Assistant: the page compared its version with the address of the panel element, which keeps the version of the start of Home Assistant. The panel element tells the page its version and whether the app offers its barcode scanner now
- The scan dialog tells why the scanner of the app is not used (an app without scanner, or a panel element of an older version)

### 1.3.1 — Home Assistant integration (2026-09-24)
- After an update Home Assistant may still have the panel element of the older version loaded, which rejected the new commands of the article database with "Unknown command". The panel element passes on every command of the integration now, and the page asks to reload Home Assistant if it belongs to an older panel element

### 1.3.0 — Home Assistant integration (2026-09-24)
- Article database in the tab *Artikel*: every article bought, offered or shown in a leaflet, with search and filters (bought, offers, leaflets, own articles, with nutrition values, photos, barcode, …); details added by hand: barcodes, package size, nutrition values per 100 g/ml, ingredients (also for non-food), notes and photos (made smaller before they are sent); articles added by hand
- Barcode scanner: the scanner of the Home Assistant app, the camera in the browser or typing the barcode; a known barcode opens its article, for an unknown one you search the article it belongs to or add a new one, with the values of Open Food Facts, Open Beauty Facts or Open Products Facts
- The leaflets show the offers of the Lidl Plus app printed on their pages (Lidl provides product data only for the products of the online shop) and the articles of the article database on their pages: added to a page by hand ("＋ Artikel" below a page) or found by their name; products and offers open their article
- Shopping duration from the location history of the persons, matched with the receipts: arrival, departure and time until paying for every receipt, statistics in the tab *Stoßzeiten*, sensor *Last shopping duration*, a button that creates a zone around the store; *Configure* chooses the persons
- REST API: `/articles`, `/articles/{key}`, `/barcodes/{code}`, `/images/{id}`, `/shopping_duration`, receipts with their `visit`; the article database can be changed (`POST`, `PATCH` and `DELETE` of articles, photos and leaflet pages), e.g. by an app with the token of a Home Assistant user; export table `articles` (the ZIP file also contains the details added by hand)

### 0.6.0 — Python library (2026-09-24)
- New module `lidlplus.articles`: article catalog of the receipts, offers and leaflets, the offers printed on the pages of a leaflet, details added by hand, search, barcode check (EAN/UPC)
- Export table `articles`

### 1.2.0 — Home Assistant integration (2026-09-24)
**New**
- Every detail of the receipts: discounts, weights, deposits and deposit returns, payment methods, savings and Lidl Plus points; food/non-food by the VAT rates of the receipt; sensors for the savings
- Offers of the chosen stores (current and announced) and the leaflets of their offer region (the weekly leaflets differ from region to region) with their pages and products, all of them kept in the cache as history; sensors for current, upcoming and "bought before" offers and for the leaflets; *Configure* chooses the stores
- Panel tabs *Angebote* and *Prospekte* with a search across leaflets, offers and your purchases; export button (ZIP or CSV)
- REST API with OpenAPI description for AI assistants and other programs (`/api/lidl_plus/...`, authentication with an access token)
- Busy hours of the store: panel tab *Stoßzeiten* and sensor *Store busyness* with the forecast of BestTime.app (optional API key), opening hours from the store directory of lidl.de and the hours of your own receipts
- Service `lidl_plus.export`

**Security & robustness**
- The panel no longer loads `/local/lidl_plus/data.json`. Everything in `www/` is served without authentication, so all receipts could be downloaded by anyone who can reach Home Assistant. The data now comes through an authenticated websocket command and the old file is moved out of `www/` on startup
- `manifest.json`, `strings.json` and the translations were excluded by `.gitignore` (`*.json`) and missing in the repository
- Refresh tokens replaced by the auth server are saved to the config entry, before the token of the initial setup was used again after every restart. The config flow stores the token returned by the validation
- A rejected refresh token starts a re-authentication; new *Reconfigure* flow. The old options flow failed on current Home Assistant (`config_entry` is read-only), *Configure* now chooses the stores for the offers
- Receipt cache per config entry in `.storage/`, written atomically; the progress of an interrupted sync is kept. Files of older versions are copied or moved, never deleted; removing the integration keeps the cache for the account
- Sensor states longer than 255 characters (error message, log) are shortened instead of becoming `unknown`; large attribute lists are excluded from the recorder database
- Diagnostics no longer contain the loyalty ID, store names and receipt IDs
- Chart.js and Tailwind are shipped with the integration instead of being loaded from CDNs: the panel works without internet access, and no third-party code runs in the origin of Home Assistant, where it could reach the login of the user
- If Lidl cannot be reached, the receipts from the cache are used, also at startup (before all sensors were unavailable until Lidl answered)
- A receipt that cannot be loaded no longer stops the sync of all newer receipts
- Refresh tokens that are replaced during a failed validation in the config flow are kept (e.g. after a typo in the country code)

**Features & fixes**
- The integration registers the panel and serves its files (no `panel_custom`, no copying to `www/`), HACS support. The panel stays while an entry is reloaded or cannot be set up
- Panel height fixed for the frontend since HA 2026.8, where `ha-panel-custom` has no height anymore and the panel collapsed to 150 px
- Panel shows all receipts (before only the last 50, which made the receipt count, total spending and category chart wrong), also receipts with a negative total (deposit returns only), German number format, banner for failed syncs, menu button with the same rule as the built-in panels (small screens, "always hide sidebar", not in kiosk mode), receipt table fits on phones
- The panel only loads the receipt and product lists again after a new sync, not every 5 minutes; a failing chart no longer hides the other tabs
- Several Lidl Plus accounts with an account selector in the panel, the loyalty ID is the unique ID now. Adding a configured account again replaces its token and reloads it, also after a failed setup
- Sensor names in English and German, diagnostic sensors categorized, `last_reset` for the monthly spending sensors
- The *Receipts* sensor counts all receipts (before at most 50, its attribute still lists the last 50), the loyalty ID has no quotes anymore
- `lidl_plus.activate_all_coupons` skips expired and not yet valid coupons and returns the activated coupons
- The cache file is read twice per update instead of 16 times

### 0.5.0 — Python library (2026-09-24)
- New receipt parser: every article with weight and unit, discounts, deposits, VAT rate; deposit returns, payments, total savings and receipt data (`analytics.parse_receipt`). Weight lines are no second purchase anymore. Cached receipts are parsed again automatically
- Stores, offers and leaflets (no login): `search_stores()`, `store()`, `store_offers()`, `sync_offers()`, `leaflets()`, `leaflet()`, `leaflet_region()`, `sync_leaflets()`, kept in the cache as history; the regional variants of the weekly leaflets for the offer region of a store; CLI commands `stores`, `offers`, `leaflets`, `sync --store/--leaflets`
- New module `lidlplus.export` and CLI command `export`: CSV (for German spreadsheets), JSON or ZIP with every table and the complete cache
- New analytics: `product_summary()`, `total_savings()`, `savings_by_month()`, `category_spending()`, `visited_stores()`, `search_leaflets()`
- The cache is written to disk (fsync) before it replaces the old file, syncs running at the same time wait for each other instead of overwriting each other's changes, offers and leaflets are only saved when something changed
- New module `lidlplus.analytics`; amounts like `"12,34"` are handled everywhere (before `lidl-plus stats` failed with a `TypeError`)
- New `activate_all_coupons()`, used by the CLI and Home Assistant
- Token handling: `AuthenticationError` for rejected refresh tokens, renewal shortly before expiry, one retry after HTTP 401, thread-safe renewal, `MissingLogin` instead of a `TypeError` without token
- All requests check the HTTP status and share one session; `tickets(only_favorite=True)` sends the filter for every page
- `sync()` skips receipts whose details cannot be loaded (HTTP 400/404/410) and tries them again next time
- `activate_all_coupons()` also accepts coupon lists without sections and ignores unexpected answers of the optional API v1
- `restock_suggestions()` counts several lines of an article on one receipt as one purchase
- `loyalty_id()` returns the ID without quotes
- Login: the Firefox fallback works with Selenium 4, Edge and Chromium are tried if Chrome is missing, the headless browser is closed afterwards
- CLI: `stats` needs no login, errors are shown as message instead of traceback, a refresh token replaced by the auth server is printed
- Requires Python 3.9+, test suite and CI for Python 3.9–3.14

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
