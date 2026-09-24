"""Shopping duration: when a phone arrived at the store and left it again, matched with the time of the receipt.

The locations come from the history of the persons (Home Assistant companion app). The recorder keeps them only for
some days (10 by default), so every visit is kept in the storage of Home Assistant as soon as it is known. A zone
around the store makes the app report the arrival and the departure right away, without it the app may not report
any location during a short trip.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Without a zone: locations up to this distance (plus their accuracy, at most ACCURACY_ALLOWANCE) are at the store
STORE_RADIUS = 100
ACCURACY_ALLOWANCE = 50
# Less accurate locations are left out
MAX_ACCURACY = 150
# A zone belongs to a store if its center is this close to the store
ZONE_DISTANCE = 150
# Locations around the time of a receipt that are compared
WINDOW_BEFORE = timedelta(hours=2)
WINDOW_AFTER = timedelta(hours=2)
# A receipt is checked again until the departure is known, at most this long
FINAL_AFTER = timedelta(hours=6)
# Longer stays are no shopping trips (e.g. a phone that stopped reporting), they are not part of the statistics
MAX_MINUTES = 180
# Statistics of the visits of this period
STATISTICS_DAYS = 365
# The location of a store is loaded again after this time
LOCATION_REFRESH = timedelta(days=30)
STORAGE_VERSION = 1


def distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance of two coordinates in meters"""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi, delta_lambda = phi2 - phi1, math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(a))


def _at_store(state: State, location: tuple[float, float], zone_names: Iterable[str]) -> bool | None:
    """True if a state of a person is at the store, False if elsewhere, None if its location is unknown"""
    if state.state in zone_names:
        return True
    latitude, longitude = state.attributes.get("latitude"), state.attributes.get("longitude")
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        return None
    accuracy = state.attributes.get("gps_accuracy")
    accuracy = accuracy if isinstance(accuracy, (int, float)) else 0
    if accuracy > MAX_ACCURACY:
        return None
    return distance(latitude, longitude, *location) <= STORE_RADIUS + min(accuracy, ACCURACY_ALLOWANCE)


def find_visit(
    states: list[State], receipt_time: datetime, location: tuple[float, float], zone_names: Iterable[str] = ()
) -> dict[str, Any]:
    """
    The visit of the store around the time of a receipt, from the states of a person (oldest first).

    Arrival and departure are the first locations reported at and away from the store: the phone needs a while for
    both, so the duration between them is about right. "status" is "ok" (both known), "incomplete" (at the store,
    but arrival or departure unknown), "not_seen" (only locations elsewhere) or "no_location".
    """
    zone_names = set(zone_names)
    points = [
        (state.last_updated, inside)
        for state in states
        if (inside := _at_store(state, location, zone_names)) is not None
    ]
    if not points:
        return {"status": "no_location"}
    before = [index for index, (time, _) in enumerate(points) if time <= receipt_time]
    after = [index for index, (time, _) in enumerate(points) if time > receipt_time]
    # The locations at the store next to the receipt
    if before and points[before[-1]][1]:
        first = last = before[-1]
    elif after and points[after[0]][1]:
        first = last = after[0]
    else:
        return {"status": "not_seen"}
    while first > 0 and points[first - 1][1]:
        first -= 1
    while last + 1 < len(points) and points[last + 1][1]:
        last += 1
    # Arrived before the receipt, a first location at the store after it came too late
    arrived = points[first][0] if points[first][0] <= receipt_time and first > 0 else None
    left = points[last + 1][0] if last + 1 < len(points) else None
    visit: dict[str, Any] = {
        "status": "ok" if arrived and left else "incomplete",
        "arrived": arrived.isoformat() if arrived else None,
        "left": left.isoformat() if left else None,
        "minutes": round((left - arrived).total_seconds() / 60) if arrived and left else None,
        # Time until paying
        "checkout_minutes": round((receipt_time - arrived).total_seconds() / 60) if arrived else None,
    }
    return visit


def receipt_time(receipt: dict[str, Any]) -> datetime | None:
    """Time of a receipt, the receipts have the local time without time zone"""
    parsed = dt_util.parse_datetime(receipt.get("date") or "")
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_util.get_default_time_zone())


