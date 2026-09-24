"""
REST API for AI assistants and other programs

Every endpoint needs a Home Assistant access token ("Authorization: Bearer <long-lived token>").
Tools that read OpenAPI find the description of all endpoints at /api/lidl_plus/openapi.json.
The API only reads: nothing can be changed or activated through it.
"""

from __future__ import annotations

from collections import defaultdict
from http import HTTPStatus
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from ._lidlplus import analytics, export
from .const import (
    DOMAIN,
    KEY_AVERAGE_BASKET,
    KEY_CATEGORY_FOOD_SPENDING,
    KEY_CATEGORY_NONFOOD_SPENDING,
    KEY_COUPONS,
    KEY_COUPONS_ACTIVATED,
    KEY_COUPONS_AVAILABLE,
    KEY_CURRENT_MONTH_SPENDING,
    KEY_FREQUENTLY_BOUGHT,
    KEY_LAST_ERROR,
    KEY_LAST_SYNC,
    KEY_OFFER_STORES,
    KEY_PRICE_CHANGES,
    KEY_PRODUCTS,
    KEY_RECEIPTS,
    KEY_RESTOCK_SUGGESTIONS,
    KEY_SAVINGS_MONTH,
    KEY_SAVINGS_TOTAL,
    KEY_SHOPPING_FREQUENCY,
    KEY_SPENDING_BY_STORE,
    KEY_STORES,
    KEY_TOTAL_SPENT,
    KEY_TOTAL_TICKETS,
)
from .coordinator import LidlPlusConfigEntry, active_leaflets, active_offers, search_data
from .openapi import API_PATH, build_openapi

_PRODUCT_SORTS = {
    "purchases": (lambda product: product["purchase_count"], True),
    "spent": (lambda product: product["total_spent"], True),
    "savings": (lambda product: product["savings"], True),
    "last": (lambda product: product["last_date"], True),
    "name": (lambda product: product["name"].lower(), False),
}
_EXPORT_TYPES = {"csv": "text/csv", "json": "application/json", "zip": "application/zip"}
# "active" are the current and upcoming ones
_STATUSES = ("active", "current", "upcoming", "expired", "all")


def _flag(request: web.Request, name: str) -> bool:
    return request.query.get(name, "").lower() in ("1", "true", "yes")


def _number(request: web.Request, name: str, default: int, maximum: int) -> int:
    try:
        return max(0, min(int(request.query.get(name, default)), maximum))
    except ValueError:
        return default


def _page(request: web.Request, rows: list, default_limit: int, max_limit: int) -> dict[str, Any]:
    """Slice of the rows selected by limit and offset, with the total number of rows"""
    limit = _number(request, "limit", default_limit, max_limit)
    offset = _number(request, "offset", 0, len(rows))
    return {"total": len(rows), "offset": offset, "limit": limit, "results": rows[offset : offset + limit]}


def _in_period(request: web.Request, rows: list[dict], key: str = "date") -> list[dict]:
    """Rows between the query parameters from and to (YYYY-MM-DD, both included)"""
    start, end = request.query.get("from"), request.query.get("to")
    return [
        row
        for row in rows
        if (not start or (row.get(key) or "")[:10] >= start) and (not end or (row.get(key) or "")[:10] <= end)
    ]


def _receipt_items(receipts: list[dict]) -> list[dict]:
    return [
        {**item, "date": receipt["date"], "ticket_id": receipt["id"]}
        for receipt in receipts
        for item in receipt["items"]
    ]


def _with_status(rows: list[dict], status: str) -> list[dict]:
    return rows if status in ("active", "all") else [row for row in rows if row["status"] == status]


def _from_archive(status: str) -> bool:
    """The coordinator only keeps the current and upcoming offers and leaflets, the others are in the cache"""
    return status in ("expired", "all")


class LidlPlusView(HomeAssistantView):
    """Base of the API views: selects the account of the query parameter entry_id"""

    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def _entry(self, request: web.Request) -> LidlPlusConfigEntry | None:
        entries = [entry for entry in self.hass.config_entries.async_loaded_entries(DOMAIN) if entry.runtime_data.data]
        entry_id = request.query.get("entry_id")
        return next((entry for entry in entries if entry_id in (None, entry.entry_id)), None)

    def _not_found(self, message: str = "No loaded Lidl Plus account") -> web.Response:
        return self.json_message(message, HTTPStatus.NOT_FOUND)


