"""Article database: the changes shared by the websocket commands of the panel and the REST API, and the commands."""

from __future__ import annotations

import base64
import binascii
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_integration

from . import openfoodfacts
from ._lidlplus.articles import (
    FILTERS,
    IMAGE_KINDS,
    NUTRIENTS,
    NUTRITION_BASES,
    SORTS,
    article_list_entry,
    barcode_key,
    find_articles,
    normalize_barcode,
)
from .article_store import MAX_IMAGE_BYTES, ArticleError, article_store
from .const import DOMAIN, KEY_CATALOG, KEY_PRODUCTS
from .coordinator import LidlPlusConfigEntry

_DATA_LOOKUPS = f"{DOMAIN}_barcode_lookups"
IMAGE_URL = "/api/lidl_plus/images"
# Signed addresses of the photos shown in the panel
_IMAGE_URL_VALIDITY = timedelta(hours=12)
# Barcodes are looked up again after a day, or an hour if the product was not found
_LOOKUP_TTL = 24 * 3600
_NOT_FOUND_TTL = 3600
_TREND = vol.In(("", "up", "down", "stable"))
# Details that may have been taken from another database
_DETAIL_SOURCES = ("name", "brand", "package_size", "nutrition", "ingredients", "image")
_CHANGES = (*_DETAIL_SOURCES, "notes", "barcodes", "nutrition_basis", "sources")
# Error codes, the REST API answers them with these HTTP status codes
NOT_FOUND = websocket_api.ERR_NOT_FOUND
INVALID_FORMAT = websocket_api.ERR_INVALID_FORMAT
INVALID_BARCODE = "invalid_barcode"
BARCODE_IN_USE = "barcode_in_use"


def _optional_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    number = vol.Coerce(float)(value)
    if not 0 <= number <= 100_000:
        raise vol.Invalid("must be between 0 and 100000")
    return round(number, 3)


# The details of an article that can be changed, for the websocket command and the REST API
ARTICLE_FIELDS: dict[Any, Any] = {
    vol.Optional("name"): vol.All(str, vol.Length(max=200)),
    vol.Optional("brand"): vol.All(str, vol.Length(max=100)),
    vol.Optional("package_size"): vol.All(str, vol.Length(max=100)),
    vol.Optional("ingredients"): vol.All(str, vol.Length(max=10_000)),
    vol.Optional("notes"): vol.All(str, vol.Length(max=10_000)),
    vol.Optional("image"): vol.Any("", vol.All(str, vol.Match(r"^https://"), vol.Length(max=1000))),
    vol.Optional("barcodes"): vol.All([str], vol.Length(max=20)),
    vol.Optional("nutrition"): {vol.In(NUTRIENTS): _optional_number},
    vol.Optional("nutrition_basis"): vol.In(NUTRITION_BASES),
    # Where details were taken from, e.g. {"nutrition": "Open Food Facts"}
    vol.Optional("sources"): {vol.In(_DETAIL_SOURCES): vol.All(str, vol.Length(max=100))},
}
LEAFLET_FIELDS: dict[Any, Any] = {
    vol.Required("leaflet_id"): str,
    # Without page (only to remove it): from all pages of the leaflet
    vol.Optional("page"): vol.All(int, vol.Range(min=1)),
}


def loaded_entry(hass: HomeAssistant, entry_id: str | None) -> LidlPlusConfigEntry | None:
    """The requested account, the first one if it is not loaded (anymore)"""
    entries = [entry for entry in hass.config_entries.async_loaded_entries(DOMAIN) if entry.runtime_data.data]
    return next((entry for entry in entries if entry.entry_id == entry_id), entries[0] if entries else None)


async def async_articles(hass: HomeAssistant, entry: LidlPlusConfigEntry | None) -> dict[str, dict[str, Any]]:
    """All articles of an account with their details, without account only the articles added by hand"""
    catalog = entry.runtime_data.data.get(KEY_CATALOG, {}) if entry else {}
    return await article_store(hass).async_articles(catalog)


def article_statistics(articles: dict[str, dict[str, Any]]) -> dict[str, int]:
    return {
        "articles": len(articles),
        "bought": sum(1 for article in articles.values() if article["purchase_count"]),
        "with_details": sum(1 for article in articles.values() if article["has_details"]),
        "with_nutrition": sum(1 for article in articles.values() if article["has_nutrition"]),
    }


