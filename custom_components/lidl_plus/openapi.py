"""OpenAPI description of the REST API, AI assistants can turn it into tools."""

from __future__ import annotations

from typing import Any

from ._lidlplus.articles import FILTERS, IMAGE_KINDS, NUTRIENTS, NUTRITION_BASES, SORTS

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
_KEY = _path("key", "Key of the article from /articles, e.g. nr:0082446")
_ANSWER = "The answer is the article with all details, like /articles/{key}."
_ARTICLE_BODY: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "maxLength": 200,
            "description": "Name, needed for a new article. For an article of Lidl it replaces the name of Lidl, "
            "an empty name brings it back",
        },
        "brand": {"type": "string", "maxLength": 100},
        "package_size": {"type": "string", "maxLength": 100, "description": "e.g. 500 g or 6 x 1,5 l"},
        "barcodes": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 20,
            "description": "EAN/UPC barcodes, they replace the known ones; a barcode belongs to one article only",
        },
        "nutrition": {
            "type": "object",
            "properties": {nutrient: {"type": ["number", "null"], "minimum": 0} for nutrient in NUTRIENTS},
            "description": "Nutrition values per 100 g or 100 ml, energy in kJ and kcal, the others in grams. "
            "They replace all known values",
        },
        "nutrition_basis": {"type": "string", "enum": list(NUTRITION_BASES)},
        "ingredients": {"type": "string", "maxLength": 10000, "description": "Ingredients, also of non-food articles"},
        "notes": {"type": "string", "maxLength": 10000},
        "image": {
            "type": "string",
            "description": "https address of a picture of the article, e.g. of Open Food Facts",
        },
        "sources": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": 'Where values were taken from, e.g. {"nutrition": "Open Food Facts"}',
        },
    },
    "additionalProperties": False,
}

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
        "Leaflets (Prospekte) of the offer region of the store with name, category, offer days (start and end are "
        "local dates, both included), status, PDF and online link and the number of pages and products. By default "
        "the current and upcoming "
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
        "price, description, link). Food offers are no products, they are only mentioned in the text of their page: "
        "`offers` are the offers of the Lidl Plus app found on the pages, `articles` the articles of the article "
        "database added to a page by hand (`added`) or found by their name.",
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
        "Stores of the receipts (visits, spending, last visit), the stores whose offers are loaded and the offer "
        "region whose leaflets are shown (the weekly leaflets differ between the regions).",
        [_ENTRY],
    ),
    "/busy_times": (
        "getBusyTimes",
        "Busy hours of the store",
        "For the store of the offers: address, opening hours (special days like holidays in `special`), the "
        "number of receipts per weekday (0 = Monday) and hour (`own.hours`, when the user went shopping) and, if "
        "configured, the expected busyness in percent per weekday and hour from BestTime.app (`forecast.hours`) "
        "with `busyness_now` for the current hour. The best start for questions like 'when is the store quiet?'.",
        [_ENTRY],
    ),
    "/shopping_duration": (
        "getShoppingDuration",
        "Shopping duration",
        "How long the shopping took, measured with the location history of the persons (Home Assistant companion "
        "app) at the store: average, shortest and longest duration in minutes, the average per weekday "
        "(`by_weekday`, 0 = Monday), `average_checkout_minutes` (arrival until paying) and the last visit with "
        "arrival and departure. `recent_visits` lists the latest receipts with their visit; a receipt of /receipts "
        "has its `visit` as well (status ok, incomplete, not_seen or no_location).",
        [_ENTRY],
    ),
    "/articles": (
        "listArticles",
        "Article database",
        "Every article known: bought (receipts, with the Lidl article number), offered in the Lidl Plus app, shown "
        "in a leaflet (products of the online shop) or added by hand, with the details added in the panel: "
        "barcodes (EAN), package size, nutrition values, ingredients (also of non-food articles), notes and the "
        "number of photos. Keys are nr:<article number>, offer:<name>, web:<product number> or own:<id>. "
        f"{_PAGED}",
        [
            _ENTRY,
            _query("q", "Words that must all appear in name, brand, receipt names, barcode, ingredients or notes"),
            _query(
                "kind",
                "Only articles that were bought, offered, in leaflets, added by hand, with details, with nutrition "
                "values, without nutrition values, with ingredients, with photos or with a barcode",
                {"type": "string", "enum": list(FILTERS)},
            ),
            _query("sort", "Order of the articles", {"type": "string", "enum": list(SORTS), "default": "count-desc"}),
            _LIMIT,
            _OFFSET,
        ],
    ),
    "/barcodes/{code}": (
        "findBarcode",
        "Find a barcode",
        "The articles with a barcode (EAN/UPC) and, for an unknown barcode or with lookup=true, the product of Open "
        "Food Facts, Open Beauty Facts or Open Products Facts (`product` with name, brand, quantity, nutrition values "
        "per 100 g/ml, ingredients, Nutri-Score and picture) that can be taken for an article.",
        [
            _path("code", "The digits of the barcode"),
            _query("lookup", "Look the barcode up also if an article has it", {"type": "boolean"}),
            _ENTRY,
        ],
    ),
    "/articles/{key}": (
        "getArticle",
        "Single article",
        "An article with all details: purchase statistics and price history (`product`, if bought), the offers and "
        "leaflets it was part of, package size, nutrition values per 100 g or 100 ml (`nutrition`, "
        "`nutrition_basis`, energy in kJ and kcal, fat, saturated fat, carbohydrates, sugars, fiber, protein and "
        "salt in grams), ingredients, notes and photos (`images[].url`, loaded with the same access token).",
        [_path("key", "Key of the article from /articles, e.g. nr:0082446"), _ENTRY],
    ),
    "/images/{image_id}": (
        "getImage",
        "Photo of an article",
        "A photo of an article (JPEG, PNG or WebP), e.g. of the nutrition label or the list of ingredients.",
        [_path("image_id", "Id of the photo from images[] of an article")],
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
                    "enum": ["all", "receipts", "items", "products", "offers", "leaflets", "articles"],
                    "default": "all",
                },
            ),
            _query("format", "Format of a single table", {"type": "string", "enum": ["csv", "json"], "default": "csv"}),
        ],
    ),
}


