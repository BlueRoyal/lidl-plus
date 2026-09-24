"""Tests for the busy hours of the store: opening hours, own shopping times and BestTime.app."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.lidl_plus.besttime import week_hours
from custom_components.lidl_plus.const import (
    CONF_BESTTIME_API_KEY,
    CONF_COUNTRY,
    CONF_LANGUAGE,
    CONF_OFFER_STORES,
    CONF_VISIT_ENTITIES,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)

from .conftest import LOYALTY_ID, NOW, FakeApiState

FORECAST_URL = "https://besttime.app/api/v1/forecasts"


def besttime_response() -> dict[str, Any]:
    """A day of BestTime runs from 6:00 to 5:00, the value is the hour of the day for the tests"""
    analysis = [
        {
            "day_info": {"day_int": day, "day_text": "Day"},
            "day_raw": [(6 + index) % 24 + day * 100 for index in range(24)],
        }
        for day in range(7)
    ]
    venue = {"venue_id": "ven_1", "venue_name": "Lidl", "venue_address": "Hauptstraße 1, 12345 Musterstadt"}
    return {"status": "OK", "venue_info": venue, "analysis": analysis}


@pytest.fixture(autouse=True)
def frozen_time(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(NOW)


@pytest.fixture
def entry_with_key(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Lidl Plus (DE)",
        unique_id=LOYALTY_ID,
        data={CONF_COUNTRY: "DE", CONF_LANGUAGE: "de", CONF_REFRESH_TOKEN: "token"},
        options={CONF_OFFER_STORES: [], CONF_BESTTIME_API_KEY: "pri_secret"},
    )
    entry.add_to_hass(hass)
    return entry


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def test_week_hours() -> None:
    hours = week_hours(besttime_response()["analysis"])
    # Monday 6:00 is the first value of Monday, Tuesday 3:00 the 22nd value of Monday, Monday 0:00 one of Sunday
    assert (hours[0][6], hours[0][23], hours[1][3], hours[0][0]) == (6, 23, 3, 600)
    assert week_hours([{"day_info": {"day_int": 9}, "day_raw": [1] * 24}, "x", None]) == [[None] * 24] * 7


async def test_own_shopping_times_and_opening_hours(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    await setup_entry(hass, config_entry)
    busy = config_entry.runtime_data.data["busy_times"]
    assert busy["store"]["name"] == "Musterstadt"
    assert busy["opening_hours"]["sunday"] == []
    # The receipts of the most visited store: Friday 10:00 (t1) and Tuesday 18:30 (t3)
    assert (busy["own"]["receipts"], busy["own"]["hours"][4][10], busy["own"]["hours"][1][18]) == (2, 1, 1)
    # Without API key no request to BestTime.app
    assert busy["forecast"] is None
    assert not aioclient_mock.mock_calls
    assert hass.states.get("sensor.lidl_plus_store_busyness").state == "unknown"


async def test_forecast_of_besttime(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    hass_storage: dict[str, Any],
    api_state: FakeApiState,
    entry_with_key: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    aioclient_mock.post(FORECAST_URL, json=besttime_response())
    await setup_entry(hass, entry_with_key)
    assert aioclient_mock.call_count == 1
    params = aioclient_mock.mock_calls[0][1].query
    assert (params["api_key_private"], params["venue_name"], params["venue_address"]) == (
        "pri_secret",
        "Lidl",
        "Hauptstraße 1, 12345 Musterstadt",
    )
    forecast = entry_with_key.runtime_data.data["busy_times"]["forecast"]
    assert forecast["venue_id"] == "ven_1"
    # Kept in the storage of Home Assistant
    assert hass_storage[f"lidl_plus_busy_times_{entry_with_key.entry_id}"]["data"]["DE1234"]["venue_id"] == "ven_1"

    client = await hass_client()
    response = await client.get("/api/lidl_plus/busy_times")
    busy = await response.json()
    assert busy["forecast"]["venue_id"] == "ven_1"
    assert busy["busyness_now"] == busy["forecast"]["hours"][busy["weekday"]][busy["hour"]] == 305
    assert busy["opening_hours"]["saturday"] == [["07:00", "21:00"]]

    # Home Assistant runs in US/Pacific during the tests: Friday 5:00, the 24th value of Thursday
    sensor = hass.states.get("sensor.lidl_plus_store_busyness")
    assert sensor.state == "305"
    assert sensor.attributes["unit_of_measurement"] == "%"
    assert sensor.attributes["today"][10] == 410
    # The state follows the hour without an update of the data
    freezer.tick(timedelta(hours=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.lidl_plus_store_busyness").state == "406"

    # The forecast is created again only after three weeks
    await entry_with_key.runtime_data.async_refresh()
    assert aioclient_mock.call_count == 1
    freezer.tick(timedelta(days=22))
    await entry_with_key.runtime_data.async_refresh()
    assert aioclient_mock.call_count == 2


async def test_failed_forecast_is_not_tried_again_too_soon(
    hass: HomeAssistant, api_state: FakeApiState, entry_with_key: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(FORECAST_URL, status=401, json={"status": "Error", "message": "Invalid API key"})
    await setup_entry(hass, entry_with_key)
    busy = entry_with_key.runtime_data.data["busy_times"]
    assert (busy["forecast"], busy["forecast_error"]) == (None, "Invalid API key")
    log = hass.states.get("sensor.lidl_plus_log").attributes["entries"]
    assert any("Stoßzeiten von BestTime.app konnten nicht geladen werden: Invalid API key" in line for line in log)
    # Every attempt may cost a credit
    await entry_with_key.runtime_data.async_refresh()
    assert aioclient_mock.call_count == 1
    assert entry_with_key.runtime_data.data["busy_times"]["forecast_error"] == "Invalid API key"
    # A new key is tried right away
    hass.config_entries.async_update_entry(entry_with_key, options={CONF_BESTTIME_API_KEY: "pri_new"})
    await entry_with_key.runtime_data.async_refresh()
    assert aioclient_mock.call_count == 2


async def test_options_with_besttime_key(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(FORECAST_URL, json=besttime_response())
    await setup_entry(hass, config_entry)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_OFFER_STORES: [], CONF_BESTTIME_API_KEY: " pri_secret "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert config_entry.options == {CONF_OFFER_STORES: [], CONF_VISIT_ENTITIES: [], CONF_BESTTIME_API_KEY: "pri_secret"}
    # The forecast is loaded right away
    assert config_entry.runtime_data.data["busy_times"]["forecast"]["venue_id"] == "ven_1"

    # The key is kept while searching stores, an empty field removes it
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    schema = result["data_schema"].schema
    key_field = next(key for key in schema if key == CONF_BESTTIME_API_KEY)
    assert key_field.description == {"suggested_value": "pri_secret"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_OFFER_STORES: []})
    assert config_entry.options == {CONF_OFFER_STORES: [], CONF_VISIT_ENTITIES: []}