class IndexView(LidlPlusView):
    """Overview of the endpoints"""

    url = API_PATH
    name = "api:lidl_plus"

    async def get(self, request: web.Request) -> web.Response:
        spec = build_openapi()
        return self.json(
            {
                "name": "Lidl Plus",
                "openapi": f"{API_PATH}/openapi.json",
                "endpoints": {path: operation["get"]["summary"] for path, operation in spec["paths"].items()},
            }
        )


class OpenApiView(LidlPlusView):
    """OpenAPI description, e.g. for AI assistants that turn it into tools"""

    url = f"{API_PATH}/openapi.json"
    name = "api:lidl_plus:openapi"

    async def get(self, request: web.Request) -> web.Response:
        return self.json(build_openapi())


class AccountsView(LidlPlusView):
    """Configured Lidl Plus accounts"""

    url = f"{API_PATH}/accounts"
    name = "api:lidl_plus:accounts"

    async def get(self, request: web.Request) -> web.Response:
        return self.json(
            [
                {
                    "entry_id": entry.entry_id,
                    "title": entry.title,
                    "last_sync": entry.runtime_data.data.get(KEY_LAST_SYNC),
                    "last_error": entry.runtime_data.data.get(KEY_LAST_ERROR),
                    "receipts": entry.runtime_data.data.get(KEY_TOTAL_TICKETS),
                }
                for entry in self.hass.config_entries.async_loaded_entries(DOMAIN)
                if entry.runtime_data.data
            ]
        )


class SummaryView(LidlPlusView):
    """Key figures of an account"""

    url = f"{API_PATH}/summary"
    name = "api:lidl_plus:summary"

    async def get(self, request: web.Request) -> web.Response:
        if (entry := self._entry(request)) is None:
            return self._not_found()
        data = entry.runtime_data.data
        offers = active_offers(data)
        return self.json(
            {
                "entry_id": entry.entry_id,
                "last_sync": data[KEY_LAST_SYNC],
                "last_error": data[KEY_LAST_ERROR],
                "receipts": data[KEY_TOTAL_TICKETS],
                "total_spent": data[KEY_TOTAL_SPENT],
                "current_month_spent": data[KEY_CURRENT_MONTH_SPENDING],
                "average_basket": data[KEY_AVERAGE_BASKET],
                "shopping_frequency_days": data[KEY_SHOPPING_FREQUENCY],
                "food_spent": data[KEY_CATEGORY_FOOD_SPENDING],
                "non_food_spent": data[KEY_CATEGORY_NONFOOD_SPENDING],
                "savings_total": data[KEY_SAVINGS_TOTAL],
                "savings_current_month": data[KEY_SAVINGS_MONTH],
                "top_stores": dict(list(data[KEY_SPENDING_BY_STORE].items())[:5]),
                "most_bought": data[KEY_FREQUENTLY_BOUGHT],
                "price_changes": data[KEY_PRICE_CHANGES],
                "restock_suggestions": data[KEY_RESTOCK_SUGGESTIONS],
                "coupons_available": data[KEY_COUPONS_AVAILABLE],
                "coupons_activated": data[KEY_COUPONS_ACTIVATED],
                "offers_current": sum(1 for offer in offers if offer["status"] == "current"),
                "offers_upcoming": sum(1 for offer in offers if offer["status"] == "upcoming"),
                "offers_for_you": sum(1 for offer in offers if offer["bought_products"]),
            }
        )


class ReceiptsView(LidlPlusView):
    """Receipts, newest first"""

    url = f"{API_PATH}/receipts"
    name = "api:lidl_plus:receipts"

    async def get(self, request: web.Request) -> web.Response:
        if (entry := self._entry(request)) is None:
            return self._not_found()
        receipts = _in_period(request, entry.runtime_data.data[KEY_RECEIPTS])
        if store := request.query.get("store", "").lower():
            receipts = [
                receipt
                for receipt in receipts
                if store in receipt["store"].lower() or store == receipt["store_id"].lower()
            ]
        if search := request.query.get("search", "").lower():
            receipts = [
                receipt
                for receipt in receipts
                if any(search in (item.get("name") or "").lower() for item in receipt["items"])
            ]
        if not _flag(request, "items"):
            receipts = [
                {
                    **{key: value for key, value in receipt.items() if key != "items"},
                    "item_count": len(receipt["items"]),
                }
                for receipt in receipts
            ]
        return self.json(_page(request, receipts, 20, 500))


class ReceiptView(LidlPlusView):
    """A single receipt with all articles"""

    url = f"{API_PATH}/receipts/{{receipt_id}}"
    name = "api:lidl_plus:receipt"

    async def get(self, request: web.Request, receipt_id: str) -> web.Response:
        if (entry := self._entry(request)) is None:
            return self._not_found()
        for receipt in entry.runtime_data.data[KEY_RECEIPTS]:
            if receipt["id"] == receipt_id:
                return self.json(receipt)
        return self._not_found("Receipt not found")