def article_details(
    hass: HomeAssistant, entry: LidlPlusConfigEntry | None, article: dict[str, Any], sign: bool = True
) -> dict[str, Any]:
    """An article with the addresses of its photos and, if it was bought, its purchase statistics"""
    product = None
    if entry and article["key"].startswith("nr:"):
        products = entry.runtime_data.data.get(KEY_PRODUCTS, [])
        product = next((bought for bought in products if bought["id"] == article["key"][3:]), None)
    images = []
    for image in article["images"]:
        path = f"{IMAGE_URL}/{image['id']}"
        images.append(
            {
                "id": image["id"],
                "kind": image.get("kind"),
                "type": image.get("type"),
                "added": image.get("added"),
                # The panel shows them without access token
                "url": async_sign_path(hass, path, _IMAGE_URL_VALIDITY) if sign else path,
            }
        )
    return {**article, "images": images, "product": product}


def _with_barcode(articles: dict[str, dict[str, Any]], code: str) -> list[dict[str, Any]]:
    """The articles with a barcode, as EAN-13 or UPC-A"""
    wanted = barcode_key(code)
    return [
        article for article in articles.values() if any(barcode_key(other) == wanted for other in article["barcodes"])
    ]


async def _async_article(hass: HomeAssistant, entry: LidlPlusConfigEntry | None, key: str) -> dict[str, Any]:
    if (article := (await async_articles(hass, entry)).get(key)) is None:
        raise ArticleError(NOT_FOUND, "Article not found")
    return article


# ── Changes, shared by the panel and the REST API ───────────────────────────────
async def async_save_article(
    hass: HomeAssistant, entry: LidlPlusConfigEntry | None, key: str | None, fields: dict[str, Any]
) -> dict[str, Any]:
    """Add an article (without key, it needs a name) or change its details, return the article"""
    articles = await async_articles(hass, entry)
    if key is None:
        if not (fields.get("name") or "").strip():
            raise ArticleError(INVALID_FORMAT, "Ein neuer Artikel braucht einen Namen")
        key = f"own:{uuid.uuid4().hex}"
    elif key not in articles:
        raise ArticleError(NOT_FOUND, "Article not found")
    elif key.startswith("own:") and "name" in fields and not fields["name"].strip():
        raise ArticleError(INVALID_FORMAT, "Der Name darf nicht leer sein")
    changes = {field: fields[field] for field in _CHANGES if field in fields}
    if "barcodes" in changes:
        codes = []
        for text in changes["barcodes"]:
            if (code := normalize_barcode(text)) is None:
                raise ArticleError(INVALID_BARCODE, f"{text} ist kein gültiger Barcode (EAN)")
            if owner := next((other for other in _with_barcode(articles, code) if other["key"] != key), None):
                raise ArticleError(
                    BARCODE_IN_USE, f"Der Barcode {code} gehört schon zu {owner['name'] or owner['key']}"
                )
            codes.append(code)
        changes["barcodes"] = codes
    await article_store(hass).async_update(key, changes)
    # Details of an article of another account are saved, but not shown with this account
    return await _async_article(hass, entry, key)


async def async_delete_article(
    hass: HomeAssistant, entry: LidlPlusConfigEntry | None, key: str
) -> dict[str, Any] | None:
    """Remove the details and photos of an article, return it without them (None if it was added by hand)"""
    await article_store(hass).async_delete(key)
    return (await async_articles(hass, entry)).get(key)


async def async_add_article_image(
    hass: HomeAssistant, entry: LidlPlusConfigEntry | None, key: str, kind: str, content: bytes
) -> dict[str, Any]:
    """Add a photo (JPEG, PNG or WebP) to an article, return the article"""
    await _async_article(hass, entry, key)
    if kind not in IMAGE_KINDS:
        raise ArticleError(INVALID_FORMAT, f"Die Art des Fotos muss eine von {', '.join(IMAGE_KINDS)} sein")
    await article_store(hass).async_add_image(key, kind, content)
    return await _async_article(hass, entry, key)