def statistics(visits: dict[str, dict[str, Any]], receipts: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Average duration and the last visit, of the visits of the last year"""
    by_id = {receipt["id"]: receipt for receipt in receipts}
    since = now - timedelta(days=STATISTICS_DAYS)
    measured = []
    for receipt_id, visit in visits.items():
        receipt = by_id.get(receipt_id)
        time = receipt_time(receipt) if receipt else None
        if visit.get("status") == "ok" and time and time >= since and 0 < visit["minutes"] <= MAX_MINUTES:
            measured.append((time, receipt, visit))
    measured.sort(key=lambda entry: entry[0])
    by_weekday: list[list[int]] = [[] for _ in range(7)]
    for time, _, visit in measured:
        by_weekday[time.weekday()].append(visit["minutes"])
    minutes = [visit["minutes"] for _, _, visit in measured]
    checkout = [visit["checkout_minutes"] for _, _, visit in measured if visit.get("checkout_minutes") is not None]
    last = measured[-1] if measured else None
    return {
        "visits": len(measured),
        "average_minutes": round(sum(minutes) / len(minutes), 1) if minutes else None,
        "average_checkout_minutes": round(sum(checkout) / len(checkout), 1) if checkout else None,
        "shortest_minutes": min(minutes) if minutes else None,
        "longest_minutes": max(minutes) if minutes else None,
        "by_weekday": [round(sum(day) / len(day), 1) if day else None for day in by_weekday],
        "last": (
            {
                "receipt_id": last[1]["id"],
                "date": last[1]["date"],
                "store": last[1]["store"],
                "store_id": last[1]["store_id"],
                **{key: last[2][key] for key in ("arrived", "left", "minutes", "checkout_minutes")},
            }
            if last
            else None
        ),
    }


def store_zones(hass: HomeAssistant, location: tuple[float, float]) -> list[str]:
    """Names of the zones around a store, a person in such a zone is at the store"""
    names = []
    for zone in hass.states.async_all("zone"):
        latitude, longitude = zone.attributes.get("latitude"), zone.attributes.get("longitude")
        if zone.entity_id == "zone.home" or not isinstance(latitude, (int, float)):
            continue
        if not isinstance(longitude, (int, float)):
            continue
        radius = zone.attributes.get("radius") or 0
        if distance(latitude, longitude, *location) <= max(ZONE_DISTANCE, radius):
            names.append(zone.name)
    return names


class VisitTracker:
    """Visits of the stores of the receipts of an account, kept in the storage of Home Assistant"""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}_visits_{entry_id}")
        self._data: dict[str, Any] | None = None

    async def _async_load(self) -> dict[str, Any]:
        if self._data is None:
            stored = await self._store.async_load() or {}
            self._data = {"visits": stored.get("visits") or {}, "locations": stored.get("locations") or {}}
        return self._data

    async def async_locations(self, store_ids: Iterable[str], load: Any) -> dict[str, tuple[float, float]]:
        """
        Coordinates of stores, loaded with load(store_id) (the store API of Lidl Plus) and kept for a month.
        """
        data = await self._async_load()
        now = dt_util.utcnow()
        changed = False
        for store_id in dict.fromkeys(store_ids):
            known = data["locations"].get(store_id)
            checked = dt_util.parse_datetime(known["checked"]) if known else None
            if checked and now - checked < LOCATION_REFRESH:
                continue
            try:
                store = await self.hass.async_add_executor_job(load, store_id)
                location = (store or {}).get("location") or {}
                latitude, longitude = float(location["latitude"]), float(location["longitude"])
            except Exception as exc:  # noqa: BLE001
                # The store is tried again with the next update, a known location stays
                _LOGGER.debug("Location of store %s could not be loaded: %s", store_id, exc)
                continue
            data["locations"][store_id] = {"latitude": latitude, "longitude": longitude, "checked": now.isoformat()}
            changed = True
        if changed:
            await self._store.async_save(data)
        return {
            store_id: (entry["latitude"], entry["longitude"])
            for store_id, entry in data["locations"].items()
            if store_id in store_ids
        }

    async def async_update(
        self, receipts: list[dict[str, Any]], locations: dict[str, tuple[float, float]], entity_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Check the receipts of the last days that have no final visit yet, return the visits of all receipts"""
        data = await self._async_load()
        visits: dict[str, dict[str, Any]] = data["visits"]
        if not entity_ids or "recorder" not in self.hass.config.components:
            return visits
        # pylint: disable-next=import-outside-toplevel
        from homeassistant.components.recorder import get_instance, history

        recorder = get_instance(self.hass)
        now = dt_util.utcnow()
        # Older locations may have been removed from the database already
        oldest = now - timedelta(days=recorder.keep_days) + WINDOW_BEFORE
        changed = False
        for receipt in receipts:
            known = visits.get(receipt["id"])
            time = receipt_time(receipt)
            if (known and known.get("final")) or time is None or not oldest <= time <= now:
                continue
            location = locations.get(receipt["store_id"])
            if location is None:
                continue
            states = await recorder.async_add_executor_job(
                partial(
                    history.get_significant_states,
                    self.hass,
                    time - WINDOW_BEFORE,
                    time + WINDOW_AFTER,
                    entity_ids,
                    include_start_time_state=True,
                    significant_changes_only=False,
                    minimal_response=False,
                    no_attributes=False,
                )
            )
            zone_names = store_zones(self.hass, location)
            visit = self._best_visit(states, time, location, zone_names)
            visit.update(
                store_id=receipt["store_id"],
                zone=zone_names[0] if zone_names else None,
                checked=now.isoformat(),
                final=visit["status"] == "ok" or now - time > FINAL_AFTER,
            )
            if visit != known:
                visits[receipt["id"]] = visit
                changed = True
        if changed:
            await self._store.async_save(data)
        return visits

    @staticmethod
    def _best_visit(
        states: dict[str, list[Any]], time: datetime, location: tuple[float, float], zone_names: list[str]
    ) -> dict[str, Any]:
        """The visit of the person that was at the store, a complete one before an incomplete one"""
        ranking = ("ok", "incomplete", "not_seen", "no_location")
        best: dict[str, Any] = {"status": "no_location"}
        for entity_id, entity_states in states.items():
            visit = find_visit(
                [state for state in entity_states if isinstance(state, State)], time, location, zone_names
            )
            if ranking.index(visit["status"]) < ranking.index(best["status"]):
                best = {**visit, "entity_id": entity_id}
        return best
