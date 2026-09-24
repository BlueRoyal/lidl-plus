"""Tests for offers, leaflets, search, export and the store options with a loaded account."""

from __future__ import annotations

import json
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
import requests
import voluptuous as vol
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.lidl_plus.const import (
    CONF_COUNTRY,
    CONF_LANGUAGE,
    CONF_OFFER_STORES,
    CONF_REFRESH_TOKEN,
    CONF_VISIT_ENTITIES,
    DOMAIN,
)

from sample_data import flyer, leaflet, leaflet_overview, offer

from .conftest import LOYALTY_ID, NOW, FakeApiState


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture(autouse=True)
def frozen_time(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(NOW)


async def test_offer_and_leaflet_sensors(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    # Without a choice the offers of the most visited store are loaded
    assert api_state.offer_calls == [["DE1234"]]

    current = hass.states.get("sensor.lidl_plus_current_offers")
    assert current.state == "1"
    assert current.attributes["stores"] == ["DE1234"]
    assert current.attributes["offers"][0]["title"] == "Kaffee Crema"
    assert current.attributes["offers"][0]["price"] == 1.77
    assert hass.states.get("sensor.lidl_plus_upcoming_offers").attributes["offers"][0]["title"] == "Butter"
    for_you = hass.states.get("sensor.lidl_plus_offers_for_articles_you_bought")
    assert for_you.state == "1"
    assert for_you.attributes["offers"][0]["bought_products"] == ["Kaffee"]

    leaflets = hass.states.get("sensor.lidl_plus_leaflets")
    # The leaflet that ended is left out
    assert leaflets.state == "2"
    assert [(entry["start"], entry["status"], entry["products"]) for entry in leaflets.attributes["leaflets"]] == [
        ("2026-05-11", "current", 1),
        ("2026-05-18", "upcoming", 1),
    ]
    assert leaflets.attributes["leaflets"][0]["pdf"] == "https://assets.example.invalid/l1-hires-01.pdf"

    assert hass.states.get("sensor.lidl_plus_savings").state == "0.5"
    assert hass.states.get("sensor.lidl_plus_savings_current_month").state == "0.5"


async def test_offers_of_the_chosen_stores(hass: HomeAssistant, api_state: FakeApiState) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Lidl Plus (DE)",
        unique_id=LOYALTY_ID,
        data={CONF_COUNTRY: "DE", CONF_LANGUAGE: "de", CONF_REFRESH_TOKEN: "token"},
        options={CONF_OFFER_STORES: ["DE2000"]},
    )
    entry.add_to_hass(hass)
    await setup_entry(hass, entry)
    assert api_state.offer_calls == [["DE2000"]]
    assert hass.states.get("sensor.lidl_plus_current_offers").attributes["offers"][0]["title"] == "Shampoo"


async def test_offers_and_leaflets_are_optional(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    api_state.public_error = requests.ConnectionError("offline")
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.lidl_plus_total_receipts").state == "3"
    assert hass.states.get("sensor.lidl_plus_current_offers").state == "0"
    assert hass.states.get("sensor.lidl_plus_leaflets").state == "0"
    # The receipts were synchronized, so this is no error of the sync
    assert hass.states.get("sensor.lidl_plus_last_error").state == "OK"
    log = hass.states.get("sensor.lidl_plus_log").attributes["entries"]
    assert any("Prospekte konnten nicht geladen werden: offline" in line for line in log)
    assert any("Angebote konnten nicht geladen werden: offline" in line for line in log)


async def test_panel_leaflets_and_search(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": "lidl_plus/panel_data"})
    data = (await client.receive_json())["result"]
    assert [offer["id"] for offer in data["offers"]] == ["o1", "o2"]
    assert data["offer_stores"] == ["DE1234"]
    # Pages and products are loaded when a leaflet is opened
    assert [leaflet["id"] for leaflet in data["leaflets"]] == ["l1", "l2"]
    assert "pages" not in data["leaflets"][0]
    assert (data["leaflets"][0]["page_count"], data["leaflets"][0]["product_count"]) == (2, 1)

    await client.send_json_auto_id({"type": "lidl_plus/leaflet", "leaflet_id": "l1"})
    leaflet = (await client.receive_json())["result"]
    assert [page["number"] for page in leaflet["pages"]] == [1, 2]
    assert leaflet["products"][0]["title"] == "Akku-Bohrschrauber"
    # Leaflets that ended come from the cache
    await client.send_json_auto_id({"type": "lidl_plus/leaflet", "leaflet_id": "l0"})
    assert (await client.receive_json())["result"]["status"] == "expired"
    await client.send_json_auto_id({"type": "lidl_plus/leaflet", "leaflet_id": "unknown"})
    assert (await client.receive_json())["error"]["code"] == "not_found"

    await client.send_json_auto_id({"type": "lidl_plus/search", "query": "Kaffee"})
    result = (await client.receive_json())["result"]
    assert [product["name"] for product in result["products"]] == ["Kaffee"]
    assert [offer["id"] for offer in result["offers"]] == ["o1"]
    assert [
        (entry["leaflet"]["id"], [page["number"] for page in entry["pages"]], [p["id"] for p in entry["products"]])
        for entry in result["leaflets"]
    ] == [("l1", [1], []), ("l2", [], ["100002"])]


async def test_export_service(hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry) -> None:
    await setup_entry(hass, config_entry)
    response = await hass.services.async_call(
        DOMAIN, "export", {"dataset": "receipts", "format": "json"}, blocking=True, return_response=True
    )
    path = Path(response["files"][0])
    assert path.parent == Path(hass.config.path("lidl_plus_export"))
    # Home Assistant runs in US/Pacific during the tests
    assert path.name == "lidl_plus_receipts_2026-05-15_05-00-00.json"
    assert [row["id"] for row in json.loads(path.read_text("utf-8"))] == ["t3", "t2", "t1"]

    # An export in the same second does not replace the first one
    response = await hass.services.async_call(
        DOMAIN, "export", {"dataset": "receipts", "format": "json"}, blocking=True, return_response=True
    )
    assert response["files"][0].endswith("_2026-05-15_05-00-00_2.json")

    response = await hass.services.async_call(DOMAIN, "export", {}, blocking=True, return_response=True)
    with zipfile.ZipFile(response["files"][0]) as archive:
        assert "leaflets.csv" in archive.namelist()
        cache = json.loads(archive.read("lidl_plus_cache.json"))
    assert sorted(cache["leaflets"]) == ["l0", "l1", "l2"]

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "export", {"dataset": "unknown"}, blocking=True, return_response=True)


async def test_options_with_stores_of_the_receipts(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    choices = result["data_schema"].schema[CONF_OFFER_STORES].config["options"]
    assert choices == [
        {"value": "DE1234", "label": "DE1234 · Lidl Musterstadt, Hauptstraße 1, 12345 Musterstadt (2×)"},
        {"value": "DE2000", "label": "DE2000 · Lidl Nord, Hauptstraße 1, 12345 Musterstadt (1×)"},
    ]

    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_OFFER_STORES: ["DE2000"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert config_entry.options == {CONF_OFFER_STORES: ["DE2000"], CONF_VISIT_ENTITIES: []}
    # The offers of the new store are loaded right away
    assert api_state.offer_calls[-1] == ["DE2000"]
    assert hass.states.get("sensor.lidl_plus_current_offers").attributes["offers"][0]["title"] == "Shampoo"


async def test_panel_gets_new_offers_while_receipts_fail(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    api_state: FakeApiState,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_entry(hass, config_entry)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "lidl_plus/panel_data"})
    first = (await client.receive_json())["result"]

    # Lidl Plus rejects the receipts, but the offers of the store could be loaded
    freezer.tick(timedelta(hours=6))
    api_state.error = requests.ConnectionError("offline")
    api_state.store_offers["DE1234"].append(offer("o5", "Tee", ["t"], "2026-05-14T22:00:00Z", "2026-05-20T21:59:59Z"))
    await config_entry.runtime_data.async_refresh()
    await client.send_json_auto_id({"type": "lidl_plus/panel_data", "known_sync": first["version"]})
    second = (await client.receive_json())["result"]
    assert "unchanged" not in second
    assert second["last_sync"] == first["last_sync"]
    assert second["last_error"] == "offline"
    assert "o5" in [entry["id"] for entry in second["offers"]]

    # Without new data the panel keeps what it shows
    await client.send_json_auto_id({"type": "lidl_plus/panel_data", "known_sync": second["version"]})
    assert (await client.receive_json())["result"]["unchanged"]


async def test_regional_leaflets(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    hass_client,
    api_state: FakeApiState,
    config_entry: MockConfigEntry,
) -> None:
    """The weekly leaflets differ between the offer regions, the region of the store replaces the national ones"""
    await setup_entry(hass, config_entry)
    assert api_state.synced_regions == [None]
    assert [entry["id"] for entry in config_entry.runtime_data.data["leaflets"]] == ["l1", "l2"]

    api_state.store_directory = {**api_state.store_directory, "region": 10, "region_name": "Grevenbroich"}
    regional = leaflet("r1", "Aktionsprospekt", "aktion-r1", "2026-05-11", "2026-05-16", regions=["10", "42"])
    api_state.leaflets_by_region[10] = leaflet_overview(("Filial-Angebote", [regional]))
    api_state.flyers["aktion-r1"] = flyer(("Warsteiner Milbona Tilsiter", []))["flyer"]
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    # The region of the most visited store
    assert api_state.region_calls[-1] == "DE1234"
    assert api_state.synced_regions[-1] == 10
    data = config_entry.runtime_data.data
    assert data["leaflet_region"] == {"region": 10, "name": "Grevenbroich", "store": "DE1234"}
    # The regional leaflet of this week replaces the national one, next week has no regional variant yet
    assert [entry["id"] for entry in data["leaflets"]] == ["r1", "l2"]
    sensor = hass.states.get("sensor.lidl_plus_leaflets")
    assert sensor.attributes["region"] == "Grevenbroich"

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "lidl_plus/panel_data"})
    panel = (await client.receive_json())["result"]
    assert panel["leaflet_region"]["name"] == "Grevenbroich"
    assert [entry["id"] for entry in panel["leaflets"]] == ["r1", "l2"]

    http = await hass_client()
    response = await http.get("/api/lidl_plus/leaflets?status=all")
    # The national leaflet stays in the cache, but belongs to no store of the account anymore
    assert [entry["id"] for entry in (await response.json())["results"]] == ["l0", "r1", "l2"]
    response = await http.get("/api/lidl_plus/search?q=tilsiter")
    assert [entry["leaflet"]["id"] for entry in (await response.json())["leaflets"]] == ["r1"]
