"""Diagnostics for Lidl Plus — adds 'Download Diagnostics' button in HA UI."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_REFRESH_TOKEN,
    KEY_AVERAGE_BASKET,
    KEY_CATEGORY_FOOD_SPENDING,
    KEY_CATEGORY_NONFOOD_SPENDING,
    KEY_COUPONS,
    KEY_COUPONS_ACTIVATED,
    KEY_COUPONS_AVAILABLE,
    KEY_CURRENT_MONTH_SPENDING,
    KEY_LAST_ERROR,
    KEY_LAST_SYNC,
    KEY_LEAFLETS,
    KEY_LOG,
    KEY_NEW_TICKETS_LAST_SYNC,
    KEY_OFFER_STORES,
    KEY_OFFERS,
    KEY_PRODUCTS,
    KEY_RECEIPTS,
    KEY_SHOPPING_FREQUENCY,
    KEY_SPENDING_BY_STORE,
    KEY_TOTAL_TICKETS,
)
from .coordinator import LidlPlusConfigEntry

# Diagnostics are meant to be attached to public issues, so leave out personal data
TO_REDACT = {CONF_REFRESH_TOKEN}
_SUMMARY_KEYS = (
    KEY_TOTAL_TICKETS,
    KEY_NEW_TICKETS_LAST_SYNC,
    KEY_LAST_SYNC,
    KEY_LAST_ERROR,
    KEY_CURRENT_MONTH_SPENDING,
    KEY_AVERAGE_BASKET,
    KEY_SHOPPING_FREQUENCY,
    KEY_CATEGORY_FOOD_SPENDING,
    KEY_CATEGORY_NONFOOD_SPENDING,
    KEY_COUPONS_AVAILABLE,
    KEY_COUPONS_ACTIVATED,
)


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: LidlPlusConfigEntry) -> dict[str, Any]:
    """Return diagnostics data without tokens, IDs and store locations."""
    coordinator = entry.runtime_data
    data = coordinator.data or {}
    receipts = data.get(KEY_RECEIPTS, [])

    return {
        "config": async_redact_data(dict(entry.data), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "last_exception": repr(coordinator.last_exception) if coordinator.last_exception else None,
        "summary": {key: data.get(key) for key in _SUMMARY_KEYS},
        "counts": {
            "receipts": len(receipts),
            "receipts_without_items": sum(1 for receipt in receipts if not receipt.get("items")),
            "products": len(data.get(KEY_PRODUCTS, [])),
            "stores": len(data.get(KEY_SPENDING_BY_STORE, {})),
            "coupons": len(data.get(KEY_COUPONS, [])),
            "offer_stores": len(data.get(KEY_OFFER_STORES, [])),
            "offers": len(data.get(KEY_OFFERS, [])),
            "leaflets": len(data.get(KEY_LEAFLETS, [])),
            "leaflets_without_products": sum(1 for leaflet in data.get(KEY_LEAFLETS, []) if not leaflet["products"]),
        },
        # A few receipts (without receipt ID and store) help to debug the parsing of the receipt HTML
        "latest_receipts": [
            {"date": receipt.get("date"), "total": receipt.get("total"), "items": receipt.get("items", [])}
            for receipt in receipts[:3]
        ],
        "log": data.get(KEY_LOG, []),
    }
