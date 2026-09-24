"""Tests for the shopping duration: the visits of the stores from the location history of the persons."""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator, WebSocketGenerator

from custom_components.lidl_plus.const import CONF_VISIT_ENTITIES, KEY_RECEIPTS, KEY_SHOPPING_DURATION
from custom_components.lidl_plus.visits import find_visit, statistics

from .conftest import NOW, FakeApiState

STORE = (51.1633, 10.4477)
# About 1.1 km away
AWAY = (51.1733, 10.4477)
ZONE = "Lidl Musterstadt"
# Receipt t3 of the fake API: 2026-05-12T18:30:00+02:00
RECEIPT = datetime(2026, 5, 12, 16, 30, tzinfo=dt_util.UTC)


def state(
    minutes: float, location: tuple[float, float] | None = STORE, value: str = "not_home", accuracy: int = 10
) -> State:
    """A state of a person some minutes before or after the receipt"""
    attributes = (
        {} if location is None else {"latitude": location[0], "longitude": location[1], "gps_accuracy": accuracy}
    )
    time = RECEIPT + timedelta(minutes=minutes)
    return State("person.anna", value, attributes, last_changed=time, last_updated=time)


def at(minutes: float) -> str:
    return (RECEIPT + timedelta(minutes=minutes)).isoformat()


def test_visit_with_arrival_and_departure() -> None:
    visit = find_visit([state(-60, AWAY, "home"), state(-15), state(-5), state(10, AWAY)], RECEIPT, STORE)
    assert visit == {"status": "ok", "arrived": at(-15), "left": at(10), "minutes": 25, "checkout_minutes": 15}


def test_visit_in_zone_of_the_store() -> None:
    # A state in the zone without coordinates, e.g. reported when entering the zone
    states = [state(-40, AWAY), state(-12, None, ZONE), state(8, AWAY)]
    assert find_visit(states, RECEIPT, STORE, [ZONE])["minutes"] == 20
    assert find_visit(states, RECEIPT, STORE)["status"] == "not_seen", "without the zone it is no location at the store"


def test_accuracy_of_the_locations() -> None:
    # About 111 m from the store: at the store with an accuracy of 20 m, not with 5 m
    near = (STORE[0] + 0.001, STORE[1])
    assert find_visit([state(-30, AWAY), state(-20, near, accuracy=20), state(5, AWAY)], RECEIPT, STORE)[
        "arrived"
    ] == at(-20)
    assert find_visit([state(-30, AWAY), state(-20, near, accuracy=5), state(5, AWAY)], RECEIPT, STORE)["status"] == (
        "not_seen"
    )
    # Too inaccurate locations are left out
    visit = find_visit([state(-40, AWAY), state(-20, STORE, accuracy=500), state(-12), state(8, AWAY)], RECEIPT, STORE)
    assert visit["arrived"] == at(-12)


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        ([], {"status": "no_location"}),
        ([state(-10, None), state(5, None, "home")], {"status": "no_location"}),
        ([state(-30, AWAY), state(20, AWAY)], {"status": "not_seen"}),
        # Still at the store, or no location after it
        (
            [state(-40, AWAY), state(-10)],
            {"status": "incomplete", "arrived": at(-10), "left": None, "minutes": None, "checkout_minutes": 10},
        ),
        # At the store since the first location of the history
        (
            [state(-10), state(5, AWAY)],
            {"status": "incomplete", "arrived": None, "left": at(5), "minutes": None, "checkout_minutes": None},
        ),
        # The first location at the store came after paying
        (
            [state(-20, AWAY), state(2), state(10, AWAY)],
            {"status": "incomplete", "arrived": None, "left": at(10), "minutes": None, "checkout_minutes": None},
        ),
    ],
)
def test_visits_without_duration(states: list[State], expected: dict[str, Any]) -> None:
    assert find_visit(states, RECEIPT, STORE) == expected


def test_statistics() -> None:
    receipts = [
        {"id": "mon", "date": "2026-05-11T18:00:00+02:00", "store": "A", "store_id": "DE1"},
        {"id": "wed", "date": "2026-05-13T10:00:00+02:00", "store": "B", "store_id": "DE2"},
        {"id": "old", "date": "2025-01-01T10:00:00+01:00", "store": "A", "store_id": "DE1"},
        {"id": "long", "date": "2026-05-12T10:00:00+02:00", "store": "A", "store_id": "DE1"},
        {"id": "gone", "date": "2026-05-12T10:00:00+02:00", "store": "A", "store_id": "DE1"},
    ]
    ok = {"status": "ok", "arrived": "a", "left": "b"}
    visits = {
        "mon": {**ok, "minutes": 20, "checkout_minutes": 15},
        "wed": {**ok, "minutes": 10, "checkout_minutes": 8},
        "old": {**ok, "minutes": 30, "checkout_minutes": 20},
        # A phone that stopped reporting
        "long": {**ok, "minutes": 500, "checkout_minutes": 490},
        "gone": {"status": "not_seen"},
        "unknown": {**ok, "minutes": 5, "checkout_minutes": 2},
    }
    result = statistics(visits, receipts, datetime(2026, 5, 15, tzinfo=dt_util.UTC))
    assert (result["visits"], result["average_minutes"], result["average_checkout_minutes"]) == (2, 15.0, 11.5)
    assert (result["shortest_minutes"], result["longest_minutes"]) == (10, 20)
    assert result["by_weekday"] == [20.0, None, 10.0, None, None, None, None]
    assert (result["last"]["receipt_id"], result["last"]["store"], result["last"]["minutes"]) == ("wed", "B", 10)
    assert statistics({}, receipts, datetime(2026, 5, 15, tzinfo=dt_util.UTC))["last"] is None