def _json(schema: dict[str, Any]) -> dict[str, Any]:
    return {"required": True, "content": {"application/json": {"schema": schema}}}


_IMAGE_BODY = {
    "required": True,
    "content": {
        **{
            content_type: {"schema": {"type": "string", "format": "binary"}}
            for content_type in ("image/jpeg", "image/png", "image/webp")
        },
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "format": "binary"},
                    "kind": {"type": "string", "enum": list(IMAGE_KINDS)},
                },
                "required": ["file"],
            }
        },
    },
}
_LEAFLET_BODY = _json(
    {
        "type": "object",
        "properties": {
            "leaflet_id": {"type": "string", "description": "Leaflet id from /leaflets"},
            "page": {"type": "integer", "minimum": 1, "description": "Number of the page"},
        },
        "required": ["leaflet_id", "page"],
    }
)

# Changes of the article database: path, method, operationId, summary, description, parameters, request body
_CHANGES: list[tuple[str, str, str, str, str, list[dict[str, Any]], dict[str, Any] | None]] = [
    (
        "/articles",
        "post",
        "addArticle",
        "Add an article",
        "Add an article that is not known yet (e.g. from a leaflet without product data), with its details. "
        f"{_ANSWER}",
        [_ENTRY],
        _json({**_ARTICLE_BODY, "required": ["name"]}),
    ),
    (
        "/articles/{key}",
        "patch",
        "changeArticle",
        "Change an article",
        "Add or change the details of an article: only the given fields are changed, empty values remove them. "
        f"{_ANSWER}",
        [_KEY, _ENTRY],
        _json(_ARTICLE_BODY),
    ),
    (
        "/articles/{key}",
        "delete",
        "deleteArticleDetails",
        "Remove the details of an article",
        "Remove every detail added by hand (barcodes, nutrition values, photos, ...). An article added by hand is "
        "removed completely, the answer is then null.",
        [_KEY, _ENTRY],
        None,
    ),
    (
        "/articles/{key}/images",
        "post",
        "addArticleImage",
        "Add a photo to an article",
        "A photo (JPEG, PNG or WebP, at most 2.5 MB) as body, or as field `file` of a form. `kind` tells what it "
        f"shows: nutrition values, ingredients, the front or something else. {_ANSWER}",
        [_KEY, _query("kind", "What the photo shows", {"type": "string", "enum": list(IMAGE_KINDS)}), _ENTRY],
        _IMAGE_BODY,
    ),
    (
        "/articles/{key}/images/{image_id}",
        "delete",
        "deleteArticleImage",
        "Remove a photo of an article",
        _ANSWER,
        [_KEY, _path("image_id", "Id of the photo from images[] of the article"), _ENTRY],
        None,
    ),
    (
        "/articles/{key}/leaflets",
        "post",
        "addArticleToLeaflet",
        "Add an article to a leaflet page",
        "The article is printed on a page of a leaflet, but Lidl has no data about it. The leaflet lists the article "
        f"in `articles`. {_ANSWER}",
        [_KEY, _ENTRY],
        _LEAFLET_BODY,
    ),
    (
        "/articles/{key}/leaflets/{leaflet_id}",
        "delete",
        "removeArticleFromLeaflet",
        "Remove an article from a leaflet",
        f"From one page (query parameter page) or from all pages it was added to. {_ANSWER}",
        [
            _KEY,
            _path("leaflet_id", "Leaflet id from /leaflets"),
            _query("page", "Number of the page", {"type": "integer", "minimum": 1}),
            _ENTRY,
        ],
        None,
    ),
]