async def async_delete_article_image(
    hass: HomeAssistant, entry: LidlPlusConfigEntry | None, key: str, image_id: str
) -> dict[str, Any] | None:
    """Remove a photo of an article, return the article"""
    if not await article_store(hass).async_delete_image(key, image_id):
        raise ArticleError(NOT_FOUND, "Photo not found")
    return (await async_articles(hass, entry)).get(key)


async def async_link_article_leaflet(
    hass: HomeAssistant,
    entry: LidlPlusConfigEntry | None,
    key: str,
    fields: dict[str, Any],
    remove: bool = False,
) -> dict[str, Any]:
    """Add an article to a page of a leaflet (fields leaflet_id and page) or remove it, return the article"""
    if entry is None or key not in await async_articles(hass, entry):
        raise ArticleError(NOT_FOUND, "Article not found")
    if (leaflet := await entry.runtime_data.async_leaflet(fields["leaflet_id"])) is None:
        raise ArticleError(NOT_FOUND, "Leaflet not found")
    page = fields.get("page")
    numbers = [entry["number"] for entry in leaflet.get("pages") or []]
    if page is None and not remove:
        raise ArticleError(INVALID_FORMAT, "Die Seite des Prospekts fehlt")
    if page is not None and numbers and page not in numbers:
        raise ArticleError(INVALID_FORMAT, f"Der Prospekt hat keine Seite {page}")
    await article_store(hass).async_link_leaflet(key, leaflet, page, remove)
    return await _async_article(hass, entry, key)


async def async_lookup_barcode(hass: HomeAssistant, code: str) -> dict[str, Any] | None:
    """The product of Open Food Facts (or its sister databases) for a barcode, remembered for a day"""
    lookups: dict[str, tuple[float, dict[str, Any] | None]] = hass.data.setdefault(_DATA_LOOKUPS, {})
    now = time.monotonic()
    if (cached := lookups.get(code)) and now - cached[0] < (_LOOKUP_TTL if cached[1] else _NOT_FOUND_TTL):
        return cached[1]
    integration = await async_get_integration(hass, DOMAIN)
    user_agent = f"LidlPlusForHomeAssistant/{integration.version} (https://github.com/BlueRoyal/lidl-plus)"
    product = await openfoodfacts.async_lookup(async_get_clientsession(hass), code, user_agent)
    lookups[code] = (now, product)
    return product


async def async_barcode(
    hass: HomeAssistant, entry: LidlPlusConfigEntry | None, text: str, lookup: bool = False
) -> dict[str, Any]:
    """
    The articles with a barcode and, for an unknown barcode (or with lookup), the product of Open Food Facts
    """
    if (code := normalize_barcode(text)) is None:
        raise ArticleError(INVALID_BARCODE, f"{text} ist kein gültiger Barcode (EAN)")
    known = [article_list_entry(article) for article in _with_barcode(await async_articles(hass, entry), code)]
    result: dict[str, Any] = {"code": code, "articles": known, "product": None, "lookup_error": None}
    if lookup or not known:
        try:
            result["product"] = await async_lookup_barcode(hass, code)
        except openfoodfacts.LookupFailed as err:
            result["lookup_error"] = str(err)
    return result


