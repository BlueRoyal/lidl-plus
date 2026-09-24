"""OpenAPI description of the REST API, AI assistants can turn it into tools."""

from __future__ import annotations

from typing import Any

API_PATH = "/api/lidl_plus"


def _query(name: str, description: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "in": "query",
        "required": False,
        "description": description,
        "schema": schema or {"type": "string"},
    }


def _path(name: str, description: str) -> dict[str, Any]:
    return {"name": name, "in": "path", "required": True, "description": description, "schema": {"type": "string"}}


_ENTRY = _query("entry_id", "Lidl Plus account (see /accounts), the first account if not given")
_FROM = _query("from", "First day of the period (YYYY-MM-DD)", {"type": "string", "format": "date"})
_TO = _query("to", "Last day of the period (YYYY-MM-DD), included", {"type": "string", "format": "date"})
_LIMIT = _query("limit", "Maximum number of results", {"type": "integer", "minimum": 0})
_OFFSET = _query("offset", "Number of results to skip, for paging", {"type": "integer", "minimum": 0})
_PAGED = "The response contains `total` (number of all matching entries), `offset`, `limit` and `results`."

# path: (operationId, summary, description, parameters)
_OPERATIONS: dict[str, tuple[str, str, str, list[dict[str, Any]]]] = {
    "/accounts": (
        "listAccounts",
        "Lidl Plus accounts",
        "The configured Lidl Plus accounts with entry_id, title, time of the last sync and number of receipts.",
        [],
    ),
    "/summary": (
        "getSummary",
        "Key figures",
        "Total and current month spending, average basket, days between shopping trips, food and non-food "
        "spending, savings, most bought articles, price changes, restock suggestions and the number of coupons "
        "and offers. Amounts are in euros.",
        [_ENTRY],
    ),
    "/receipts": (
        "listReceipts",
        "Receipts",
        "Receipts (newest first) with date, store, total, savings, payments and deposit returns. "
        "With items=true every receipt contains its articles (name, quantity, unit, unit_price, total, "
        f"discounts, tax_rate). {_PAGED}",
        [
            _ENTRY,
            _FROM,
            _TO,
            _query("store", "Store name (part of it) or store id like DE1234"),
            _query("search", "Only receipts with an article whose name contains this text"),
            _query("items", "Include the articles of every receipt", {"type": "boolean"}),
            _LIMIT,
            _OFFSET,
        ],
    ),
    "/receipts/{receipt_id}": (
        "getReceipt",
        "Single receipt",
        "A receipt with all articles, discounts (Lidl Plus coupons, price advantages), deposit returns and payments.",
        [_path("receipt_id", "Receipt id from /receipts"), _ENTRY],
    ),
    "/products": (
        "listProducts",
        "Articles bought",
        "Every article bought so far with number of purchases, quantity, unit (kg for weighed articles), "
        "total spent, savings, average and last price, first and last purchase and the price history. "
        f"Article names are abbreviated like on the receipt, e.g. 'Traub dunk 500g'. {_PAGED}",
        [
            _ENTRY,
            _query("search", "Part of the article name, e.g. 'milch'"),
            _query(
                "sort",
                "Order of the articles",
                {"type": "string", "enum": ["purchases", "spent", "savings", "last", "name"], "default": "purchases"},
            ),
            _LIMIT,
            _OFFSET,
        ],
    ),
    "/products/{product_id}": (
        "getProduct",
        "Single article",
        "An article with its statistics, every single purchase and all offers of the article seen so far.",
        [_path("product_id", "Article id from /products or a receipt"), _ENTRY],
    ),
    "/spending": (
        "getSpending",
        "Spending of a period",
        "Spending and savings of a period by month and store, food (reduced VAT rate) and non-food spending.",
        [_ENTRY, _FROM, _TO],
    ),
    "/offers": (
        "listOffers",
        "Offers of the stores",
        "Offers of the chosen stores from the Lidl Plus app with price, regular price, discount, validity and "
        "`bought_products`: the articles of the offer that were bought before. By default the current and "
        f"upcoming offers, status=all also returns every offer seen before. {_PAGED}",
        [
            _ENTRY,
            _query(
                "status",
                "Which offers",
                {"type": "string", "enum": ["active", "current", "upcoming", "expired", "all"], "default": "active"},
            ),
            _query("search", "Part of the title or brand"),
            _query("bought", "Only offers of articles that were bought before", {"type": "boolean"}),
            _query("store", "Store id like DE1234"),
            _LIMIT,
            _OFFSET,
        ],
    ),
    "/leaflets": (
        "listLeaflets",
        "Leaflets",
        "Leaflets (Prospekte) with name, category, offer days (start and end are local dates, both included), "
        "status, PDF and online link and the number of pages and products. By default the current and upcoming "
        f"leaflets, status=all also returns the ones that ended. {_PAGED}",
        [
            _ENTRY,
            _query(
                "status",
                "Which leaflets",
                {"type": "string", "enum": ["active", "current", "upcoming", "expired", "all"], "default": "active"},
            ),
            _query("category", "Part of the category, e.g. 'Filial-Angebote'"),
            _LIMIT,
            _OFFSET,
        ],
    ),
    "/leaflets/{leaflet_id}": (
        "getLeaflet",
        "Single leaflet",
        "A leaflet with every page (number, printed words, description, image) and its products (title, brand, "
        "price, description, link). Food offers are no products, they are only mentioned in the text of their page.",
        [_path("leaflet_id", "Leaflet id from /leaflets"), _ENTRY],
    ),
    "/search": (
        "search",
        "Search everything",
        "Articles bought before, offers of the stores and leaflet pages and products that contain every word of "
        "the query. The best start for questions like 'is coffee on offer next week?'.",
        [
            {
                "name": "q",
                "in": "query",
                "required": True,
                "description": "Words to search",
                "schema": {"type": "string"},
            },
            _ENTRY,
        ],
    ),
    "/coupons": (
        "listCoupons",
        "Coupons",
        "Lidl Plus coupons of the account with title, validity and whether they are activated.",
        [_ENTRY],
    ),
    "/stores": (
        "listStores",
        "Stores",
        "Stores of the receipts (visits, spending, last visit) and the stores whose offers are loaded.",
        [_ENTRY],
    ),
    "/export": (
        "exportData",
        "Export",
        "Download as file: dataset=all is a ZIP file with every table as CSV and all raw data, a single table can "
        "also be downloaded as CSV (semicolon, decimal comma) or JSON.",
        [
            _ENTRY,
            _query(
                "dataset",
                "Table to export",
                {
                    "type": "string",
                    "enum": ["all", "receipts", "items", "products", "offers", "leaflets"],
                    "default": "all",
                },
            ),
            _query("format", "Format of a single table", {"type": "string", "enum": ["csv", "json"], "default": "csv"}),
        ],
    ),
}


