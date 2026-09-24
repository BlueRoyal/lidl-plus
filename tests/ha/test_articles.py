"""Tests for the article database: websocket commands of the panel, barcodes, Open Food Facts, photos and REST."""

from __future__ import annotations

import base64
import io
import json
import os
import zipfile
from http import HTTPStatus
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator, WebSocketGenerator

from custom_components.lidl_plus import openfoodfacts
from custom_components.lidl_plus.article_store import MAX_IMAGE_BYTES, STORAGE_KEY, image_type

from .conftest import NOW, FakeApiState

JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF" + bytes(64)
PNG = b"\x89PNG\r\n\x1a\n" + bytes(32)
CODE = "4056489123453"
OFF = "https://world.openfoodfacts.org/api/v2/product"
OBF = "https://world.openbeautyfacts.org/api/v2/product"
OPF = "https://world.openproductsfacts.org/api/v2/product"
SKYR = {
    "code": CODE,
    "product_name_de": "Skyr Natur",
    "product_name": "Skyr",
    "brands": "Milbona, Lidl",
    "quantity": "500 g",
    "nutriments": {"energy-kcal_100g": 63, "proteins_100g": "11", "fat_100g": 0.2, "salt_100g": -1},
    "ingredients_text_de": "Magermilch, Milchsäurebakterien",
    "allergens_tags": ["en:milk"],
    "nutriscore_grade": "a",
    "image_front_url": "https://images.openfoodfacts.org/skyr.jpg",
    "image_nutrition_url": "http://insecure.invalid/n.jpg",
}


@pytest.fixture(autouse=True)
def frozen_time(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(NOW)


@pytest.fixture
async def ws(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, api_state: FakeApiState, config_entry: MockConfigEntry
):
    """Sends a websocket command and returns its result, or its error with error=True"""
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)

    async def call(message: dict[str, Any], error: bool = False) -> Any:
        await client.send_json_auto_id(message)
        response = await client.receive_json()
        assert response["success"] is not error, response
        return response["error"] if error else response["result"]

    return call


async def test_list_and_details(ws, hass: HomeAssistant) -> None:
    result = await ws({"type": "lidl_plus/articles"})
    # Bought articles, offers of the stores and products of the leaflets
    assert result["total"] == 7
    assert result["statistics"] == {"articles": 7, "bought": 4, "with_details": 0, "with_nutrition": 0}
    assert [article["key"] for article in result["articles"][:2]] == ["nr:a", "nr:c"]
    assert result["articles"][0]["name"] == "Milch"
    assert "offers" not in result["articles"][0], "lists without the long lists"

    result = await ws({"type": "lidl_plus/articles", "query": "kaffee", "sort": "name-asc"})
    assert [(article["key"], article["name"]) for article in result["articles"]] == [
        ("nr:c", "BELLAROM Kaffee Crema"),
        ("web:100002", "Kaffeevollautomat"),
    ]
    result = await ws({"type": "lidl_plus/articles", "kind": "leaflets", "sort": "name-asc", "limit": 2, "offset": 1})
    assert (result["total"], [article["key"] for article in result["articles"]]) == (3, ["nr:x", "web:100002"])
    await ws({"type": "lidl_plus/articles", "kind": "wrong"}, error=True)

    coffee = await ws({"type": "lidl_plus/article", "key": "nr:c"})
    assert coffee["receipt_names"] == ["Kaffee"]
    assert [offer["id"] for offer in coffee["offers"]] == ["o1"]
    # Purchases of the article for the price chart
    assert (coffee["product"]["purchase_count"], coffee["product"]["savings"]) == (1, 0.5)
    drill = await ws({"type": "lidl_plus/article", "key": "web:100001"})
    assert (drill["product"], drill["url"], drill["leaflets"][0]["id"]) == (None, "https://www.lidl.de/p/100001", "l1")
    assert (await ws({"type": "lidl_plus/article", "key": "nr:unknown"}, error=True))["code"] == "not_found"

    # The offers printed on the pages of a leaflet, with the key of their article
    leaflet = await ws({"type": "lidl_plus/leaflet", "leaflet_id": "l2"})
    assert [(offer["id"], offer["pages"], offer["article_key"]) for offer in leaflet["offers"]] == [("o2", [1], "nr:x")]
    butter = await ws({"type": "lidl_plus/article", "key": "nr:x"})
    assert [entry["id"] for entry in butter["leaflets"]] == ["l2"]
    # Offers of the panel know their article
    data = await ws({"type": "lidl_plus/panel_data"})
    assert [offer["article_key"] for offer in data["offers"]] == ["nr:c", "nr:x"]
    assert "catalog" not in data