# ── Websocket commands of the panel ─────────────────────────────────────────────
async def _async_answer(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    change: Callable[[LidlPlusConfigEntry | None], Awaitable[dict[str, Any] | None]],
) -> None:
    """Send the article returned by a change with all its details, or the error of the change"""
    entry = loaded_entry(hass, msg.get("entry_id"))
    try:
        article = await change(entry)
    except ArticleError as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    connection.send_result(msg["id"], article_details(hass, entry, article) if article else None)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/articles",
        vol.Optional("entry_id"): str,
        vol.Optional("query", default=""): str,
        vol.Optional("kind", default=""): vol.In(("", *FILTERS)),
        vol.Optional("sort", default="count-desc"): vol.In(SORTS),
        vol.Optional("trend", default=""): _TREND,
        vol.Optional("bought_since", default=""): str,
        vol.Optional("offset", default=0): vol.All(int, vol.Range(min=0)),
        vol.Optional("limit", default=60): vol.All(int, vol.Range(min=1, max=500)),
    }
)
@websocket_api.async_response
async def ws_articles(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Send a page of the articles that match the query and filters."""
    articles = await async_articles(hass, loaded_entry(hass, msg.get("entry_id")))
    found = find_articles(
        articles, msg["query"], msg["kind"], msg["sort"], trend=msg["trend"], bought_since=msg["bought_since"]
    )
    page = found[msg["offset"] : msg["offset"] + msg["limit"]]
    connection.send_result(
        msg["id"],
        {
            "total": len(found),
            "offset": msg["offset"],
            "statistics": article_statistics(articles),
            "articles": [article_list_entry(article) for article in page],
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/article",
        vol.Optional("entry_id"): str,
        vol.Required("key"): str,
    }
)
@websocket_api.async_response
async def ws_article(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Send an article with all its details."""
    await _async_answer(hass, connection, msg, lambda entry: _async_article(hass, entry, msg["key"]))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/article_save",
        vol.Optional("entry_id"): str,
        # Without key a new article is added, it needs a name
        vol.Optional("key"): str,
        **ARTICLE_FIELDS,
    }
)
@websocket_api.async_response
async def ws_article_save(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Add an article or change its details, send the article with all its details."""
    await _async_answer(hass, connection, msg, lambda entry: async_save_article(hass, entry, msg.get("key"), msg))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/article_delete",
        vol.Optional("entry_id"): str,
        vol.Required("key"): str,
    }
)
@websocket_api.async_response
async def ws_article_delete(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Remove the details and photos of an article, an article added by hand is removed completely."""
    await _async_answer(hass, connection, msg, lambda entry: async_delete_article(hass, entry, msg["key"]))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/barcode",
        vol.Optional("entry_id"): str,
        vol.Required("code"): str,
        # Ask Open Food Facts also for a known barcode
        vol.Optional("lookup", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_barcode(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Send the articles with a barcode and, for an unknown one, the product of Open Food Facts."""
    try:
        result = await async_barcode(hass, loaded_entry(hass, msg.get("entry_id")), msg["code"], msg["lookup"])
    except ArticleError as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/article_image",
        vol.Optional("entry_id"): str,
        vol.Required("key"): str,
        vol.Optional("kind", default="other"): vol.In(IMAGE_KINDS),
        # The photo as base64, the panel makes it smaller before
        vol.Required("data"): vol.All(str, vol.Length(max=(MAX_IMAGE_BYTES * 4) // 3 + 4)),
    }
)
@websocket_api.async_response
async def ws_article_image(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Add a photo to an article, send the article with all its details."""

    async def add(entry: LidlPlusConfigEntry | None) -> dict[str, Any]:
        await _async_article(hass, entry, msg["key"])
        try:
            content = base64.b64decode(msg["data"], validate=True)
        except (binascii.Error, ValueError) as err:
            raise ArticleError(INVALID_FORMAT, "Das Foto ist nicht lesbar") from err
        return await async_add_article_image(hass, entry, msg["key"], msg["kind"], content)

    await _async_answer(hass, connection, msg, add)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/article_image_delete",
        vol.Optional("entry_id"): str,
        vol.Required("key"): str,
        vol.Required("image_id"): str,
    }
)
@websocket_api.async_response
async def ws_article_image_delete(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Remove a photo of an article, send the article with all its details."""
    await _async_answer(
        hass, connection, msg, lambda entry: async_delete_article_image(hass, entry, msg["key"], msg["image_id"])
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/article_leaflet",
        vol.Optional("entry_id"): str,
        vol.Required("key"): str,
        **LEAFLET_FIELDS,
        vol.Optional("remove", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_article_leaflet(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Add an article to a page of a leaflet or remove it from it, send the article with all its details."""
    await _async_answer(
        hass,
        connection,
        msg,
        lambda entry: async_link_article_leaflet(hass, entry, msg["key"], msg, msg["remove"]),
    )


@callback
def async_setup_article_api(hass: HomeAssistant) -> None:
    """Register the websocket commands of the article database."""
    for command in (
        ws_articles,
        ws_article,
        ws_article_save,
        ws_article_delete,
        ws_barcode,
        ws_article_image,
        ws_article_image_delete,
        ws_article_leaflet,
    ):
        websocket_api.async_register_command(hass, command)