def _operation(operation_id: str, summary: str, description: str, parameters: list[dict[str, Any]]) -> dict:
    content = (
        {"application/zip": {}, "text/csv": {}, "application/json": {}}
        if operation_id == "exportData"
        else {"application/json": {"schema": {"type": ["object", "array"]}}}
    )
    responses: dict[str, Any] = {"200": {"description": summary, "content": content}}
    if operation_id in ("listOffers", "listLeaflets", "search", "exportData"):
        responses["400"] = {"description": "Invalid or missing parameter"}
    if operation_id != "listAccounts":
        responses["404"] = {"description": "No loaded Lidl Plus account, or the requested entry was not found"}
    return {
        "get": {
            "operationId": operation_id,
            "summary": summary,
            "description": description,
            "parameters": parameters,
            "responses": responses,
        }
    }


def build_openapi() -> dict[str, Any]:
    """The OpenAPI 3.1 description of all endpoints"""
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Lidl Plus",
            "version": "1.0.0",
            "description": "Receipts, articles, spending, offers, leaflets and coupons of the Lidl Plus accounts in "
            "Home Assistant. Authenticate with a long-lived access token of Home Assistant: "
            "`Authorization: Bearer <token>`. Amounts are in euros, dates are ISO 8601.",
        },
        "components": {"securitySchemes": {"homeAssistant": {"type": "http", "scheme": "bearer"}}},
        "security": [{"homeAssistant": []}],
        "paths": {f"{API_PATH}{path}": _operation(*operation) for path, operation in _OPERATIONS.items()},
    }