class ProductsView(LidlPlusView):
    """Articles that were bought, with statistics and price history"""

    url = f"{API_PATH}/products"
    name = "api:lidl_plus:products"

    async def get(self, request: web.Request) -> web.Response:
        if (entry := self._entry(request)) is None:
            return self._not_found()
        products = entry.runtime_data.data[KEY_PRODUCTS]
        if search := request.query.get("search", "").lower():
            products = [product for product in products if search in product["name"].lower()]
        key, reverse = _PRODUCT_SORTS.get(request.query.get("sort", "purchases"), _PRODUCT_SORTS["purchases"])
        return self.json(_page(request, sorted(products, key=key, reverse=reverse), 50, 1000))


class ProductView(LidlPlusView):
    """A single article with all purchases and all offers it was part of"""

    url = f"{API_PATH}/products/{{product_id}}"
    name = "api:lidl_plus:product"

    async def get(self, request: web.Request, product_id: str) -> web.Response:
        if (entry := self._entry(request)) is None:
            return self._not_found()
        data = entry.runtime_data.data
        product = next((product for product in data[KEY_PRODUCTS] if product["id"] == product_id), None)
        if product is None:
            return self._not_found("Product not found")
        purchases = [
            {**item, "receipt_id": receipt["id"], "date": receipt["date"], "store": receipt["store"]}
            for receipt in data[KEY_RECEIPTS]
            for item in receipt["items"]
            if item["id"] == product_id
        ]
        offers = await entry.runtime_data.async_all_offers()
        return self.json(
            {
                **product,
                "purchases": purchases,
                "offers": [offer for offer in offers if product_id in offer["product_ids"]],
            }
        )


class SpendingView(LidlPlusView):
    """Spending and savings of a period by month, store and category"""

    url = f"{API_PATH}/spending"
    name = "api:lidl_plus:spending"

    async def get(self, request: web.Request) -> web.Response:
        if (entry := self._entry(request)) is None:
            return self._not_found()
        receipts = _in_period(request, entry.runtime_data.data[KEY_RECEIPTS])
        items = _receipt_items(receipts)
        by_month: dict[str, float] = defaultdict(float)
        by_store: dict[str, float] = defaultdict(float)
        for receipt in receipts:
            by_month[receipt["date"][:7]] += receipt["total"]
            by_store[receipt["store"] or "Unknown"] += receipt["total"]
        categories = analytics.category_spending(items)
        return self.json(
            {
                "from": request.query.get("from"),
                "to": request.query.get("to"),
                "receipts": len(receipts),
                "total": round(sum(receipt["total"] for receipt in receipts), 2),
                "average_basket": (
                    round(sum(receipt["total"] for receipt in receipts) / len(receipts), 2) if receipts else 0.0
                ),
                "by_month": {month: round(total, 2) for month, total in sorted(by_month.items())},
                "by_store": {
                    store: round(total, 2)
                    for store, total in sorted(by_store.items(), key=lambda entry: entry[1], reverse=True)
                },
                "food": categories["reduced"],
                "non_food": categories["standard"],
                "savings": analytics.total_savings(items),
                "savings_by_month": analytics.savings_by_month(items),
            }
        )


class OffersView(LidlPlusView):
    """Offers of the chosen stores: current, upcoming or all seen so far"""

    url = f"{API_PATH}/offers"
    name = "api:lidl_plus:offers"

    async def get(self, request: web.Request) -> web.Response:
        if (entry := self._entry(request)) is None:
            return self._not_found()
        if (status := request.query.get("status", "active")) not in _STATUSES:
            return self.json_message(f"status must be one of {', '.join(_STATUSES)}", HTTPStatus.BAD_REQUEST)
        if _from_archive(status):
            offers = await entry.runtime_data.async_all_offers()
        else:
            offers = active_offers(entry.runtime_data.data)
        offers = _with_status(offers, status)
        if search := request.query.get("search", "").lower():
            offers = [offer for offer in offers if search in f"{offer['brand']} {offer['title']}".lower()]
        if _flag(request, "bought"):
            offers = [offer for offer in offers if offer["bought_products"]]
        if store := request.query.get("store"):
            offers = [offer for offer in offers if store in offer["stores"]]
        return self.json(_page(request, offers, 50, 2000))


