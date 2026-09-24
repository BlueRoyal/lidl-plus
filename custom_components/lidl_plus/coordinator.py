"""DataUpdateCoordinator for Lidl Plus."""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import besttime
from ._lidlplus import analytics, articles, export
from ._lidlplus.api import LidlPlusApi
from ._lidlplus.exceptions import LoginError, MissingLogin
from .article_store import article_store
from .const import (
    BESTTIME_REFRESH_DAYS,
    CONF_BESTTIME_API_KEY,
    CONF_OFFER_STORES,
    CONF_REFRESH_TOKEN,
    CONF_VISIT_ENTITIES,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
    FREQUENTLY_BOUGHT_LIMIT,
    KEY_AVERAGE_BASKET,
    KEY_BUSY_TIMES,
    KEY_CATALOG,
    KEY_CATEGORY_FOOD_SPENDING,
    KEY_CATEGORY_NONFOOD_SPENDING,
    KEY_COUPONS,
    KEY_COUPONS_ACTIVATED,
    KEY_COUPONS_AVAILABLE,
    KEY_CURRENT_MONTH_SPENDING,
    KEY_CURRENT_MONTH_START,
    KEY_DATA_VERSION,
    KEY_FREQUENTLY_BOUGHT,
    KEY_LAST_ERROR,
    KEY_LAST_SYNC,
    KEY_LEAFLET_REGION,
    KEY_LEAFLETS,
    KEY_LOG,
    KEY_LOYALTY_ID,
    KEY_NEW_TICKETS_LAST_SYNC,
    KEY_OFFER_STORES,
    KEY_OFFERS,
    KEY_OFFERS_CURRENT,
    KEY_OFFERS_FOR_YOU,
    KEY_OFFERS_UPCOMING,
    KEY_PRICE_CHANGES,
    KEY_PRODUCTS,
    KEY_RECEIPTS,
    KEY_RESTOCK_SUGGESTIONS,
    KEY_SAVINGS_BY_MONTH,
    KEY_SAVINGS_MONTH,
    KEY_SAVINGS_TOTAL,
    KEY_SHOPPING_DURATION,
    KEY_SHOPPING_FREQUENCY,
    KEY_SPENDING_BY_MONTH,
    KEY_SPENDING_BY_STORE,
    KEY_STORES,
    KEY_TOTAL_SPENT,
    KEY_TOTAL_TICKETS,
    LOG_LENGTH,
    PRICE_HISTORY_LENGTH,
)
from .visits import VisitTracker, receipt_time, store_zones
from .visits import statistics as visit_statistics

_LOGGER = logging.getLogger(__name__)

type LidlPlusConfigEntry = ConfigEntry[LidlPlusCoordinator]


def _detect_price_changes(products: list[dict], frequently_bought: list[dict]) -> list[dict]:
    """Compare the last two prices of the most frequently bought articles."""
    by_id = {product["id"]: product for product in products}
    changes = []
    for entry in frequently_bought:
        history = by_id.get(entry["id"], {}).get("price_history", [])
        if len(history) < 2:
            continue
        previous, current = history[-2], history[-1]
        if current["price"] == previous["price"]:
            continue
        changes.append(
            {
                "id": entry["id"],
                "name": entry["name"],
                "prev_price": previous["price"],
                "curr_price": current["price"],
                "change_pct": round((current["price"] - previous["price"]) / previous["price"] * 100, 1),
                "date": current["date"],
                "store": current["store"],
            }
        )
    # Largest absolute change first
    changes.sort(key=lambda change: abs(change["change_pct"]), reverse=True)
    return changes


def _build_receipts(tickets: list[dict]) -> list[dict]:
    """All receipts with their articles and details, newest first."""
    receipts = []
    for ticket in tickets:
        store = ticket.get("store") or {}
        details = ticket.get("_receipt") or {}
        items = ticket.get("_items") or []
        locality = " ".join(filter(None, [store.get("postalCode"), store.get("locality")]))
        receipts.append(
            {
                "id": ticket.get("id") or "",
                "date": ticket.get("date") or "",
                "store": store.get("name") or "",
                "store_id": store.get("id") or "",
                "store_address": ", ".join(filter(None, [store.get("address"), locality])),
                "total": analytics.to_float(ticket.get("totalAmount")),
                "savings": analytics.total_savings(items),
                "deposit_returns": details.get("deposit_returns") or [],
                "payments": details.get("payments") or [],
                "points": (ticket.get("collectingModel") or {}).get("points"),
                "coupons_used": ticket.get("couponsUsed") or [],
                "items": items,
            }
        )
    receipts.sort(key=lambda receipt: receipt["date"], reverse=True)
    return receipts


