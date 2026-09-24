"""Fixtures for the tests of the Home Assistant integration."""

from __future__ import annotations

from collections.abc import Generator
from datetime import date
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lidl_plus._lidlplus import analytics
from custom_components.lidl_plus.const import CONF_COUNTRY, CONF_LANGUAGE, CONF_REFRESH_TOKEN, DOMAIN
from sample_data import OPENING_HOURS, flyer, item, leaflet, leaflet_overview, offer, ticket

LOYALTY_ID = "4000000123456"
NOW = "2026-05-15T12:00:00+00:00"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components."""


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir: str) -> str:
    """Write the cache and legacy files into a temporary config directory."""
    return hass_tmp_config_dir


class FakeApiState:
    """Data and behavior of the fake Lidl Plus API, shared by all its instances."""

    def __init__(self) -> None:
        self.tickets = [
            ticket(
                "t1",
                "2026-04-10T10:00:00+02:00",
                "12,50",
                items=[item("a", "Milch", "1,09", 2), item("b", "Brot", "2,49")],
            ),
            ticket(
                "t2",
                "2026-05-02T10:00:00+02:00",
                "7,50",
                store="Lidl Nord",
                store_id="DE2000",
                items=[item("a", "Milch", "1,19"), item("s", "Shampoo", "3,95", tax="B")],
            ),
            ticket(
                "t3",
                "2026-05-12T18:30:00+02:00",
                "20,00",
                items=[item("a", "Milch", "1,29"), item("c", "Kaffee", "5,99", discount=-0.5)],
            ),
        ]
        self.coupons: Any = {
            "sections": [
                {
                    "coupons": [
                        {"id": "c1", "title": "Coupon A", "isActivated": False},
                        {"id": "c2", "title": "Coupon B", "isActivated": True},
                    ]
                }
            ]
        }
        self.loyalty_id = LOYALTY_ID
        # Raised by every call that needs a valid token
        self.error: Exception | None = None
        # Raised by account_id() after the token was renewed, e.g. for a wrong country
        self.tickets_error: Exception | None = None
        # Raised by loyalty_id(), the endpoint fails for some accounts
        self.loyalty_error: Exception | None = None
        # Raised by the public endpoints: offers, leaflets and store search
        self.public_error: Exception | None = None
        self.rotated_token = "rotated-token"
        self.activate_result = {"activated": ["Coupon A"], "failed": []}
        self.instances: list[FakeLidlPlusApi] = []
        self.initial_tokens: list[str] = []
        # Offers of the stores: one current, one upcoming and one that ended
        self.store_offers = {
            "DE1234": [
                offer("o1", "Kaffee Crema", ["c"], "2026-05-10T22:00:00Z", "2026-05-16T21:59:59Z", brand="BELLAROM"),
                offer("o2", "Butter", ["x"], "2026-05-17T22:00:00Z", "2026-05-23T21:59:59Z"),
                offer("o3", "Brot", ["b"], "2026-05-01T22:00:00Z", "2026-05-03T21:59:59Z"),
            ],
            "DE2000": [offer("o4", "Shampoo", ["s"], "2026-05-10T22:00:00Z", "2026-05-16T21:59:59Z")],
        }
        self.offer_calls: list[list[str]] = []
        self.offer_archive: dict[str, dict] = {}
        self.leaflets = leaflet_overview(
            (
                "Filial-Angebote",
                [
                    leaflet("l1", "Aktionsprospekt", "aktion-1", "2026-05-11", "2026-05-16"),
                    leaflet("l2", "Aktionsprospekt", "aktion-2", "2026-05-18", "2026-05-23"),
                    leaflet("l0", "Aktionsprospekt", "aktion-0", "2026-04-06", "2026-04-11"),
                ],
            )
        )
        self.flyers = {
            "aktion-1": flyer(
                ("Kaffee ganze Bohnen 1 kg", []),
                ("Werkzeug", [("100001", "Akku-Bohrschrauber", "39.99")]),
            )["flyer"],
            "aktion-2": flyer(("Butter Milch", [("100002", "Kaffeevollautomat", "199.00")]))["flyer"],
            "aktion-0": flyer(("Ostern", []))["flyer"],
        }
        self.leaflet_archive: dict[str, dict] = {}
        # Details of the stores (the offer region None: national leaflets) and the regional leaflet overviews
        self.store_directory: dict | None = {
            "store": {
                "id": "DE1234",
                "name": "Musterstadt",
                "address": "Hauptstraße 1",
                "postal_code": "12345",
                "locality": "Musterstadt",
            },
            "region": None,
            "region_name": "",
            "opening_hours": analytics.opening_hours(OPENING_HOURS),
            "checked": NOW,
        }
        self.leaflets_by_region: dict[int, Any] = {}
        self.region_calls: list[str] = []
        self.synced_regions: list[int | None] = []
        # Locations of the stores (store API of Lidl Plus), for the shopping duration
        self.store_locations = {
            "DE1234": {"latitude": 51.1633, "longitude": 10.4477},
            "DE2000": {"latitude": 51.1450, "longitude": 10.4600},
        }
        self.store_calls: list[str] = []
        self.found_stores = [
            {
                "storeKey": "DE3000",
                "name": "Lidl Süd",
                "address": "Südstraße 3",
                "postalCode": "12347",
                "locality": "Musterstadt",
            }
        ]


class FakeLidlPlusApi:
    """Stand-in for LidlPlusApi, rotates the refresh token like the Lidl auth server may do."""

    def __init__(
        self, state: FakeApiState, language: str, country: str, refresh_token: str = "", cache_file: str | None = None
    ) -> None:
        self._state = state
        self.language = language
        self.country = country
        self.refresh_token = refresh_token
        self.cache_file = cache_file
        state.instances.append(self)
        state.initial_tokens.append(refresh_token)

    def _authenticate(self) -> None:
        if self._state.error:
            raise self._state.error
        self.refresh_token = self._state.rotated_token

    def sync(self) -> int:
        self._authenticate()
        return len(self._state.tickets)

    def cached_data(self) -> dict:
        return {
            "tickets": {entry["id"]: entry for entry in self._state.tickets},
            "offers": self._state.offer_archive,
            "leaflets": self._state.leaflet_archive,
        }

    def sync_offers(self, store_keys: list[str]) -> int:
        """Keeps the history like LidlPlusApi.sync_offers"""
        if self._state.public_error:
            raise self._state.public_error
        self._state.offer_calls.append(list(store_keys))
        added = 0
        for store_key in store_keys:
            for entry in self._state.store_offers.get(store_key, []):
                if entry["id"] not in self._state.offer_archive:
                    self._state.offer_archive[entry["id"]] = {"offer": entry, "stores": [], "first_seen": NOW}
                    added += 1
                archived = self._state.offer_archive[entry["id"]]
                archived["last_seen"] = NOW
                if store_key not in archived["stores"]:
                    archived["stores"].append(store_key)
        return added

    def cached_offers(self) -> list[dict]:
        return analytics.archived_offers(self._state.offer_archive)

    def store(self, store_key: str) -> dict:
        """Like LidlPlusApi.store: the details of a store of the public store API"""
        self._state.store_calls.append(store_key)
        if self._state.public_error:
            raise self._state.public_error
        return {"storeKey": store_key, "location": self._state.store_locations[store_key]}

    def store_directory(self, store_key: str) -> dict | None:
        self._state.region_calls.append(store_key)
        return self._state.store_directory

    def sync_leaflets(self, categories: Any = None, region: int | None = None) -> int:
        """Keeps the history like LidlPlusApi.sync_leaflets, leaflets that ended are not loaded"""
        if self._state.public_error:
            raise self._state.public_error
        self._state.synced_regions.append(region)
        added = 0
        overview = self._state.leaflets_by_region.get(region, self._state.leaflets)
        for entry in analytics.leaflet_overview(overview):
            if entry["id"] not in self._state.leaflet_archive:
                self._state.leaflet_archive[entry["id"]] = {"first_seen": NOW}
                added += 1
            archived = self._state.leaflet_archive[entry["id"]]
            archived.update(leaflet=entry, last_seen=NOW)
            if analytics.leaflet_status(entry) != "expired" and entry["identifier"] in self._state.flyers:
                archived["details"] = analytics.leaflet_details(self._state.flyers[entry["identifier"]])
        return added

    def cached_leaflets(self, today: date | None = None, region: int | None = None) -> list[dict]:
        leaflets = analytics.archived_leaflets(self._state.leaflet_archive, today)
        return analytics.leaflets_of_region(leaflets, region) if region is not None else leaflets

    def search_stores(self, query: str, latitude: float, longitude: float) -> list[dict]:
        if self._state.public_error:
            raise self._state.public_error
        return self._state.found_stores

    def coupons(self) -> Any:
        return self._state.coupons

    def loyalty_id(self) -> str:
        self._authenticate()
        if self._state.loyalty_error:
            raise self._state.loyalty_error
        return self._state.loyalty_id

    def account_id(self) -> str | None:
        """Like LidlPlusApi.account_id: the receipts check login and country, the loyalty ID is optional"""
        self._authenticate()
        if self._state.tickets_error:
            raise self._state.tickets_error
        if self._state.loyalty_error:
            return None
        return self._state.loyalty_id

    def activate_all_coupons(self) -> dict:
        self._authenticate()
        return self._state.activate_result


@pytest.fixture
def api_state() -> Generator[FakeApiState]:
    """Replace the Lidl Plus API client in the integration and the config flow."""
    state = FakeApiState()

    def create(*args: Any, **kwargs: Any) -> FakeLidlPlusApi:
        return FakeLidlPlusApi(state, *args, **kwargs)

    with (
        patch("custom_components.lidl_plus.LidlPlusApi", side_effect=create),
        patch("custom_components.lidl_plus.config_flow.LidlPlusApi", side_effect=create),
    ):
        yield state


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Lidl Plus (DE)",
        unique_id=LOYALTY_ID,
        data={CONF_COUNTRY: "DE", CONF_LANGUAGE: "de", CONF_REFRESH_TOKEN: "old-token"},
    )
    entry.add_to_hass(hass)
    return entry
