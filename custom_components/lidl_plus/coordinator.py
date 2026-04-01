"""DataUpdateCoordinator for Lidl Plus."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
    FREQUENTLY_BOUGHT_LIMIT,
    KEY_AVERAGE_BASKET,
    KEY_CATEGORY_FOOD_SPENDING,
    KEY_CATEGORY_NONFOOD_SPENDING,
    KEY_COUPONS,
    KEY_COUPONS_ACTIVATED,
    KEY_COUPONS_AVAILABLE,
    KEY_CURRENT_MONTH_SPENDING,
    KEY_FREQUENTLY_BOUGHT,
    KEY_LAST_SYNC,
    KEY_LAST_ERROR,
    KEY_LOYALTY_ID,
    KEY_NEW_TICKETS_LAST_SYNC,
    KEY_PRICE_CHANGES,
    KEY_PRODUCTS,
    KEY_RECEIPTS,
    KEY_RESTOCK_SUGGESTIONS,
    KEY_SHOPPING_FREQUENCY,
    KEY_SPENDING_BY_MONTH,
    KEY_SPENDING_BY_STORE,
    KEY_TOTAL_TICKETS,
    TAX_TYPE_FOOD,
    TAX_TYPE_NONFOOD,
)

_LOGGER = logging.getLogger(__name__)


def _parse_price(price_str: str) -> float:
    """Convert German decimal string '1,49' or '1.49' to float."""
    try:
        return float(str(price_str).replace(",", "."))
    except (ValueError, AttributeError):
        return 0.0


def _coupon_is_activated(coupon: dict) -> bool:
    """Check multiple possible activated-flag keys across API versions."""
    return bool(
        coupon.get("activated")
        or coupon.get("isActivated")
        or coupon.get("isActive")
    )


class LidlPlusCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch and process all Lidl Plus data on a fixed interval."""

    def __init__(self, hass: HomeAssistant, api: Any) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=DEFAULT_SCAN_INTERVAL_HOURS),
        )
        self.api = api
        self._log_entries: deque = deque(maxlen=50)  # last 50 entries

    def _log(self, level: str, message: str) -> None:
        """Log to HA logger and keep entry in internal log."""
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{ts}] {level}: {message}"
        self._log_entries.append(entry)
        if level == "ERROR":
            _LOGGER.error(message)
        elif level == "WARNING":
            _LOGGER.warning(message)
        else:
            _LOGGER.debug(message)

    async def _async_update_data(self) -> dict[str, Any]:
        self._log("INFO", "Sync gestartet")
        try:
            data = await self.hass.async_add_executor_job(self._fetch_all)
            data[KEY_LAST_ERROR] = None
            data["log"] = list(self._log_entries)
            self._log("INFO", f"Sync erfolgreich — {data.get('new_tickets_last_sync', 0)} neue Kassenbons")
            data["log"] = list(self._log_entries)
            return data
        except Exception as exc:
            error_msg = str(exc)
            self._log("ERROR", f"Sync fehlgeschlagen: {error_msg}")
            if self.data:
                return {**self.data, KEY_LAST_ERROR: error_msg, "log": list(self._log_entries)}
            raise UpdateFailed(f"Lidl Plus: {error_msg}") from exc

    def _fetch_all(self) -> dict[str, Any]:
        # 1. Sync new tickets into cache
        new_count: int = self.api.sync()
        self._log("INFO", f"Tickets synchronisiert: {new_count} neu")

        # 2. Core analytics — compute spending ourselves to handle string amounts
        tickets = self.api.cached_tickets()
        all_items = self.api.all_ticket_items()
        freq_bought = self.api.frequently_bought(FREQUENTLY_BOUGHT_LIMIT)
        restock = self.api.restock_suggestions()
        freq_days = self.api.shopping_frequency_days()

        # spending_by_month / average_basket / current_month via _parse_price
        # so that string amounts like "1,19" are handled correctly
        from collections import defaultdict as _dd
        _by_month: dict = _dd(float)
        _by_store: dict = _dd(float)
        for t in tickets:
            amount = _parse_price(t.get("totalAmount", "0"))
            month = (t.get("date") or "")[:7]
            store = (t.get("store") or {}).get("name", "Unknown")
            if month:
                _by_month[month] = round(_by_month[month] + amount, 2)
            _by_store[store] = round(_by_store[store] + amount, 2)

        by_month = dict(sorted(_by_month.items()))
        by_store = dict(sorted(_by_store.items(), key=lambda x: x[1], reverse=True))

        from datetime import datetime as _dt
        _cur_month = _dt.utcnow().strftime("%Y-%m")
        current_month = by_month.get(_cur_month, 0.0)
        avg_basket = (
            round(sum(by_month.values()) / len(tickets), 2) if tickets else 0.0
        )

        # 3. Price change detection — compare last two prices for each top item
        price_changes: list[dict] = []
        for item in freq_bought:
            history = self.api.price_history(item["id"])
            if len(history) < 2:
                continue
            # Find last two entries that actually have a unit_price
            priced = [h for h in history if h.get("unit_price")]
            if len(priced) < 2:
                continue
            prev_price = _parse_price(priced[-2]["unit_price"])
            curr_price = _parse_price(priced[-1]["unit_price"])
            if curr_price != prev_price and prev_price > 0:
                change_pct = round((curr_price - prev_price) / prev_price * 100, 1)
                price_changes.append({
                    "id": item["id"],
                    "name": item["name"],
                    "prev_price": prev_price,
                    "curr_price": curr_price,
                    "change_pct": change_pct,
                    "date": priced[-1].get("date", ""),
                    "store": priced[-1].get("store", ""),
                })
        # Sort: largest absolute change first
        price_changes.sort(key=lambda x: abs(x["change_pct"]), reverse=True)

        # 4. Category spending via tax_type (A=19% non-food, B=7% food)
        food_total = 0.0
        nonfood_total = 0.0
        for item in all_items:
            line_total = _parse_price(item.get("unit_price", "0")) * item.get("quantity", 1)
            tax = item.get("tax_type", "")
            if tax == TAX_TYPE_FOOD:
                food_total += line_total
            elif tax == TAX_TYPE_NONFOOD:
                nonfood_total += line_total

        # 5. Produkt-Übersicht — alle Artikel mit vollständiger Statistik
        from collections import defaultdict as _ddict
        _prod: dict = _ddict(lambda: {
            "name": "", "purchase_count": 0, "total_quantity": 0.0,
            "total_spent": 0.0, "last_date": "", "last_store": "",
            "last_price": 0.0, "prices": [],
        })
        for item in all_items:
            pid = item["id"]
            price = _parse_price(item.get("unit_price", "0"))
            qty = float(item.get("quantity", 1))
            p = _prod[pid]
            p["name"] = item.get("name") or p["name"]
            p["purchase_count"] += 1
            p["total_quantity"] = round(p["total_quantity"] + qty, 3)
            p["total_spent"] = round(p["total_spent"] + price * qty, 2)
            if item.get("date", "") > p["last_date"]:
                p["last_date"] = item.get("date", "")
                p["last_store"] = item.get("store", "")
                p["last_price"] = price
            if price > 0:
                p["prices"].append({
                    "date": item.get("date", ""),
                    "price": price,
                    "store": item.get("store", ""),
                })

        # Compute avg_price, sort price history, keep last 20 prices per product
        products_list = []
        for pid, p in _prod.items():
            prices_sorted = sorted(p["prices"], key=lambda x: x["date"])[-20:]
            avg = round(p["total_spent"] / p["total_quantity"], 2) if p["total_quantity"] else 0.0
            products_list.append({
                "id": pid,
                "name": p["name"],
                "purchase_count": p["purchase_count"],
                "total_quantity": p["total_quantity"],
                "total_spent": p["total_spent"],
                "avg_price": avg,
                "last_price": p["last_price"],
                "last_date": p["last_date"],
                "last_store": p["last_store"],
                "price_history": prices_sorted,
            })
        # Sort by total purchase count descending
        products_list.sort(key=lambda x: x["purchase_count"], reverse=True)
        self._log("INFO", f"Produkte indexiert: {len(products_list)} einzigartige Artikel")

        # 6. Kassenbons mit Produkten — letzte 50 vollständig
        receipts_list = []
        for t in sorted(tickets, key=lambda x: x.get("date", ""), reverse=True)[:50]:
            receipts_list.append({
                "id": t.get("id", ""),
                "date": t.get("date", ""),
                "store": (t.get("store") or {}).get("name", ""),
                "total": _parse_price(t.get("totalAmount", "0")),
                "items": t.get("_items", []),
            })

        self._log("INFO", f"Preisänderungen erkannt: {len(price_changes)}")
        self._log("INFO", f"Nachkauf-Vorschläge: {len(restock)}")

        # 5. Coupons (live API call — separate endpoint)
        all_coupons: list[dict] = []
        try:
            coupons_raw = self.api.coupons()
            # API may return a list directly or a sectioned dict
            if isinstance(coupons_raw, list):
                all_coupons = coupons_raw
            elif isinstance(coupons_raw, dict):
                for section in coupons_raw.get("sections", []):
                    all_coupons.extend(section.get("coupons", []))
            self._log("INFO", f"Coupons geladen: {len(all_coupons)}")
        except Exception as e:  # noqa: BLE001
            self._log("WARNING", f"Coupons konnten nicht geladen werden: {e}")

        activated = sum(1 for c in all_coupons if _coupon_is_activated(c))
        available = len(all_coupons) - activated

        # 6. Loyalty ID (rarely changes — tolerate failure)
        loyalty_id: str | None = None
        try:
            loyalty_id = self.api.loyalty_id()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Could not fetch loyalty ID, skipping")

        return {
            KEY_CURRENT_MONTH_SPENDING:    round(current_month, 2),
            KEY_AVERAGE_BASKET:            avg_basket,
            KEY_SHOPPING_FREQUENCY:        freq_days,
            KEY_SPENDING_BY_MONTH:         by_month,
            KEY_SPENDING_BY_STORE:         by_store,
            KEY_FREQUENTLY_BOUGHT:         freq_bought,
            KEY_RESTOCK_SUGGESTIONS:       restock,
            KEY_PRICE_CHANGES:             price_changes,
            KEY_CATEGORY_FOOD_SPENDING:    round(food_total, 2),
            KEY_CATEGORY_NONFOOD_SPENDING: round(nonfood_total, 2),
            KEY_TOTAL_TICKETS:             len(tickets),
            KEY_COUPONS:                   all_coupons,
            KEY_COUPONS_AVAILABLE:         available,
            KEY_COUPONS_ACTIVATED:         activated,
            KEY_LAST_SYNC:                 datetime.now(tz=timezone.utc).isoformat(),
            KEY_NEW_TICKETS_LAST_SYNC:     new_count,
            KEY_LOYALTY_ID:                loyalty_id,
            KEY_PRODUCTS:                  products_list,
            KEY_RECEIPTS:                  receipts_list,
        }