@pytest.fixture(autouse=True)
def frozen_time(freezer: FrozenDateTimeFactory) -> None:
    """Before the other fixtures: their access tokens must not be issued in the future of the test"""
    freezer.move_to(NOW)


class FakeRecorder:
    """The recorder instance: keeps the history for 10 days"""

    keep_days = 10

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def async_add_executor_job(self, target: Any, *args: Any) -> Any:
        return self.hass.async_add_executor_job(target, *args)


@pytest.fixture
def history(hass: HomeAssistant) -> Generator[tuple[dict[str, list[State]], list[Any]]]:
    """The location history of the recorder (states by entity) and the queries of it"""
    states: dict[str, list[State]] = {}
    queries: list[Any] = []

    def get_significant_states(
        hass: HomeAssistant, start: datetime, end: datetime, entity_ids: list[str], **kwargs: Any
    ):
        queries.append((start, end, entity_ids, kwargs))
        if states.get("error"):
            raise RuntimeError("database is locked")
        return {
            entity_id: [entry for entry in states.get(entity_id, []) if start <= entry.last_updated <= end]
            for entity_id in entity_ids
        }

    hass.config.components.add("recorder")
    with (
        patch("homeassistant.components.recorder.get_instance", return_value=FakeRecorder(hass)),
        patch("homeassistant.components.recorder.history.get_significant_states", side_effect=get_significant_states),
    ):
        yield states, queries