class LeafletsView(LidlPlusView):
    """Leaflets without pages and products: current and upcoming ones, or all seen so far"""

    url = f"{API_PATH}/leaflets"
    name = "api:lidl_plus:leaflets"

    async def get(self, request: web.Request) -> web.Response:
        if (entry := self._entry(request)) is None:
            return self._not_found()
        if (status := request.query.get("status", "active")) not in _STATUSES:
            return self.json_message(f"status must be one of {', '.join(_STATUSES)}", HTTPStatus.BAD_REQUEST)
        if _from_archive(status):
            leaflets = await entry.runtime_data.async_all_leaflets()
        else:
            leaflets = active_leaflets(entry.runtime_data.data)
        leaflets = _with_status(leaflets, status)
        if category := request.query.get("category", "").lower():
            leaflets = [leaflet for leaflet in leaflets if category in leaflet["category"].lower()]
        return self.json(_page(request, [analytics.leaflet_summary(leaflet) for leaflet in leaflets], 50, 1000))


class LeafletView(LidlPlusView):
    """A single leaflet with the text of its pages and its products"""

    url = f"{API_PATH}/leaflets/{{leaflet_id}}"
    name = "api:lidl_plus:leaflet"

    async def get(self, request: web.Request, leaflet_id: str) -> web.Response:
        if (entry := self._entry(request)) is None:
            return self._not_found()
        if (leaflet := await entry.runtime_data.async_leaflet(leaflet_id)) is None:
            return self._not_found("Leaflet not found")
        return self.json(leaflet)


class SearchView(LidlPlusView):
    """Articles bought before, offers and leaflets that mention every word of the query"""

    url = f"{API_PATH}/search"
    name = "api:lidl_plus:search"

    async def get(self, request: web.Request) -> web.Response:
        if not (query := request.query.get("q", "").strip()):
            return self.json_message("The query parameter q is missing", HTTPStatus.BAD_REQUEST)
        if (entry := self._entry(request)) is None:
            return self._not_found()
        return self.json({"query": query, **search_data(entry.runtime_data.data, query)})


class CouponsView(LidlPlusView):
    """Lidl Plus coupons of the account"""

    url = f"{API_PATH}/coupons"
    name = "api:lidl_plus:coupons"

    async def get(self, request: web.Request) -> web.Response:
        if (entry := self._entry(request)) is None:
            return self._not_found()
        return self.json(
            [
                {**coupon, "activated": analytics.coupon_is_activated(coupon)}
                for coupon in entry.runtime_data.data[KEY_COUPONS]
            ]
        )


class StoresView(LidlPlusView):
    """Stores of the receipts and the stores whose offers are loaded"""

    url = f"{API_PATH}/stores"
    name = "api:lidl_plus:stores"

    async def get(self, request: web.Request) -> web.Response:
        if (entry := self._entry(request)) is None:
            return self._not_found()
        data = entry.runtime_data.data
        return self.json({"visited": data[KEY_STORES], "offer_stores": data[KEY_OFFER_STORES]})


class ExportView(LidlPlusView):
    """Download of receipts, articles, products, offers or leaflets as CSV/JSON, or everything as ZIP"""

    url = f"{API_PATH}/export"
    name = "api:lidl_plus:export"

    async def get(self, request: web.Request) -> web.Response:
        if (entry := self._entry(request)) is None:
            return self._not_found()
        dataset = request.query.get("dataset", "all")
        file_format = "zip" if dataset == "all" else request.query.get("format", "csv")
        if dataset not in (*export.DATASETS, "all") or (dataset != "all" and file_format not in ("csv", "json")):
            return self.json_message(
                f"dataset must be one of {', '.join((*export.DATASETS, 'all'))}, format csv or json",
                HTTPStatus.BAD_REQUEST,
            )
        body = await entry.runtime_data.async_export(dataset, file_format)
        filename = f"lidl_plus_{dataset}_{dt_util.now():%Y-%m-%d}.{file_format}"
        return web.Response(
            body=body,
            content_type=_EXPORT_TYPES[file_format],
            charset="utf-8" if file_format != "zip" else None,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


@callback
def async_setup_rest_api(hass: HomeAssistant) -> None:
    """Register the endpoints of the REST API"""
    for view in (
        IndexView,
        OpenApiView,
        AccountsView,
        SummaryView,
        ReceiptsView,
        ReceiptView,
        ProductsView,
        ProductView,
        SpendingView,
        OffersView,
        LeafletsView,
        LeafletView,
        SearchView,
        CouponsView,
        StoresView,
        ExportView,
    ):
        hass.http.register_view(view(hass))
