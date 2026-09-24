"""Tests for the REST API used by AI assistants and other programs."""

from __future__ import annotations

import io
import zipfile
from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator, WebSocketGenerator

from .conftest import NOW, FakeApiState


@pytest.fixture(autouse=True)
def frozen_time(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(NOW)


@pytest.fixture
async def get(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, api_state: FakeApiState, config_entry: MockConfigEntry
):
    """GET of an endpoint of the API with a long-lived access token"""
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client()

    async def request(path: str, status: int = 200) -> Any:
        response = await client.get(f"/api/lidl_plus{path}")
        assert response.status == status, await response.text()
        return await response.json()

    return request


async def test_description(get) -> None:
    index = await get("")
    assert index["endpoints"]["/api/lidl_plus/search"] == "Search everything"
    spec = await get("/openapi.json")
    assert spec["openapi"] == "3.1.0"
    operations = {operation["operationId"] for methods in spec["paths"].values() for operation in methods.values()}
    assert {"listReceipts", "getLeaflet", "search", "exportData", "addArticle", "changeArticle"} <= operations
    assert set(index["endpoints"]) == {path for path, methods in spec["paths"].items() if "get" in methods}
    # The changes of the article database
    assert index["changes"]["POST /api/lidl_plus/articles"] == "Add an article"
    assert index["changes"]["DELETE /api/lidl_plus/articles/{key}/images/{image_id}"] == "Remove a photo of an article"
    body = spec["paths"]["/api/lidl_plus/articles"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert body["required"] == ["name"]
    assert body["properties"]["nutrition"]["properties"]["salt"]["type"] == ["number", "null"]
    assert spec["paths"]["/api/lidl_plus/leaflets"]["get"]["parameters"][1]["schema"]["enum"] == [
        "active",
        "current",
        "upcoming",
        "expired",
        "all",
    ]


async def test_accounts_and_summary(get, config_entry: MockConfigEntry) -> None:
    accounts = await get("/accounts")
    assert [(account["entry_id"], account["title"], account["receipts"]) for account in accounts] == [
        (config_entry.entry_id, "Lidl Plus (DE)", 3)
    ]
    summary = await get("/summary")
    assert (summary["receipts"], summary["total_spent"], summary["savings_total"]) == (3, 40.0, 0.5)
    assert (summary["offers_current"], summary["offers_upcoming"], summary["offers_for_you"]) == (1, 1, 1)
    assert summary["most_bought"][0]["name"] == "Milch"
    await get("/summary?entry_id=unknown", 404)


async def test_receipts(get) -> None:
    receipts = await get("/receipts?from=2026-05-01")
    assert [receipt["id"] for receipt in receipts["results"]] == ["t3", "t2"]
    assert receipts["results"][0]["item_count"] == 2
    assert "items" not in receipts["results"][0]

    receipts = await get("/receipts?items=true&search=kaffee")
    assert [receipt["id"] for receipt in receipts["results"]] == ["t3"]
    assert receipts["results"][0]["items"][1]["discounts"] == [{"text": "Lidl Plus Rabatt", "amount": -0.5}]
    assert [receipt["id"] for receipt in (await get("/receipts?store=nord"))["results"]] == ["t2"]
    assert [receipt["id"] for receipt in (await get("/receipts?store=DE2000"))["results"]] == ["t2"]
    assert [receipt["id"] for receipt in (await get("/receipts?to=2026-04-30"))["results"]] == ["t1"]

    paged = await get("/receipts?limit=1&offset=1")
    assert (paged["total"], paged["offset"], paged["limit"]) == (3, 1, 1)
    assert [receipt["id"] for receipt in paged["results"]] == ["t2"]

    assert (await get("/receipts/t1"))["total"] == 12.5
    await get("/receipts/unknown", 404)


async def test_products_and_spending(get) -> None:
    products = await get("/products?search=milch")
    assert [(product["name"], product["purchase_count"]) for product in products["results"]] == [("Milch", 3)]
    assert (await get("/products?sort=spent"))["results"][0]["name"] == "Kaffee"
    assert (await get("/products?sort=name"))["results"][0]["name"] == "Brot"

    coffee = await get("/products/c")
    assert [purchase["receipt_id"] for purchase in coffee["purchases"]] == ["t3"]
    assert [offer["id"] for offer in coffee["offers"]] == ["o1"]
    await get("/products/unknown", 404)

    spending = await get("/spending?from=2026-05-01&to=2026-05-31")
    assert (spending["receipts"], spending["total"], spending["savings"]) == (2, 27.5, 0.5)
    assert spending["by_store"] == {"Lidl Musterstadt": 20.0, "Lidl Nord": 7.5}
    assert (spending["food"], spending["non_food"]) == (7.97, 3.95)


async def test_offers(get) -> None:
    assert [offer["id"] for offer in (await get("/offers"))["results"]] == ["o1", "o2"]
    assert [offer["id"] for offer in (await get("/offers?status=upcoming"))["results"]] == ["o2"]
    # Offers that ended come from the cache
    assert [offer["id"] for offer in (await get("/offers?status=all"))["results"]] == ["o3", "o1", "o2"]
    assert [offer["id"] for offer in (await get("/offers?status=expired"))["results"]] == ["o3"]
    bought = (await get("/offers?bought=true"))["results"]
    assert [(offer["id"], offer["bought_products"]) for offer in bought] == [("o1", [{"id": "c", "name": "Kaffee"}])]
    assert [offer["id"] for offer in (await get("/offers?search=bellarom"))["results"]] == ["o1"]
    await get("/offers?status=curent", 400)


async def test_leaflets(get) -> None:
    leaflets = (await get("/leaflets"))["results"]
    assert [(leaflet["id"], leaflet["status"], leaflet["product_count"]) for leaflet in leaflets] == [
        ("l1", "current", 1),
        ("l2", "upcoming", 1),
    ]
    assert "pages" not in leaflets[0]
    assert [leaflet["id"] for leaflet in (await get("/leaflets?status=all"))["results"]] == ["l0", "l1", "l2"]
    assert [leaflet["id"] for leaflet in (await get("/leaflets?status=expired"))["results"]] == ["l0"]
    assert not (await get("/leaflets?category=reisen"))["results"]
    await get("/leaflets?status=old", 400)

    leaflet = await get("/leaflets/l1")
    assert leaflet["pages"][0]["text"] == "Kaffee ganze Bohnen 1 kg"
    assert leaflet["products"][0]["pages"] == [2]
    assert (await get("/leaflets/l0"))["status"] == "expired"
    await get("/leaflets/unknown", 404)


async def test_search(get) -> None:
    result = await get("/search?q=kaffee")
    assert result["query"] == "kaffee"
    assert [product["name"] for product in result["products"]] == ["Kaffee"]
    assert [offer["id"] for offer in result["offers"]] == ["o1"]
    assert [entry["leaflet"]["id"] for entry in result["leaflets"]] == ["l1", "l2"]
    await get("/search", 400)


async def test_coupons_and_stores(get) -> None:
    coupons = await get("/coupons")
    assert [(coupon["id"], coupon["activated"]) for coupon in coupons] == [("c1", False), ("c2", True)]
    stores = await get("/stores")
    assert [store["id"] for store in stores["visited"]] == ["DE1234", "DE2000"]
    assert stores["offer_stores"] == ["DE1234"]


async def test_export(get, hass: HomeAssistant, hass_client: ClientSessionGenerator) -> None:
    await get("/accounts")
    client = await hass_client()
    response = await client.get("/api/lidl_plus/export?dataset=items&format=csv")
    assert response.status == 200
    assert response.headers["Content-Disposition"] == 'attachment; filename="lidl_plus_items_2026-05-15.csv"'
    lines = (await response.read()).decode("utf-8-sig").splitlines()
    assert lines[0].startswith("receipt_id;date;")
    assert len(lines) == 7

    response = await client.get("/api/lidl_plus/export")
    assert response.headers["Content-Type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(await response.read())) as archive:
        assert {"receipts.csv", "leaflets.csv", "lidl_plus_cache.json"} <= set(archive.namelist())

    assert (await client.get("/api/lidl_plus/export?dataset=unknown")).status == 400
    assert (await client.get("/api/lidl_plus/export?dataset=items&format=xlsx")).status == 400
    # A single table is no ZIP file
    assert (await client.get("/api/lidl_plus/export?dataset=items&format=zip")).status == 400


async def test_authentication(
    get, hass_client_no_auth: ClientSessionGenerator, hass_ws_client: WebSocketGenerator, hass: HomeAssistant
) -> None:
    await get("/accounts")
    client = await hass_client_no_auth()
    assert (await client.get("/api/lidl_plus/summary")).status == 401
    assert (await client.get("/api/lidl_plus/openapi.json")).status == 401

    # The panel downloads exports with a signed address, like Home Assistant does for backups
    websocket = await hass_ws_client(hass)
    await websocket.send_json_auto_id({"type": "auth/sign_path", "path": "/api/lidl_plus/export?dataset=receipts"})
    signed = (await websocket.receive_json())["result"]["path"]
    response = await client.get(signed)
    assert response.status == 200
    assert (await response.read()).decode("utf-8-sig").startswith("id;date;")


async def test_token_of_an_assistant_and_status_of_now(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    api_state: FakeApiState,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A user without administrator rights and its long-lived token, like for an AI assistant"""
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    # The first user becomes the owner
    await hass.auth.async_create_user("Besitzer")
    user = await hass.auth.async_create_user("KI-Assistent", group_ids=["system-users"])
    assert not user.is_admin
    refresh_token = await hass.auth.async_create_refresh_token(
        user,
        client_name="Bot",
        token_type=TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN,
        access_token_expiration=timedelta(days=3650),
    )
    headers = {"Authorization": f"Bearer {hass.auth.async_create_access_token(refresh_token)}"}
    client = await hass_client_no_auth()

    async def get(path: str) -> Any:
        response = await client.get(f"/api/lidl_plus{path}", headers=headers)
        assert response.status == 200, await response.text()
        return await response.json()

    assert [offer["id"] for offer in (await get("/offers"))["results"]] == ["o1", "o2"]
    # Two days later, without an update of the data in between: the offer and the leaflet of last week ended
    freezer.move_to("2026-05-17T12:00:00+00:00")
    assert [offer["id"] for offer in (await get("/offers"))["results"]] == ["o2"]
    assert [offer["id"] for offer in (await get("/offers?status=expired"))["results"]] == ["o3", "o1"]
    assert [leaflet["id"] for leaflet in (await get("/leaflets"))["results"]] == ["l2"]
    assert [leaflet["id"] for leaflet in (await get("/leaflets?status=expired"))["results"]] == ["l0", "l1"]
    summary = await get("/summary")
    assert (summary["offers_current"], summary["offers_upcoming"], summary["offers_for_you"]) == (0, 1, 0)
    assert not (await get("/search?q=kaffee"))["offers"]