def _region_code(region: dict | None) -> int:
    """Offer region for the leaflets, 0 are the national leaflets"""
    return region["region"] if region else 0


def active_offers(data: dict[str, Any]) -> list[dict]:
    """
    Current and upcoming offers with their status of now (the data of the last update may be hours old) and the key
    of their article in the article database
    """
    offers = [
        {**offer, "status": analytics.offer_status(offer), "article_key": articles.offer_article_key(offer)}
        for offer in data.get(KEY_OFFERS, [])
    ]
    return [offer for offer in offers if offer["status"] != "expired"]


def active_leaflets(data: dict[str, Any]) -> list[dict]:
    """Current and upcoming leaflets with their status of today (in the time zone of Home Assistant)"""
    today = dt_util.now().date()
    leaflets = [
        {**leaflet, "status": analytics.leaflet_status(leaflet, today)} for leaflet in data.get(KEY_LEAFLETS, [])
    ]
    return [leaflet for leaflet in leaflets if leaflet["status"] != "expired"]


def search_data(data: dict[str, Any], query: str) -> dict[str, list]:
    """Articles bought before, offers and leaflet pages or products that contain every word of the query"""
    words = query.lower().split()

    def matches(text: str) -> bool:
        return bool(words) and all(word in text.lower() for word in words)

    return {
        "products": [product for product in data.get(KEY_PRODUCTS, []) if matches(product["name"])],
        "offers": [offer for offer in active_offers(data) if matches(f"{offer['brand']} {offer['title']}")],
        "leaflets": analytics.search_leaflets(active_leaflets(data), query),
    }


class LidlPlusCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch and process all Lidl Plus data on a fixed interval."""

    config_entry: LidlPlusConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: LidlPlusConfigEntry, api: LidlPlusApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(hours=DEFAULT_SCAN_INTERVAL_HOURS),
        )
        self.api = api
        self._log_entries: deque[str] = deque(maxlen=LOG_LENGTH)
        # Refresh token that was last written to the config entry by this coordinator
        self._stored_token = api.refresh_token
        # Why the loyalty ID could not be loaded, shown in the diagnostics
        self.loyalty_error: str | None = None
        # Forecasts of BestTime.app per store, kept in the storage of Home Assistant
        self._forecast_store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}_busy_times_{config_entry.entry_id}")
        self._forecasts: dict[str, Any] | None = None
        # A failed forecast costs a credit as well, it is not tried again too soon: store -> (API key, time)
        self._forecast_failures: dict[str, tuple[str, Any, str]] = {}
        # Every offer of the cache as of the last update, the leaflets are compared with them
        self._archived_offers: list[dict] | None = None
        # Arrival and departure at the stores of the receipts, from the locations of the persons
        self._visits = VisitTracker(hass, config_entry.entry_id)

    def _log(self, level: str, message: str) -> None:
        """Log to HA logger and keep entry in internal log."""
        timestamp = dt_util.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log_entries.append(f"[{timestamp}] {level}: {message}")
        if level == "ERROR":
            _LOGGER.error(message)
        elif level == "WARNING":
            _LOGGER.warning(message)
        else:
            _LOGGER.debug(message)

    async def async_call_api[_T](self, func: Callable[..., _T], *args: Any) -> _T:
        """Run a blocking API call in the executor and keep a rotated refresh token."""
        try:
            return await self.hass.async_add_executor_job(func, *args)
        finally:
            self._async_store_refresh_token()

    @callback
    def _async_store_refresh_token(self) -> None:
        """Persist the refresh token, the auth server may replace it on every renewal."""
        token = self.api.refresh_token
        # Only write tokens renewed by this client, a config flow may have stored a newer one meanwhile
        if token and token != self._stored_token:
            self._stored_token = token
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**self.config_entry.data, CONF_REFRESH_TOKEN: token}
            )

    async def _async_update_data(self) -> dict[str, Any]:
        self._log("INFO", "Sync gestartet")
        try:
            data = await self.async_call_api(self._fetch_all)
        except (LoginError, MissingLogin) as exc:
            self._log("ERROR", f"Anmeldung fehlgeschlagen: {exc}")
            raise ConfigEntryAuthFailed(f"Lidl Plus refresh token rejected: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc) or type(exc).__name__
            self._log("ERROR", f"Sync fehlgeschlagen: {error_msg}")
            # Keep showing the last data, the error is exposed by the "Last Error" sensor
            if self.data:
                return {**self.data, KEY_LAST_ERROR: error_msg, KEY_LOG: list(self._log_entries)}
            raise UpdateFailed(f"Lidl Plus: {error_msg}") from exc
        if data[KEY_LAST_ERROR]:
            self._log("ERROR", f"Sync fehlgeschlagen, Kassenbons aus dem Cache: {data[KEY_LAST_ERROR]}")
        else:
            self._log("INFO", f"Sync erfolgreich — {data[KEY_NEW_TICKETS_LAST_SYNC]} neue Kassenbons")
        try:
            data[KEY_BUSY_TIMES] = await self._async_add_forecast(data[KEY_BUSY_TIMES])
        except Exception:  # noqa: BLE001
            # The busy hours are an extra, they must never stop the update
            _LOGGER.exception("Could not add the forecast of BestTime.app")
        try:
            data[KEY_SHOPPING_DURATION] = await self._async_add_visits(data)
        except Exception:  # noqa: BLE001
            # Like the busy hours: an extra that must never stop the update
            _LOGGER.exception("Could not add the shopping durations")
        data[KEY_LOG] = list(self._log_entries)
        return data

    def visit_entities(self) -> list[str]:
        """Persons whose locations show the shopping duration: the ones of the options, otherwise all persons"""
        configured = self.config_entry.options.get(CONF_VISIT_ENTITIES)
        if configured is not None:
            return list(configured)
        return sorted(self.hass.states.async_entity_ids("person"))

    async def _async_add_visits(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Add the visit of the store (arrival, departure, duration) to the receipts of the last days and return the
        statistics of all visits
        """
        receipts = data[KEY_RECEIPTS]
        now = dt_util.utcnow()
        # Only the stores of receipts of the last days need their location, and the store of the busy hours
        recent = [
            receipt for receipt in receipts if (time := receipt_time(receipt)) and now - time < timedelta(days=31)
        ]
        store_ids = [receipt["store_id"] for receipt in recent if receipt["store_id"]] + data[KEY_OFFER_STORES][:1]
        locations = await self._visits.async_locations(store_ids, self.api.store)
        entity_ids = self.visit_entities()
        visits = await self._visits.async_update(receipts, locations, entity_ids)
        for receipt in receipts:
            receipt["visit"] = visits.get(receipt["id"])
        store = None
        if data[KEY_OFFER_STORES] and (location := locations.get(data[KEY_OFFER_STORES][0])):
            busy_store = (data.get(KEY_BUSY_TIMES) or {}).get("store") or {}
            store = {
                "id": data[KEY_OFFER_STORES][0],
                "name": busy_store.get("name") or data[KEY_OFFER_STORES][0],
                "latitude": location[0],
                "longitude": location[1],
                # A zone makes the companion app report the arrival and departure right away
                "zones": store_zones(self.hass, location),
            }
        return {**visit_statistics(visits, receipts, now), "entities": entity_ids, "store": store}

    async def _async_add_forecast(self, busy: dict[str, Any] | None) -> dict[str, Any] | None:
        """Add the busy hours forecast of BestTime.app for the store, created again every 3 weeks"""
        api_key = (self.config_entry.options.get(CONF_BESTTIME_API_KEY) or "").strip()
        if not busy or not api_key or not busy.get("store"):
            return busy
        store = busy["store"]
        if self._forecasts is None:
            self._forecasts = await self._forecast_store.async_load() or {}
        forecast = self._forecasts.get(store["id"])
        updated = dt_util.parse_datetime(forecast["updated"]) if forecast else None
        if updated and dt_util.utcnow() - updated < timedelta(days=BESTTIME_REFRESH_DAYS):
            return {**busy, "forecast": forecast}
        failed = self._forecast_failures.get(store["id"])
        if failed and failed[0] == api_key and dt_util.utcnow() - failed[1] < timedelta(hours=24):
            return {**busy, "forecast": forecast, "forecast_error": failed[2]}
        address = ", ".join(
            filter(None, [store.get("address"), f"{store.get('postal_code', '')} {store.get('locality', '')}".strip()])
        )
        try:
            new = await besttime.async_new_forecast(async_get_clientsession(self.hass), api_key, "Lidl", address)
        except besttime.BestTimeError as exc:
            self._forecast_failures[store["id"]] = (api_key, dt_util.utcnow(), str(exc))
            self._log("WARNING", f"Stoßzeiten von BestTime.app konnten nicht geladen werden: {exc}")
            return {**busy, "forecast": forecast, "forecast_error": str(exc)}
        self._forecast_failures.pop(store["id"], None)
        forecast = {**new, "updated": dt_util.utcnow().isoformat()}
        self._forecasts[store["id"]] = forecast
        await self._forecast_store.async_save(self._forecasts)
        self._log("INFO", f"Stoßzeiten von BestTime.app geladen: {forecast['venue_name']}")
        return {**busy, "forecast": forecast}

    def _fetch_all(self) -> dict[str, Any]:
        previous = self.data or {}
        # 1. Sync new tickets into the cache
        sync_error: Exception | None = None
        new_count = 0
        try:
            new_count = self.api.sync()
        except (LoginError, MissingLogin):
            raise
        except Exception as exc:  # noqa: BLE001
            # Lidl is not reachable: continue with the receipts in the cache
            sync_error = exc
        else:
            self._log("INFO", f"Tickets synchronisiert: {new_count} neu")
        cache = self.api.cached_data()
        tickets = list(cache["tickets"].values())
        if sync_error is not None and not tickets:
            raise sync_error
        items = analytics.ticket_items(tickets)
        stores = analytics.visited_stores(tickets)

        # 2. Articles, receipts and price changes
        frequently_bought = analytics.frequently_bought(items, FREQUENTLY_BOUGHT_LIMIT)
        products = analytics.product_summary(items, PRICE_HISTORY_LENGTH)
        price_changes = _detect_price_changes(products, frequently_bought)
        now = dt_util.now()
        restock = analytics.restock_suggestions(items, now=now)
        self._log("INFO", f"Produkte indexiert: {len(products)} einzigartige Artikel")
        self._log("INFO", f"Preisänderungen erkannt: {len(price_changes)}")
        self._log("INFO", f"Nachkauf-Vorschläge: {len(restock)}")

        # 3. Spending and savings
        by_month = analytics.spending_by_month(tickets)
        categories = analytics.category_spending(items)
        savings_by_month = analytics.savings_by_month(items)
        month = now.strftime("%Y-%m")

        # 4. Offers of the chosen stores and leaflets, public data that is independent of the login
        offer_stores = self._offer_store_keys(stores)
        # Region (for the leaflets) and opening hours of the first store, the store of the busy hours
        directory = self._store_directory(offer_stores)
        leaflet_region = (
            {"region": directory["region"], "name": directory["region_name"], "store": offer_stores[0]}
            if directory and directory.get("region") is not None
            else None
        )
        if self._sync_public_data(offer_stores, leaflet_region):
            cache = self.api.cached_data()
        archived_offers = analytics.archived_offers(cache.get("offers"))
        self._archived_offers = archived_offers
        offers = analytics.mark_bought_offers(
            [
                offer
                for offer in archived_offers
                if offer["status"] != "expired" and set(offer["stores"]) & set(offer_stores)
            ],
            items,
        )
        # Leaflets of the region that have not ended, with pages and products; all others stay in the cache
        regional_leaflets = analytics.leaflets_of_region(
            analytics.archived_leaflets(cache.get("leaflets"), now.date()), _region_code(leaflet_region)
        )
        leaflets = [leaflet for leaflet in regional_leaflets if leaflet["status"] != "expired"]
        # Every article of the receipts, of all offers seen so far and of the leaflets of the region
        catalog = articles.article_catalog(tickets, archived_offers, regional_leaflets)

        # 5. Coupons and loyalty ID (separate endpoints, failures are tolerated)
        if sync_error is None:
            coupons = self._fetch_coupons()
            loyalty_id = self._fetch_loyalty_id()
        else:
            coupons = previous.get(KEY_COUPONS, [])
            loyalty_id = previous.get(KEY_LOYALTY_ID)
        activated = sum(1 for coupon in coupons if analytics.coupon_is_activated(coupon))

        return {
            KEY_CURRENT_MONTH_SPENDING: by_month.get(month, 0.0),
            KEY_CURRENT_MONTH_START: dt_util.start_of_local_day(now.replace(day=1)).isoformat(),
            KEY_AVERAGE_BASKET: analytics.average_basket(tickets),
            KEY_SHOPPING_FREQUENCY: analytics.shopping_frequency_days(tickets),
            KEY_SPENDING_BY_MONTH: by_month,
            KEY_SPENDING_BY_STORE: analytics.spending_by_store(tickets),
            KEY_TOTAL_SPENT: analytics.total_spending(tickets),
            KEY_FREQUENTLY_BOUGHT: frequently_bought,
            KEY_RESTOCK_SUGGESTIONS: restock,
            KEY_PRICE_CHANGES: price_changes,
            KEY_CATEGORY_FOOD_SPENDING: categories["reduced"],
            KEY_CATEGORY_NONFOOD_SPENDING: categories["standard"],
            KEY_SAVINGS_TOTAL: analytics.total_savings(items),
            KEY_SAVINGS_MONTH: savings_by_month.get(month, 0.0),
            KEY_SAVINGS_BY_MONTH: savings_by_month,
            KEY_TOTAL_TICKETS: len(tickets),
            KEY_STORES: stores,
            KEY_OFFER_STORES: offer_stores,
            KEY_OFFERS: offers,
            KEY_OFFERS_CURRENT: sum(1 for offer in offers if offer["status"] == "current"),
            KEY_OFFERS_UPCOMING: sum(1 for offer in offers if offer["status"] == "upcoming"),
            KEY_OFFERS_FOR_YOU: [offer for offer in offers if offer["bought_products"]],
            KEY_LEAFLETS: leaflets,
            KEY_LEAFLET_REGION: leaflet_region,
            KEY_BUSY_TIMES: self._busy_times(offer_stores, directory, stores, tickets),
            KEY_CATALOG: catalog,
            # Added after the update, the locations are in the database of Home Assistant
            KEY_SHOPPING_DURATION: previous.get(KEY_SHOPPING_DURATION),
            KEY_DATA_VERSION: dt_util.utcnow().isoformat(),
            KEY_COUPONS: coupons,
            KEY_COUPONS_AVAILABLE: len(coupons) - activated,
            KEY_COUPONS_ACTIVATED: activated,
            KEY_LAST_SYNC: dt_util.utcnow().isoformat() if sync_error is None else previous.get(KEY_LAST_SYNC),
            KEY_LAST_ERROR: None if sync_error is None else (str(sync_error) or type(sync_error).__name__),
            KEY_NEW_TICKETS_LAST_SYNC: new_count,
            KEY_LOYALTY_ID: loyalty_id,
            KEY_PRODUCTS: products,
            KEY_RECEIPTS: _build_receipts(tickets),
        }

    def _offer_store_keys(self, stores: list[dict]) -> list[str]:
        """Stores chosen in the options, otherwise the most visited store of the receipts"""
        if configured := self.config_entry.options.get(CONF_OFFER_STORES):
            return list(configured)
        return [stores[0]["id"]] if stores and stores[0]["id"] else []

    def _store_directory(self, store_keys: list[str]) -> dict | None:
        """Address, offer region and opening hours of the first store (checked again once a week)"""
        if not store_keys:
            return None
        try:
            directory = self.api.store_directory(store_keys[0])
        except Exception as exc:  # noqa: BLE001
            self._log("WARNING", f"Details der Filiale {store_keys[0]} konnten nicht geladen werden: {exc}")
            return None
        if not directory or directory.get("region") is None:
            self._log(
                "INFO", f"Region der Filiale {store_keys[0]} unbekannt, es werden die bundesweiten Prospekte geladen"
            )
        return directory

    @staticmethod
    def _busy_times(
        store_keys: list[str], directory: dict | None, stores: list[dict], tickets: list[dict]
    ) -> dict | None:
        """Opening hours and own shopping times of the first store, the forecast is added later"""
        if not store_keys:
            return None
        store = (directory or {}).get("store")
        if not store:
            # A store of the receipts, known without the store details
            visited = next((entry for entry in stores if entry["id"] == store_keys[0]), None)
            store = visited and {key: visited[key] for key in ("id", "name", "address", "postal_code", "locality")}
        return {
            "store": store,
            "opening_hours": (directory or {}).get("opening_hours"),
            "own": analytics.shopping_times(tickets, store_keys[0]),
            "forecast": None,
            "forecast_error": None,
        }

    def _sync_public_data(self, store_keys: list[str], leaflet_region: dict | None) -> bool:
        """Add the offers of the stores and the leaflets to the cache, True if anything was added"""
        synced = False
        if store_keys:
            try:
                new_offers = self.api.sync_offers(store_keys)
            except Exception as exc:  # noqa: BLE001
                self._log("WARNING", f"Angebote konnten nicht geladen werden: {exc}")
            else:
                self._log("INFO", f"Angebote geladen: {new_offers} neu")
                synced = True
        try:
            new_leaflets = self.api.sync_leaflets(region=_region_code(leaflet_region) or None)
        except Exception as exc:  # noqa: BLE001
            self._log("WARNING", f"Prospekte konnten nicht geladen werden: {exc}")
        else:
            region = f" (Region {leaflet_region['name'] or leaflet_region['region']})" if leaflet_region else ""
            self._log("INFO", f"Prospekte geladen: {new_leaflets} neu{region}")
            synced = True
        return synced

    async def async_all_offers(self) -> list[dict]:
        """All offers of the cache, also the ones that ended, marked with the articles bought before"""
        offers = await self.hass.async_add_executor_job(self.api.cached_offers)
        items = [item for receipt in (self.data or {}).get(KEY_RECEIPTS, []) for item in receipt["items"]]
        return analytics.mark_bought_offers(offers, items)

    async def async_all_leaflets(self) -> list[dict]:
        """All leaflets of the region in the cache, also the ones that ended"""
        region = _region_code((self.data or {}).get(KEY_LEAFLET_REGION))
        return await self.hass.async_add_executor_job(self.api.cached_leaflets, dt_util.now().date(), region)

    async def async_leaflet(self, leaflet_id: str) -> dict | None:
        """
        A leaflet with its pages and products, and the offers printed on its pages (key "offers", see
        articles.offers_of_leaflet); None if it is not in the cache
        """
        leaflet = next((leaflet for leaflet in active_leaflets(self.data or {}) if leaflet["id"] == leaflet_id), None)
        if leaflet is None:
            leaflet = next((entry for entry in await self.async_all_leaflets() if entry["id"] == leaflet_id), None)
        if leaflet is None:
            return None
        # The offers of the last update, reading the cache file again would take long on small computers
        offers = self._archived_offers if self._archived_offers is not None else await self.async_all_offers()
        found = await self.hass.async_add_executor_job(articles.offers_of_leaflet, leaflet, offers)
        return {
            **leaflet,
            "offers": [
                # The package like "Je 500 g", without the purchase limit and the prices of the further lines
                {**offer, "packaging": articles.packaging(offer), "article_key": articles.offer_article_key(offer)}
                for offer in found
            ],
        }

    async def async_export(self, dataset: str, file_format: str) -> bytes:
        """Export file of the cache with the details of the articles added by hand, see export.export_file"""
        details = await article_store(self.hass).async_load()
        return await self.hass.async_add_executor_job(self._export, dataset, file_format, dict(details))

    def _export(self, dataset: str, file_format: str, details: dict[str, Any]) -> bytes:
        return export.export_file(self.api.cached_data(), dataset, file_format, dt_util.now().date(), details)

    def _fetch_coupons(self) -> list[dict]:
        try:
            coupons = analytics.section_entries(self.api.coupons(), "coupons")
        except Exception as exc:  # noqa: BLE001
            self._log("WARNING", f"Coupons konnten nicht geladen werden: {exc}")
            return (self.data or {}).get(KEY_COUPONS, [])
        self._log("INFO", f"Coupons geladen: {len(coupons)}")
        return coupons

    def _fetch_loyalty_id(self) -> str | None:
        try:
            loyalty_id = self.api.loyalty_id() or None
        except Exception as exc:  # noqa: BLE001
            # The loyalty endpoint fails for some accounts while everything else works
            self.loyalty_error = str(exc) or type(exc).__name__
            self._log("INFO", f"Loyalty-ID nicht verfügbar: {self.loyalty_error}")
            return (self.data or {}).get(KEY_LOYALTY_ID)
        self.loyalty_error = None
        return loyalty_id
