"""The Lidl Plus integration."""

from __future__ import annotations

import json
import logging
import os

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback

from .const import (
    CONF_COUNTRY,
    CONF_LANGUAGE,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    KEY_AVERAGE_BASKET,
    KEY_CATEGORY_FOOD_SPENDING,
    KEY_CATEGORY_NONFOOD_SPENDING,
    KEY_CURRENT_MONTH_SPENDING,
    KEY_SPENDING_BY_MONTH,
    KEY_SPENDING_BY_STORE,
    KEY_TOTAL_TICKETS,
    SERVICE_ACTIVATE_ALL_COUPONS,
    SERVICE_SYNC,
)
from .coordinator import LidlPlusCoordinator

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor"]
_CACHE_FILENAME = "lidl_plus_cache.json"
_WWW_DIR = "www/lidl_plus"
_DATA_FILE = "www/lidl_plus/data.json"


async def _write_panel_data(hass: HomeAssistant, coordinator: LidlPlusCoordinator) -> None:
    """Write full receipts + products data to www/lidl_plus/data.json for the panel."""
    if not coordinator.data:
        return

    data = coordinator.data
    output = {
        "receipts": [
            {
                "id": r["id"],
                "date": r["date"],
                "store": r["store"],
                "total": r["total"],
                "items": r.get("items", []),
            }
            for r in data.get("receipts", [])
        ],
        "products": data.get("products", []),
        "last_sync": data.get("last_sync", ""),
        "spending_by_month": data.get(KEY_SPENDING_BY_MONTH, {}),
        "spending_by_store": data.get(KEY_SPENDING_BY_STORE, {}),
        "food_total": data.get(KEY_CATEGORY_FOOD_SPENDING, 0),
        "nonfood_total": data.get(KEY_CATEGORY_NONFOOD_SPENDING, 0),
        "total_tickets": data.get(KEY_TOTAL_TICKETS, 0),
        "avg_basket": data.get(KEY_AVERAGE_BASKET, 0),
        "current_month": data.get(KEY_CURRENT_MONTH_SPENDING, 0),
    }

    www_path = hass.config.path(_WWW_DIR)
    data_path = hass.config.path(_DATA_FILE)

    def _write():
        os.makedirs(www_path, exist_ok=True)
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

    await hass.async_add_executor_job(_write)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Lidl Plus from a config entry."""
    from ._lidlplus.api import LidlPlusApi

    cache_path = hass.config.path(_CACHE_FILENAME)
    api = LidlPlusApi(
        language=entry.data[CONF_LANGUAGE],
        country=entry.data[CONF_COUNTRY],
        refresh_token=entry.data[CONF_REFRESH_TOKEN],
        cache_file=cache_path,
    )

    coordinator = LidlPlusCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Write initial panel data
    await _write_panel_data(hass, coordinator)

    # Update panel data on every coordinator refresh
    @callback
    def _on_coordinator_update():
        hass.async_create_task(_write_panel_data(hass, coordinator))

    coordinator.async_add_listener(_on_coordinator_update)

    # Panel is registered via panel_custom in configuration.yaml

    # ── Services ──────────────────────────────────────────────────────────────

    async def handle_activate_all_coupons(call: ServiceCall) -> None:
        """Activate every currently available coupon (both API v1 and v2)."""
        try:
            coupons_raw = await hass.async_add_executor_job(api.coupons)
            coupon_list = (
                coupons_raw if isinstance(coupons_raw, list)
                else [c for section in coupons_raw.get("sections", []) for c in section.get("coupons", [])]
            )
            for coupon in coupon_list:
                if coupon.get("activated") or coupon.get("isActivated"):
                    continue
                coupon_id = coupon.get("id") or coupon.get("couponId")
                if coupon_id:
                    await hass.async_add_executor_job(api.activate_coupon, coupon_id)

            promos_raw = await hass.async_add_executor_job(api.coupon_promotions_v1)
            promo_list = (
                promos_raw if isinstance(promos_raw, list)
                else [p for section in promos_raw.get("sections", []) for p in section.get("promotions", [])]
            )
            for promo in promo_list:
                if promo.get("isActivated"):
                    continue
                promo_id = promo.get("promotionId") or promo.get("id")
                if promo_id:
                    await hass.async_add_executor_job(api.activate_coupon_promotion_v1, promo_id)

        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Failed to activate coupons: %s", exc)

        await coordinator.async_request_refresh()

    async def handle_sync(call: ServiceCall) -> None:
        """Force an immediate sync of new receipts."""
        await coordinator.async_request_refresh()

    if not hass.services.has_service(DOMAIN, SERVICE_ACTIVATE_ALL_COUPONS):
        hass.services.async_register(DOMAIN, SERVICE_ACTIVATE_ALL_COUPONS, handle_activate_all_coupons)
    if not hass.services.has_service(DOMAIN, SERVICE_SYNC):
        hass.services.async_register(DOMAIN, SERVICE_SYNC, handle_sync)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_ACTIVATE_ALL_COUPONS)
            hass.services.async_remove(DOMAIN, SERVICE_SYNC)
            pass  # panel removed automatically when panel_custom config is gone
    return unload_ok
