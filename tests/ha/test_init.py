"""Tests for setup, sensors, services, panel and diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components import frontend, panel_custom
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.lidl_plus._lidlplus.exceptions import AuthenticationError
from custom_components.lidl_plus.const import CONF_COUNTRY, CONF_LANGUAGE, CONF_REFRESH_TOKEN, DOMAIN
from custom_components.lidl_plus.diagnostics import async_get_config_entry_diagnostics

from .conftest import LOYALTY_ID, NOW, FakeApiState

LEGACY_CACHE = '{"tickets": {"t0": {"id": "t0", "totalAmount": "9,99"}}, "last_updated": "2026-04-01T08:00:00"}'


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def panel_exists(hass: HomeAssistant) -> bool:
    return "lidl-plus" in hass.data.get(frontend.DATA_PANELS, {})


@pytest.fixture(autouse=True)
def frozen_time(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(NOW)


async def test_setup_and_unload(hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry) -> None:
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    # The auth server rotated the refresh token, the new one has to survive a restart
    assert config_entry.data[CONF_REFRESH_TOKEN] == "rotated-token"
    assert api_state.instances[0].cache_file == hass.config.path(
        ".storage", f"lidl_plus_cache_{config_entry.entry_id}.json"
    )
    assert panel_exists(hass)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    # The panel stays while entries are reloaded, it is removed together with the last entry
    assert panel_exists(hass)
    await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert not panel_exists(hass)


async def test_sensors(hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry) -> None:
    await setup_entry(hass, config_entry)

    month = hass.states.get("sensor.lidl_plus_current_month_spending")
    assert month.state == "27.5"
    # Home Assistant runs in US/Pacific during tests
    assert month.attributes["last_reset"] == "2026-05-01T00:00:00-07:00"
    assert month.attributes["unit_of_measurement"] == "€"

    assert hass.states.get("sensor.lidl_plus_average_basket").state == "13.33"
    # Paid after the discount of 0.50 on the coffee
    assert hass.states.get("sensor.lidl_plus_food_category_spending").state == "12.64"
    assert hass.states.get("sensor.lidl_plus_non_food_category_spending").state == "3.95"
    assert hass.states.get("sensor.lidl_plus_total_receipts").state == "3"
    assert hass.states.get("sensor.lidl_plus_coupons_available").state == "1"
    assert hass.states.get("sensor.lidl_plus_coupons_activated").state == "1"
    assert hass.states.get("sensor.lidl_plus_most_bought_item").state == "Milch"
    assert hass.states.get("sensor.lidl_plus_top_store").state == "Lidl Musterstadt"
    assert hass.states.get("sensor.lidl_plus_loyalty_id").state == LOYALTY_ID
    assert hass.states.get("sensor.lidl_plus_last_purchase").state == "2026-05-12T16:30:00+00:00"
    assert hass.states.get("sensor.lidl_plus_last_error").state == "OK"

    price_changes = hass.states.get("sensor.lidl_plus_price_changes_detected")
    assert price_changes.state == "1"
    change = price_changes.attributes["price_changes"][0]
    assert (change["name"], change["prev_price"], change["curr_price"], change["change_pct"]) == (
        "Milch",
        1.19,
        1.29,
        8.4,
    )

    receipts = hass.states.get("sensor.lidl_plus_receipts")
    assert receipts.state == "3"
    assert [receipt["id"] for receipt in receipts.attributes["receipts"]] == ["t3", "t2", "t1"]

    entity_registry = er.async_get(hass)
    assert entity_registry.async_get("sensor.lidl_plus_last_error").entity_category == "diagnostic"


async def test_sensor_names_are_translated(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    await hass.config.async_update(language="de")
    await setup_entry(hass, config_entry)
    # New entities get their ID in the language of the system, existing ones keep theirs
    state = hass.states.get("sensor.lidl_plus_ausgaben_aktueller_monat")
    assert state.attributes["friendly_name"] == "Lidl Plus Ausgaben aktueller Monat"
    assert hass.states.get("sensor.lidl_plus_letzter_einkauf").state == "2026-05-12T16:30:00+00:00"


async def test_setup_with_rejected_token_starts_reauth(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    api_state.error = AuthenticationError("invalid_grant")
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_setup_offline_uses_cache(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    api_state.error = requests.ConnectionError("offline")
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.lidl_plus_total_receipts").state == "3"
    assert hass.states.get("sensor.lidl_plus_last_error").state == "offline"
    # Nothing was synchronized yet
    assert hass.states.get("sensor.lidl_plus_last_sync").state == STATE_UNKNOWN


async def test_setup_offline_without_cache_is_retried(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    api_state.error = requests.ConnectionError("offline")
    api_state.tickets = []
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    # The panel is there anyway and reports that no account is loaded
    assert panel_exists(hass)


async def test_failed_sync_keeps_data(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    last_sync = hass.states.get("sensor.lidl_plus_last_sync").state
    message = "HTTPSConnectionPool: " + "x" * 300
    api_state.error = requests.ConnectionError(message)
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("sensor.lidl_plus_total_receipts").state == "3"
    # Coupons and the time of the last successful sync are kept
    assert hass.states.get("sensor.lidl_plus_coupons_available").state == "1"
    assert hass.states.get("sensor.lidl_plus_last_sync").state == last_sync
    last_error = hass.states.get("sensor.lidl_plus_last_error")
    # States are limited to 255 characters, the full message is kept as attribute
    assert len(last_error.state) == 255
    assert last_error.attributes["message"] == message
    assert len(hass.states.get("sensor.lidl_plus_log").state) <= 255


async def test_rejected_token_during_update(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    api_state.error = AuthenticationError("invalid_grant")
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.lidl_plus_total_receipts").state == STATE_UNAVAILABLE
    assert [flow["context"]["source"] for flow in hass.config_entries.flow.async_progress()] == [SOURCE_REAUTH]


async def test_migration_of_legacy_files_and_unique_id(hass: HomeAssistant, api_state: FakeApiState) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="DE_de",
        data={CONF_COUNTRY: "DE", CONF_LANGUAGE: "de", CONF_REFRESH_TOKEN: "old-token"},
    )
    entry.add_to_hass(hass)
    legacy_cache = Path(hass.config.path("lidl_plus_cache.json"))
    legacy_cache.write_text(LEGACY_CACHE, encoding="utf-8")
    public_data = Path(hass.config.path("www", "lidl_plus", "data.json"))
    public_data.parent.mkdir(parents=True)
    public_data.write_text('{"receipts": []}', encoding="utf-8")

    await setup_entry(hass, entry)
    # The entry works on a copy, the old cache stays as backup (and for going back to 1.1.0)
    assert (
        Path(hass.config.path(".storage", f"lidl_plus_cache_{entry.entry_id}.json")).read_text("utf-8") == LEGACY_CACHE
    )
    assert legacy_cache.read_text("utf-8") == LEGACY_CACHE
    # The panel data leaves the public www folder, but is not deleted
    assert not public_data.exists()
    assert (
        Path(hass.config.path(".storage", "lidl_plus_panel_data_backup.json")).read_text("utf-8") == '{"receipts": []}'
    )
    assert entry.unique_id == LOYALTY_ID


async def test_legacy_cache_is_copied_once(hass: HomeAssistant, api_state: FakeApiState) -> None:
    """Older versions shared one cache between all entries, only the first one takes it over."""
    for unique_id in ("DE_de", "AT_de"):
        MockConfigEntry(
            domain=DOMAIN,
            unique_id=unique_id,
            data={CONF_COUNTRY: unique_id[:2], CONF_LANGUAGE: "de", CONF_REFRESH_TOKEN: "old-token"},
        ).add_to_hass(hass)
    Path(hass.config.path("lidl_plus_cache.json")).write_text(LEGACY_CACHE, encoding="utf-8")

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    caches = list(Path(hass.config.path(".storage")).glob("lidl_plus_cache_*.json"))
    assert [cache.read_text("utf-8") for cache in caches] == [LEGACY_CACHE]
    assert Path(hass.config.path("lidl_plus_cache.json")).exists()


async def test_removed_entry_keeps_cache(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    cache = Path(api_state.instances[0].cache_file)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(LEGACY_CACHE, encoding="utf-8")
    await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert not panel_exists(hass)
    # Lidl may not return old receipts anymore, so the cache is kept
    kept = Path(hass.config.path(".storage", f"lidl_plus_removed_{LOYALTY_ID}.json"))
    assert kept.read_text("utf-8") == LEGACY_CACHE

    # Added again, the account continues with its receipts
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=LOYALTY_ID,
        data={CONF_COUNTRY: "DE", CONF_LANGUAGE: "de", CONF_REFRESH_TOKEN: "new-token"},
    )
    entry.add_to_hass(hass)
    await setup_entry(hass, entry)
    assert (
        Path(hass.config.path(".storage", f"lidl_plus_cache_{entry.entry_id}.json")).read_text("utf-8") == LEGACY_CACHE
    )
    assert not kept.exists()
    assert panel_exists(hass)


async def test_removed_cache_does_not_replace_an_older_one(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    cache = Path(api_state.instances[0].cache_file)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("newer", encoding="utf-8")
    older = Path(hass.config.path(".storage", f"lidl_plus_removed_{LOYALTY_ID}.json"))
    older.write_text("older", encoding="utf-8")

    await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert older.read_text("utf-8") == "older"
    # The time is frozen during the tests
    newer = Path(hass.config.path(".storage", f"lidl_plus_removed_{LOYALTY_ID}_20260515120000.json"))
    assert newer.read_text("utf-8") == "newer"


async def test_replaces_panel_from_configuration_yaml(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry, caplog: pytest.LogCaptureFixture
) -> None:
    assert await async_setup_component(hass, "frontend", {})
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path="lidl-plus",
        webcomponent_name="lidl-plus-panel-element",
        module_url="/local/lidl_plus/panel.js",
    )
    await setup_entry(hass, config_entry)
    panel = hass.data[frontend.DATA_PANELS]["lidl-plus"]
    assert panel.config["_panel_custom"]["name"] == "lidl-plus-panel"
    assert panel.config["_panel_custom"]["module_url"].startswith("/lidl_plus_frontend/panel.js?v=")
    assert "remove the Lidl Plus entry from panel_custom" in caplog.text


async def test_panel_data(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "lidl_plus/panel_data"})
    response = await client.receive_json()
    assert response["success"]
    data = response["result"]
    assert data["entry_id"] == config_entry.entry_id
    assert data["accounts"] == [{"entry_id": config_entry.entry_id, "title": "Lidl Plus (DE)"}]
    assert [receipt["id"] for receipt in data["receipts"]] == ["t3", "t2", "t1"]
    assert data["total_tickets"] == 3
    assert data["total_spent"] == 40.0
    assert data["current_month"] == 27.5
    assert data["spending_by_store"] == {"Lidl Musterstadt": 32.5, "Lidl Nord": 7.5}
    assert data["products"][0]["name"] == "Milch"
    assert data["products"][0]["price_history"][-1] == {
        "date": "2026-05-12T18:30:00+02:00",
        "price": 1.29,
        "store": "Lidl Musterstadt",
    }

    # Nothing new since then: the large lists are not sent again
    await client.send_json_auto_id({"type": "lidl_plus/panel_data", "known_sync": data["version"]})
    result = (await client.receive_json())["result"]
    assert result["unchanged"]
    assert "receipts" not in result

    # An account that is not loaded (anymore) falls back to the first one
    await client.send_json_auto_id({"type": "lidl_plus/panel_data", "entry_id": "unknown"})
    assert (await client.receive_json())["result"]["entry_id"] == config_entry.entry_id


async def test_panel_data_of_second_account(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Lidl Plus (AT)",
        unique_id="4000000999999",
        data={CONF_COUNTRY: "AT", CONF_LANGUAGE: "de", CONF_REFRESH_TOKEN: "token-2"},
    )
    second.add_to_hass(hass)
    await setup_entry(hass, config_entry)
    assert second.state is ConfigEntryState.LOADED

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "lidl_plus/panel_data", "entry_id": second.entry_id})
    result = (await client.receive_json())["result"]
    assert result["entry_id"] == second.entry_id
    assert [account["title"] for account in result["accounts"]] == ["Lidl Plus (DE)", "Lidl Plus (AT)"]


async def test_panel_data_without_loaded_account(hass: HomeAssistant, hass_ws_client: WebSocketGenerator) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    assert panel_exists(hass)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "lidl_plus/panel_data"})
    response = await client.receive_json()
    assert not response["success"]
    assert response["error"]["code"] == "not_found"


async def test_sync_service(hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry) -> None:
    await setup_entry(hass, config_entry)
    api_state.tickets = api_state.tickets[:1]
    await hass.services.async_call(DOMAIN, "sync", blocking=True)
    assert hass.states.get("sensor.lidl_plus_total_receipts").state == "1"


async def test_activate_all_coupons_service(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    response = await hass.services.async_call(DOMAIN, "activate_all_coupons", blocking=True, return_response=True)
    assert response == {"activated": ["Coupon A"], "failed": []}

    api_state.error = requests.ConnectionError("offline")
    with pytest.raises(HomeAssistantError, match="offline"):
        await hass.services.async_call(DOMAIN, "activate_all_coupons", blocking=True, return_response=True)


async def test_services_without_loaded_entry(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(HomeAssistantError, match="No Lidl Plus account"):
        await hass.services.async_call(DOMAIN, "sync", blocking=True)


async def test_diagnostics(hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry) -> None:
    await setup_entry(hass, config_entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    assert diagnostics["config"][CONF_REFRESH_TOKEN] == "**REDACTED**"
    assert diagnostics["loyalty_id_error"] is None
    assert diagnostics["counts"] == {
        "receipts": 3,
        "receipts_without_items": 0,
        "products": 4,
        "stores": 2,
        "coupons": 2,
        "offer_stores": 1,
        "offers": 2,
        "leaflets": 2,
        "leaflets_without_products": 0,
    }
    assert diagnostics["leaflet_region_known"] is False
    assert diagnostics["busy_times"] == {"opening_hours_known": True, "forecast": False, "forecast_error": None}
    dump = json.dumps(diagnostics)
    for private in ("rotated-token", LOYALTY_ID, "Lidl Musterstadt", "Lidl Nord", '"t1"'):
        assert private not in dump


async def test_account_without_loyalty_id(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    """The loyalty endpoint fails for some accounts, the receipts are loaded anyway"""
    api_state.loyalty_error = requests.HTTPError("404 Client Error: Not Found")
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.lidl_plus_total_receipts").state == "3"
    assert hass.states.get("sensor.lidl_plus_loyalty_id").state == STATE_UNKNOWN
    assert hass.states.get("sensor.lidl_plus_last_error").state == "OK"
    log = hass.states.get("sensor.lidl_plus_log").attributes["entries"]
    assert any("Loyalty-ID nicht verfügbar: 404 Client Error" in line for line in log)
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    assert diagnostics["loyalty_id_error"] == "404 Client Error: Not Found"


async def test_diagnostics_without_besttime_key(hass: HomeAssistant, api_state: FakeApiState) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=LOYALTY_ID,
        data={CONF_COUNTRY: "DE", CONF_LANGUAGE: "de", CONF_REFRESH_TOKEN: "old-token"},
        options={"besttime_api_key": "pri_very_secret"},
    )
    entry.add_to_hass(hass)
    await setup_entry(hass, entry)
    assert "pri_very_secret" not in json.dumps(await async_get_config_entry_diagnostics(hass, entry))