@pytest.fixture
def person_and_zone(hass: HomeAssistant) -> None:
    hass.states.async_set("person.anna", "home")
    hass.states.async_set(
        "zone.lidl_musterstadt",
        "0",
        {"latitude": STORE[0] + 0.0003, "longitude": STORE[1], "radius": 100, "friendly_name": ZONE},
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_shopping_duration(
    hass: HomeAssistant,
    api_state: FakeApiState,
    config_entry: MockConfigEntry,
    history: tuple[dict[str, list[State]], list[Any]],
    person_and_zone: None,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
    hass_ws_client: WebSocketGenerator,
    hass_client: ClientSessionGenerator,
) -> None:
    freezer.move_to(NOW)
    states, queries = history
    states["person.anna"] = [state(-60, AWAY, "home"), state(-14, None, ZONE), state(-3), state(9, AWAY)]
    await setup_entry(hass, config_entry)

    data = config_entry.runtime_data.data
    receipts = {receipt["id"]: receipt for receipt in data[KEY_RECEIPTS]}
    visit = receipts["t3"]["visit"]
    assert (visit["status"], visit["minutes"], visit["checkout_minutes"]) == ("ok", 23, 14)
    assert (visit["entity_id"], visit["zone"], visit["store_id"], visit["final"]) == (
        "person.anna",
        ZONE,
        "DE1234",
        True,
    )
    # Older receipts than the history of the recorder are not checked
    assert (receipts["t2"]["visit"], receipts["t1"]["visit"]) == (None, None)
    assert [(query[0], query[1], query[2]) for query in queries] == [
        (RECEIPT - timedelta(hours=2), RECEIPT + timedelta(hours=2), ["person.anna"])
    ]
    assert queries[0][3]["significant_changes_only"] is False, "every location, not only changes of the state"
    # The locations of the stores of the last month, from the store API of Lidl Plus
    assert api_state.store_calls == ["DE1234", "DE2000"]

    duration = data[KEY_SHOPPING_DURATION]
    assert (duration["visits"], duration["average_minutes"], duration["entities"]) == (1, 23.0, ["person.anna"])
    assert duration["store"] == {
        "id": "DE1234",
        "name": "Musterstadt",
        "latitude": STORE[0],
        "longitude": STORE[1],
        "zones": [ZONE],
    }
    sensor = hass.states.get("sensor.lidl_plus_last_shopping_duration")
    assert (sensor.state, sensor.attributes["unit_of_measurement"], sensor.attributes["device_class"]) == (
        "23",
        "min",
        "duration",
    )
    assert (sensor.attributes["arrived"], sensor.attributes["left"], sensor.attributes["store"]) == (
        at(-14),
        at(9),
        "Lidl Musterstadt",
    )
    stored = hass_storage[f"lidl_plus_visits_{config_entry.entry_id}"]["data"]
    assert stored["visits"]["t3"]["minutes"] == 23
    assert stored["locations"]["DE1234"]["latitude"] == STORE[0]

    # Panel and REST API (before the access tokens of the test expire)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "lidl_plus/panel_data"})
    panel = (await client.receive_json())["result"]
    assert panel["shopping_duration"]["average_minutes"] == 23.0
    assert next(receipt for receipt in panel["receipts"] if receipt["id"] == "t3")["visit"]["minutes"] == 23
    rest = await (await (await hass_client()).get("/api/lidl_plus/shopping_duration")).json()
    assert (rest["visits"], [entry["receipt_id"] for entry in rest["recent_visits"]]) == (1, ["t3"])
    receipt = await (await (await hass_client()).get("/api/lidl_plus/receipts/t3")).json()
    assert receipt["visit"]["status"] == "ok"

    # Known visits and locations are kept, the recorder may have removed the locations meanwhile
    states["person.anna"] = []
    freezer.tick(timedelta(hours=6, minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert config_entry.runtime_data.data[KEY_SHOPPING_DURATION]["visits"] == 1
    assert len(queries) == 1
    assert api_state.store_calls == ["DE1234", "DE2000"]


async def test_departure_found_later(
    hass: HomeAssistant,
    api_state: FakeApiState,
    config_entry: MockConfigEntry,
    history: tuple[dict[str, list[State]], list[Any]],
    person_and_zone: None,
    freezer: FrozenDateTimeFactory,
) -> None:
    # An hour after the receipt the person is still at the store
    freezer.move_to(RECEIPT + timedelta(hours=1))
    states, queries = history
    states["person.anna"] = [state(-40, AWAY), state(-12)]
    await setup_entry(hass, config_entry)
    visit = next(r for r in config_entry.runtime_data.data[KEY_RECEIPTS] if r["id"] == "t3")["visit"]
    assert (visit["status"], visit["final"]) == ("incomplete", False)
    assert config_entry.runtime_data.data[KEY_SHOPPING_DURATION]["visits"] == 0

    # Checked again with the next update
    states["person.anna"].append(state(70, AWAY))
    freezer.tick(timedelta(hours=6, minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    visit = next(r for r in config_entry.runtime_data.data[KEY_RECEIPTS] if r["id"] == "t3")["visit"]
    assert (visit["status"], visit["minutes"], visit["final"]) == ("ok", 82, True)
    assert len(queries) == 2


async def test_without_persons_or_recorder(
    hass: HomeAssistant,
    api_state: FakeApiState,
    history: tuple[dict[str, list[State]], list[Any]],
    person_and_zone: None,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(NOW)
    states, queries = history
    states["person.anna"] = [state(-60, AWAY), state(-14), state(9, AWAY)]
    # No person chosen in the options: no shopping duration
    entry = MockConfigEntry(
        domain="lidl_plus",
        title="Lidl Plus (DE)",
        unique_id="4000000123456",
        data={"country": "DE", "language": "de", "refresh_token": "old-token"},
        options={CONF_VISIT_ENTITIES: []},
    )
    entry.add_to_hass(hass)
    await setup_entry(hass, entry)
    duration = entry.runtime_data.data[KEY_SHOPPING_DURATION]
    assert (duration["visits"], duration["entities"], queries) == (0, [], [])
    # The store and its zone are known anyway
    assert duration["store"]["zones"] == [ZONE]
    assert hass.states.get("sensor.lidl_plus_last_shopping_duration").state == "unknown"

    # Without recorder no locations are read
    hass.config.components.remove("recorder")
    hass.config_entries.async_update_entry(entry, options={CONF_VISIT_ENTITIES: ["person.anna"]})
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.data[KEY_SHOPPING_DURATION]["entities"] == ["person.anna"]
    assert queries == []


async def test_errors_do_not_stop_the_update(
    hass: HomeAssistant,
    api_state: FakeApiState,
    config_entry: MockConfigEntry,
    history: tuple[dict[str, list[State]], list[Any]],
    person_and_zone: None,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    freezer.move_to(NOW)
    states, _ = history
    states["error"] = [state(0)]
    # The store API fails as well: the stores are tried again with the next update
    api_state.store_locations.pop("DE2000")
    await setup_entry(hass, config_entry)
    assert config_entry.runtime_data.last_update_success
    assert "Could not add the shopping durations" in caplog.text
    assert config_entry.runtime_data.data[KEY_SHOPPING_DURATION] is None
    assert hass.states.get("sensor.lidl_plus_last_shopping_duration").state == "unknown"
    assert hass.states.get("sensor.lidl_plus_receipts").state == "3"
