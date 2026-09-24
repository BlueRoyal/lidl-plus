"""Sidebar panel and the websocket command that feeds it."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.loader import async_get_integration

from ._lidlplus import analytics
from .const import (
    DOMAIN,
    KEY_AVERAGE_BASKET,
    KEY_CATEGORY_FOOD_SPENDING,
    KEY_CATEGORY_NONFOOD_SPENDING,
    KEY_CURRENT_MONTH_SPENDING,
    KEY_DATA_VERSION,
    KEY_LAST_ERROR,
    KEY_LAST_SYNC,
    KEY_LEAFLET_REGION,
    KEY_OFFER_STORES,
    KEY_PRODUCTS,
    KEY_RECEIPTS,
    KEY_SAVINGS_BY_MONTH,
    KEY_SAVINGS_TOTAL,
    KEY_SPENDING_BY_MONTH,
    KEY_SPENDING_BY_STORE,
    KEY_STORES,
    KEY_TOTAL_SPENT,
    KEY_TOTAL_TICKETS,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL_PATH,
    PANEL_WEBCOMPONENT,
    STATIC_URL_PATH,
)
from .coordinator import LidlPlusConfigEntry, active_leaflets, active_offers, search_data

_LOGGER = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent / "frontend"
_DATA_STATIC_REGISTERED = f"{DOMAIN}_static_registered"
_DATA_PANEL_REGISTERED = f"{DOMAIN}_panel_registered"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Serve the panel files and add the Lidl Plus panel to the sidebar."""
    if not hass.data.get(_DATA_STATIC_REGISTERED):
        # Only static code is served here, the receipts are loaded through the websocket API
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL_PATH, str(FRONTEND_DIR), cache_headers=False)]
        )
        hass.data[_DATA_STATIC_REGISTERED] = True

    if hass.data.get(_DATA_PANEL_REGISTERED):
        return
    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        # Older versions required a panel_custom entry in configuration.yaml
        _LOGGER.warning(
            "Replacing the '%s' panel from configuration.yaml, the integration registers it itself now. "
            "Please remove the Lidl Plus entry from panel_custom",
            PANEL_URL_PATH,
        )
        frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)

    integration = await async_get_integration(hass, DOMAIN)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_WEBCOMPONENT,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        # The version busts the browser cache after updates
        module_url=f"{STATIC_URL_PATH}/panel.js?v={integration.version}",
        require_admin=False,
    )
    hass.data[_DATA_PANEL_REGISTERED] = True


@callback
def async_unregister_panel(hass: HomeAssistant) -> None:
    """Remove the sidebar panel."""
    if hass.data.pop(_DATA_PANEL_REGISTERED, False):
        frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)


@callback
def async_setup_panel_api(hass: HomeAssistant) -> None:
    """Register the websocket commands used by the panel."""
    websocket_api.async_register_command(hass, ws_panel_data)
    websocket_api.async_register_command(hass, ws_leaflet)
    websocket_api.async_register_command(hass, ws_search)


def _loaded_entries(hass: HomeAssistant) -> list[LidlPlusConfigEntry]:
    return [entry for entry in hass.config_entries.async_loaded_entries(DOMAIN) if entry.runtime_data.data]


def _loaded_entry(hass: HomeAssistant, entry_id: str | None) -> LidlPlusConfigEntry | None:
    """The requested account, the first one if it is not loaded (anymore)"""
    entries = _loaded_entries(hass)
    return next((entry for entry in entries if entry.entry_id == entry_id), entries[0] if entries else None)


def build_panel_data(data: dict[str, Any]) -> dict[str, Any]:
    """Everything the panel shows, taken from the coordinator data."""
    return {
        "receipts": data.get(KEY_RECEIPTS, []),
        "products": data.get(KEY_PRODUCTS, []),
        "offers": active_offers(data),
        "offer_stores": data.get(KEY_OFFER_STORES, []),
        # Pages and products are loaded when a leaflet is opened
        "leaflets": [analytics.leaflet_summary(leaflet) for leaflet in active_leaflets(data)],
        "leaflet_region": data.get(KEY_LEAFLET_REGION),
        "stores": data.get(KEY_STORES, []),
        "last_sync": data.get(KEY_LAST_SYNC),
        "last_error": data.get(KEY_LAST_ERROR),
        "spending_by_month": data.get(KEY_SPENDING_BY_MONTH, {}),
        "spending_by_store": data.get(KEY_SPENDING_BY_STORE, {}),
        "savings_by_month": data.get(KEY_SAVINGS_BY_MONTH, {}),
        "savings_total": data.get(KEY_SAVINGS_TOTAL, 0),
        "food_total": data.get(KEY_CATEGORY_FOOD_SPENDING, 0),
        "nonfood_total": data.get(KEY_CATEGORY_NONFOOD_SPENDING, 0),
        "total_tickets": data.get(KEY_TOTAL_TICKETS, 0),
        "total_spent": data.get(KEY_TOTAL_SPENT, 0),
        "avg_basket": data.get(KEY_AVERAGE_BASKET, 0),
        "current_month": data.get(KEY_CURRENT_MONTH_SPENDING, 0),
    }


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel_data",
        vol.Optional("entry_id"): str,
        # version of the data the panel shows already
        vol.Optional("known_sync"): str,
    }
)
@callback
def ws_panel_data(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Send the receipts and statistics of a Lidl Plus account to the panel."""
    if (entry := _loaded_entry(hass, msg.get("entry_id"))) is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "No loaded Lidl Plus account")
        return
    entries = _loaded_entries(hass)
    data = entry.runtime_data.data
    result: dict[str, Any] = {
        "entry_id": entry.entry_id,
        "accounts": [{"entry_id": account.entry_id, "title": account.title} for account in entries],
        "last_sync": data.get(KEY_LAST_SYNC),
        "last_error": data.get(KEY_LAST_ERROR),
        # Also changes when only the offers or leaflets were updated because the receipts could not be loaded
        "version": data.get(KEY_DATA_VERSION),
    }
    if msg.get("known_sync") and msg["known_sync"] == result["version"]:
        # Nothing new since the panel loaded the data, skip the large lists
        result["unchanged"] = True
    else:
        result.update(build_panel_data(data))
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/leaflet",
        vol.Optional("entry_id"): str,
        vol.Required("leaflet_id"): str,
    }
)
@websocket_api.async_response
async def ws_leaflet(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Send a leaflet with its pages and products."""
    if (entry := _loaded_entry(hass, msg.get("entry_id"))) is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "No loaded Lidl Plus account")
    elif (leaflet := await entry.runtime_data.async_leaflet(msg["leaflet_id"])) is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Leaflet not found")
    else:
        connection.send_result(msg["id"], leaflet)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/search",
        vol.Optional("entry_id"): str,
        vol.Required("query"): str,
    }
)
@callback
def ws_search(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Send the articles, offers and leaflet pages and products that match the query."""
    if (entry := _loaded_entry(hass, msg.get("entry_id"))) is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "No loaded Lidl Plus account")
        return
    connection.send_result(msg["id"], search_data(entry.runtime_data.data, msg["query"]))