async def test_save_and_delete_details(ws, hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    saved = await ws(
        {
            "type": "lidl_plus/article_save",
            "key": "nr:c",
            "barcodes": [" 4056489-123453 "],
            "package_size": " 500 g ",
            "nutrition": {"energy_kcal": "2", "fat": None, "protein": 0.1},
            "nutrition_basis": "100ml",
            "ingredients": "Kaffee",
            "notes": "",
            "sources": {"nutrition": "Open Food Facts"},
        }
    )
    assert saved["barcodes"] == [CODE]
    assert (saved["package_size"], saved["nutrition"], saved["nutrition_basis"]) == (
        "500 g",
        {"energy_kcal": 2.0, "protein": 0.1},
        "100ml",
    )
    assert saved["sources_of_details"] == {"nutrition": "Open Food Facts"}
    assert saved["has_nutrition"] and saved["has_details"]
    assert saved["product"]["purchase_count"] == 1, "the purchases are part of the answer"
    stored = hass_storage[STORAGE_KEY]["data"]["articles"]["nr:c"]
    assert (stored["package_size"], stored["ingredients"], "notes" in stored) == ("500 g", "Kaffee", False)

    # The name of Lidl stays as lidl_name
    milk = await ws({"type": "lidl_plus/article_save", "key": "nr:a", "name": "Frische Milch"})
    assert (milk["name"], milk["lidl_name"]) == ("Frische Milch", "Milch")
    result = await ws({"type": "lidl_plus/articles", "kind": "details", "sort": "name-asc"})
    assert [article["key"] for article in result["articles"]] == ["nr:c", "nr:a"]
    assert (await ws({"type": "lidl_plus/articles", "query": CODE}))["total"] == 1

    # Invalid values
    error = await ws({"type": "lidl_plus/article_save", "key": "nr:a", "barcodes": ["4056489123450"]}, error=True)
    assert error["code"] == "invalid_barcode"
    await ws({"type": "lidl_plus/article_save", "key": "nr:a", "nutrition": {"fat": "viel"}}, error=True)
    await ws({"type": "lidl_plus/article_save", "key": "nr:a", "nutrition": {"fat": -1}}, error=True)
    await ws({"type": "lidl_plus/article_save", "key": "nr:a", "image": "javascript:alert(1)"}, error=True)
    await ws({"type": "lidl_plus/article_save", "key": "nr:unknown", "notes": "x"}, error=True)

    # Articles added by hand need a name, a barcode belongs to one article only
    assert (await ws({"type": "lidl_plus/article_save", "brand": "Cien"}, error=True))["code"] == "invalid_format"
    soap = await ws({"type": "lidl_plus/article_save", "name": "Handseife", "barcodes": ["036000291452"]})
    assert soap["key"].startswith("own:")
    assert (soap["sources"], soap["purchase_count"]) == (["own"], 0)
    error = await ws({"type": "lidl_plus/article_save", "key": "nr:a", "barcodes": ["0036000291452"]}, error=True)
    assert (error["code"], error["message"]) == (
        "barcode_in_use",
        "Der Barcode 0036000291452 gehört schon zu Handseife",
    )
    assert (await ws({"type": "lidl_plus/article_save", "key": soap["key"], "name": " "}, error=True))["code"] == (
        "invalid_format"
    )
    result = await ws({"type": "lidl_plus/articles", "kind": "own"})
    assert [article["name"] for article in result["articles"]] == ["Handseife"]
    assert result["statistics"]["articles"] == 8

    # Empty fields are removed, deleting removes all details
    cleared = await ws({"type": "lidl_plus/article_save", "key": "nr:c", "package_size": "", "nutrition": {}})
    assert (cleared["package_size"], cleared["nutrition"], cleared["barcodes"]) == ("", {}, [CODE])
    deleted = await ws({"type": "lidl_plus/article_delete", "key": "nr:c"})
    assert (deleted["barcodes"], deleted["ingredients"], deleted["name"]) == ([], "", "BELLAROM Kaffee Crema")
    assert "nr:c" not in hass_storage[STORAGE_KEY]["data"]["articles"]
    # An article added by hand is gone
    assert await ws({"type": "lidl_plus/article_delete", "key": soap["key"]}) is None
    assert (await ws({"type": "lidl_plus/articles", "kind": "own"}))["total"] == 0


async def test_barcode_lookup(ws, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{OFF}/{CODE}.json", json={"status": 1, "product": SKYR})
    result = await ws({"type": "lidl_plus/barcode", "code": f" {CODE} "})
    assert (result["code"], result["articles"], result["lookup_error"]) == (CODE, [], None)
    product = result["product"]
    assert (product["source"], product["url"]) == ("Open Food Facts", f"https://world.openfoodfacts.org/product/{CODE}")
    assert (product["name"], product["brand"], product["quantity"]) == ("Skyr Natur", "Milbona", "500 g")
    # kJ from kcal, invalid values left out
    assert product["nutrition"] == {"energy_kj": 264, "energy_kcal": 63.0, "fat": 0.2, "protein": 11.0}
    assert (product["nutrition_basis"], product["nutriscore"], product["allergens"]) == ("100g", "a", ["milk"])
    assert (product["image"], product["image_nutrition"]) == ("https://images.openfoodfacts.org/skyr.jpg", "")
    assert aioclient_mock.mock_calls[0][1].query["fields"].startswith("code,")
    assert aioclient_mock.mock_calls[0][3]["User-Agent"].startswith("LidlPlusForHomeAssistant/")
    # Asked once a day
    await ws({"type": "lidl_plus/barcode", "code": CODE})
    assert aioclient_mock.call_count == 1

    # A known barcode opens its article, Open Food Facts only on request
    await ws({"type": "lidl_plus/article_save", "key": "nr:s", "barcodes": [CODE]})
    result = await ws({"type": "lidl_plus/barcode", "code": CODE})
    assert ([article["key"] for article in result["articles"]], result["product"]) == (["nr:s"], None)
    assert (await ws({"type": "lidl_plus/barcode", "code": CODE, "lookup": True}))["product"]["name"] == "Skyr Natur"
    assert (await ws({"type": "lidl_plus/barcode", "code": "123"}, error=True))["code"] == "invalid_barcode"


async def test_barcode_of_other_databases(ws, aioclient_mock: AiohttpClientMocker) -> None:
    code = "96385074"
    # Unknown to Open Food Facts, a cosmetic product of Open Beauty Facts
    aioclient_mock.get(f"{OFF}/{code}.json", status=HTTPStatus.NOT_FOUND, json={"status": 0})
    aioclient_mock.get(
        f"{OBF}/{code}.json",
        json={
            "status": 1,
            "product": {
                "code": code,
                "product_name": "Duschgel",
                "quantity": "300 ml",
                "ingredients_text": "Aqua, Sodium Laureth Sulfate",
            },
        },
    )
    product = (await ws({"type": "lidl_plus/barcode", "code": code}))["product"]
    assert (product["source"], product["ingredients"], product["nutrition"], product["nutrition_basis"]) == (
        "Open Beauty Facts",
        "Aqua, Sodium Laureth Sulfate",
        {},
        "100ml",
    )

    # Unknown everywhere, and no database reachable
    aioclient_mock.clear_requests()
    unknown = "036000291452"
    aioclient_mock.get(f"{OFF}/{unknown}.json", json={"status": 0})
    aioclient_mock.get(f"{OBF}/{unknown}.json", status=HTTPStatus.NOT_FOUND)
    aioclient_mock.get(f"{OPF}/{unknown}.json", json={"status": 0})
    result = await ws({"type": "lidl_plus/barcode", "code": unknown})
    assert (result["product"], result["lookup_error"]) == (None, None)
    offline = "4000000000006"
    aioclient_mock.get(f"{OFF}/{offline}.json", status=HTTPStatus.SERVICE_UNAVAILABLE)
    aioclient_mock.get(f"{OBF}/{offline}.json", exc=TimeoutError())
    aioclient_mock.get(f"{OPF}/{offline}.json", text="<html>")
    result = await ws({"type": "lidl_plus/barcode", "code": offline})
    assert result["product"] is None
    assert result["lookup_error"].startswith(
        "Open Food Facts: HTTP 503; Open Beauty Facts: TimeoutError; Open Products"
    )


def test_image_type() -> None:
    assert image_type(JPEG) == ("image/jpeg", ".jpg")
    assert image_type(PNG) == ("image/png", ".png")
    assert image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == ("image/webp", ".webp")
    assert image_type(b"<svg onload=alert(1)>") is None


def test_normalize_product() -> None:
    product = openfoodfacts.normalize_product(
        {
            "product_name": "Cola",
            "quantity": "1,5 l",
            "nutriments": {"energy_100g": 180},
            "nutriscore_grade": "unknown",
        },
        "Open Food Facts",
        "https://world.openfoodfacts.org/product/1",
    )
    assert (product["nutrition"], product["nutrition_basis"], product["nutriscore"]) == (
        {"energy_kj": 180.0, "energy_kcal": 43},
        "100ml",
        "",
    )


async def test_photos(
    ws, hass: HomeAssistant, hass_client: ClientSessionGenerator, hass_client_no_auth: ClientSessionGenerator
) -> None:
    upload = {"type": "lidl_plus/article_image", "key": "nr:a", "kind": "nutrition"}
    article = await ws({**upload, "data": base64.b64encode(JPEG).decode()})
    image = article["images"][0]
    assert (image["kind"], image["type"]) == ("nutrition", "image/jpeg")
    # The panel shows the photo without access token
    assert image["url"].startswith(f"/api/lidl_plus/images/{image['id']}?authSig=")
    path = hass.config.path("lidl_plus", "images", f"{image['id']}.jpg")
    assert os.path.isfile(path)
    assert (await ws({"type": "lidl_plus/article", "key": "nr:a"}))["has_details"]

    anonymous = await hass_client_no_auth()
    response = await anonymous.get(image["url"])
    assert response.status == 200
    assert (await response.read(), response.headers["Content-Type"]) == (JPEG, "image/jpeg")
    assert (await anonymous.get(f"/api/lidl_plus/images/{image['id']}")).status == 401
    client = await hass_client()
    assert (await client.get(f"/api/lidl_plus/images/{image['id']}")).status == 200
    assert (await client.get(f"/api/lidl_plus/images/{'0' * 32}")).status == 404
    assert (await client.get("/api/lidl_plus/images/..%2F..%2Fsecrets.yaml")).status in (400, 404)

    # Only photos, not too large, of known articles
    assert (await ws({**upload, "data": "no base64!"}, error=True))["code"] == "invalid_format"
    text = base64.b64encode(b"<svg onload=alert(1)>").decode()
    assert (await ws({**upload, "data": text}, error=True))["message"] == "Nur Fotos im Format JPEG, PNG oder WebP"
    large = base64.b64encode(JPEG + bytes(MAX_IMAGE_BYTES + 1 - len(JPEG))).decode()
    assert (await ws({**upload, "data": large}, error=True))["code"] == "too_large"
    unknown = {**upload, "key": "nr:unknown", "data": base64.b64encode(JPEG).decode()}
    assert (await ws(unknown, error=True))["code"] == "not_found"
    await ws({**upload, "kind": "selfie", "data": base64.b64encode(JPEG).decode()}, error=True)

    second = (await ws({**upload, "kind": "front", "data": base64.b64encode(PNG).decode()}))["images"][1]
    assert os.path.isfile(hass.config.path("lidl_plus", "images", f"{second['id']}.png"))
    article = await ws({"type": "lidl_plus/article_image_delete", "key": "nr:a", "image_id": image["id"]})
    assert [entry["id"] for entry in article["images"]] == [second["id"]]
    assert not os.path.exists(path)
    await ws({"type": "lidl_plus/article_image_delete", "key": "nr:a", "image_id": image["id"]}, error=True)
    # Deleting the details removes the photos
    await ws({"type": "lidl_plus/article_delete", "key": "nr:a"})
    assert not os.path.exists(hass.config.path("lidl_plus", "images", f"{second['id']}.png"))


async def test_rest_api_and_export(
    ws, hass: HomeAssistant, hass_client: ClientSessionGenerator, api_state: FakeApiState
) -> None:
    await ws(
        {
            "type": "lidl_plus/article_save",
            "key": "nr:a",
            "barcodes": [CODE],
            "nutrition": {"protein": 3.4},
            "ingredients": "Milch",
        }
    )
    photo = (await ws({"type": "lidl_plus/article_image", "key": "nr:a", "data": base64.b64encode(JPEG).decode()}))[
        "images"
    ][0]
    client = await hass_client()

    response = await client.get("/api/lidl_plus/articles?q=milch")
    articles = await response.json()
    assert (articles["total"], articles["results"][0]["key"], articles["results"][0]["image_count"]) == (1, "nr:a", 1)
    assert (await client.get("/api/lidl_plus/articles?kind=wrong")).status == 400
    response = await client.get("/api/lidl_plus/articles?kind=nutrition&sort=name-asc&limit=1")
    assert [entry["key"] for entry in (await response.json())["results"]] == ["nr:a"]
    article = await (await client.get("/api/lidl_plus/articles/nr:a")).json()
    assert (article["nutrition"], article["product"]["name"]) == ({"protein": 3.4}, "Milch")
    # The photos of the REST API are loaded with the access token of the request
    assert article["images"][0]["url"] == f"/api/lidl_plus/images/{photo['id']}"
    assert (await client.get(article["images"][0]["url"])).status == 200
    assert (await client.get("/api/lidl_plus/articles/nr:unknown")).status == 404
    spec = await (await client.get("/api/lidl_plus/openapi.json")).json()
    assert {"listArticles", "getArticle", "getImage", "getShoppingDuration"} <= {
        operation["get"]["operationId"] for operation in spec["paths"].values()
    }

    # The export contains the article database with the details added by hand
    response = await client.get("/api/lidl_plus/export?dataset=articles&format=json")
    rows = {row["key"]: row for row in await response.json()}
    assert (rows["nr:a"]["barcodes"], rows["nr:a"]["protein"], rows["nr:a"]["photos"]) == (CODE, 3.4, 1)
    response = await client.get("/api/lidl_plus/export?dataset=all")
    with zipfile.ZipFile(io.BytesIO(await response.read())) as archive:
        details = json.loads(archive.read("lidl_plus_articles.json"))
        assert details["nr:a"]["ingredients"] == "Milch"
        assert "articles.csv" in archive.namelist()

    response = await hass.services.async_call(
        "lidl_plus", "export", {"dataset": "articles", "format": "csv"}, blocking=True, return_response=True
    )
    with open(response["files"][0], encoding="utf-8-sig") as file:
        assert file.readline().startswith("key;name;brand")