def _operation(
    operation_id: str,
    summary: str,
    description: str,
    parameters: list[dict[str, Any]],
    body: dict[str, Any] | None = None,
) -> dict:
    if operation_id == "exportData":
        content: dict[str, Any] = {"application/zip": {}, "text/csv": {}, "application/json": {}}
    elif operation_id == "getImage":
        content = {"image/jpeg": {}, "image/png": {}, "image/webp": {}}
    else:
        content = {"application/json": {"schema": {"type": ["object", "array"]}}}
    created = operation_id in ("addArticle", "addArticleImage")
    responses: dict[str, Any] = {"201" if created else "200": {"description": summary, "content": content}}
    if body or operation_id in ("listOffers", "listLeaflets", "search", "exportData", "listArticles", "findBarcode"):
        responses["400"] = {"description": "Invalid or missing parameter"}
    if operation_id != "listAccounts":
        responses["404"] = {"description": "No loaded Lidl Plus account, or the requested entry was not found"}
    if operation_id in ("addArticle", "changeArticle"):
        responses["409"] = {"description": "A barcode belongs to another article already"}
    if operation_id == "addArticleImage":
        responses["409"] = {"description": "The article has 20 photos already"}
        responses["413"] = {"description": "The photo is larger than 2.5 MB"}
    operation = {
        "operationId": operation_id,
        "summary": summary,
        "description": description,
        "parameters": parameters,
        "responses": responses,
    }
    if body:
        operation["requestBody"] = body
    return operation


def build_openapi() -> dict[str, Any]:
    """The OpenAPI 3.1 description of all endpoints"""
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Lidl Plus",
            "version": "1.0.0",
            "description": "Receipts, articles, spending, offers, leaflets and coupons of the Lidl Plus accounts in "
            "Home Assistant, and the article database with barcodes, nutrition values, ingredients and photos, which "
            "can be changed as well. Authenticate with a long-lived access token of Home Assistant: "
            "`Authorization: Bearer <token>`. Amounts are in euros, dates are ISO 8601.",
        },
        "components": {"securitySchemes": {"homeAssistant": {"type": "http", "scheme": "bearer"}}},
        "security": [{"homeAssistant": []}],
        "paths": _paths(),
    }


def _paths() -> dict[str, dict[str, Any]]:
    paths: dict[str, dict[str, Any]] = {}
    for path, operation in _OPERATIONS.items():
        paths.setdefault(f"{API_PATH}{path}", {})["get"] = _operation(*operation)
    for path, method, *operation in _CHANGES:
        paths.setdefault(f"{API_PATH}{path}", {})[method] = _operation(*operation)
    return paths
