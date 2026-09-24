"""
Export of receipts, articles, products, offers and leaflets

CSV files are made for spreadsheets with German settings (semicolon, decimal comma, UTF-8 with BOM),
JSON keeps the values as they are. The ZIP file contains all tables and the complete cache.
"""

import csv
import io
import json
import zipfile

from . import analytics

DATASETS = ("receipts", "items", "products", "offers", "leaflets")
FORMATS = ("csv", "json", "zip")

RECEIPT_COLUMNS = [
    "id",
    "date",
    "store_id",
    "store_name",
    "store_address",
    "store_postal_code",
    "store_locality",
    "total",
    "articles",
    "savings",
    "deposit_returned",
    "payment",
    "till",
    "receipt_number",
    "lidl_plus_points",
]
ITEM_COLUMNS = [
    "receipt_id",
    "date",
    "store_id",
    "store_name",
    "article_id",
    "name",
    "quantity",
    "unit",
    "unit_price",
    "total",
    "discount",
    "paid",
    "discounts",
    "tax_type",
    "tax_rate",
    "deposit",
]
PRODUCT_COLUMNS = [
    "article_id",
    "name",
    "purchase_count",
    "receipts",
    "total_quantity",
    "unit",
    "total_spent",
    "avg_price",
    "last_price",
    "savings",
    "tax_rate",
    "first_date",
    "last_date",
    "last_store",
]
OFFER_COLUMNS = [
    "id",
    "title",
    "brand",
    "type",
    "status",
    "start",
    "end",
    "price",
    "regular_price",
    "price_text",
    "discount",
    "price_per_unit",
    "packaging",
    "product_ids",
    "bought_products",
    "stores",
    "first_seen",
    "last_seen",
    "image",
]
LEAFLET_COLUMNS = [
    "leaflet_id",
    "leaflet_name",
    "leaflet_title",
    "category",
    "start",
    "end",
    "status",
    "product_id",
    "title",
    "brand",
    "price",
    "product_category",
    "description",
    "url",
    "pdf",
]


def receipt_rows(tickets):
    """One row per receipt, newest first"""
    rows = []
    for ticket in sorted(tickets, key=lambda entry: entry.get("date") or "", reverse=True):
        store = ticket.get("store") or {}
        receipt = ticket.get("_receipt") or {}
        items = ticket.get("_items") or []
        info = receipt.get("info") or {}
        rows.append(
            {
                "id": ticket.get("id"),
                "date": ticket.get("date"),
                "store_id": store.get("id"),
                "store_name": store.get("name"),
                "store_address": store.get("address"),
                "store_postal_code": store.get("postalCode"),
                "store_locality": store.get("locality"),
                "total": analytics.to_float(ticket.get("totalAmount")),
                "articles": len(items),
                "savings": analytics.total_savings(items),
                "deposit_returned": round(-sum(entry["amount"] for entry in receipt.get("deposit_returns") or []), 2),
                "payment": ", ".join(payment["method"] for payment in receipt.get("payments") or []),
                "till": info.get("till"),
                "receipt_number": info.get("sequence_number"),
                "lidl_plus_points": (ticket.get("collectingModel") or {}).get("points"),
            }
        )
    return rows


def item_rows(tickets):
    """One row per article line of all receipts, newest receipt first"""
    rows = []
    for ticket in sorted(tickets, key=lambda entry: entry.get("date") or "", reverse=True):
        store = ticket.get("store") or {}
        for item in ticket.get("_items") or []:
            rows.append(
                {
                    "receipt_id": ticket.get("id"),
                    "date": ticket.get("date"),
                    "store_id": store.get("id"),
                    "store_name": store.get("name"),
                    "article_id": item.get("id"),
                    "name": item.get("name"),
                    "quantity": item.get("quantity"),
                    "unit": item.get("unit") or "",
                    "unit_price": analytics.to_float(item.get("unit_price")),
                    "total": item.get("total"),
                    "discount": item.get("discount") or 0.0,
                    "paid": analytics.item_total(item),
                    "discounts": ", ".join(
                        f"{discount['text']} {discount['amount']:.2f}".replace(".", ",")
                        for discount in item.get("discounts") or []
                    ),
                    "tax_type": item.get("tax_type"),
                    "tax_rate": item.get("tax_rate"),
                    "deposit": bool(item.get("is_deposit")),
                }
            )
    return rows


def product_rows(tickets):
    """One row per article that was bought, most frequently bought first"""
    return [
        {**product, "article_id": product["id"]}
        for product in analytics.product_summary(analytics.ticket_items(tickets))
    ]


def offer_rows(offers):
    """One row per offer (see analytics.archived_offers), newest first"""
    rows = []
    for offer in sorted(offers, key=lambda entry: entry.get("start") or "", reverse=True):
        rows.append(
            {
                **offer,
                "product_ids": ", ".join(offer.get("product_ids") or []),
                "bought_products": ", ".join(product["name"] for product in offer.get("bought_products") or []),
                "stores": ", ".join(offer.get("stores") or []),
            }
        )
    return rows


def leaflet_rows(leaflets):
    """One row per product of a leaflet (see analytics.archived_leaflets), newest leaflet first"""
    rows = []
    for leaflet in sorted(leaflets, key=lambda entry: entry.get("start") or "", reverse=True):
        base = {
            "leaflet_id": leaflet.get("id"),
            "leaflet_name": leaflet.get("name"),
            "leaflet_title": leaflet.get("title"),
            "category": leaflet.get("category"),
            "start": leaflet.get("start"),
            "end": leaflet.get("end"),
            "status": leaflet.get("status"),
            "pdf": leaflet.get("pdf"),
        }
        for product in leaflet.get("products") or []:
            rows.append(
                {
                    **base,
                    "product_id": product["id"],
                    "title": product["title"],
                    "brand": product["brand"],
                    "price": product["price"],
                    "product_category": product["category"],
                    "description": product["description"],
                    "url": product["url"],
                }
            )
    return rows


def _csv_value(value):
    if isinstance(value, bool):
        return "ja" if value else "nein"
    if isinstance(value, float):
        return f"{value:.10g}".replace(".", ",")
    if value is None:
        return ""
    return value


def to_csv(rows, columns):
    """CSV for spreadsheets with German settings: semicolon, decimal comma, UTF-8 with BOM"""
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_csv_value(row.get(column)) for column in columns])
    return "﻿" + output.getvalue()


def dataset(name, tickets, offers, leaflets=()):
    """Rows and columns of a dataset: "receipts", "items", "products", "offers" or "leaflets" """
    if name == "receipts":
        return receipt_rows(tickets), RECEIPT_COLUMNS
    if name == "items":
        return item_rows(tickets), ITEM_COLUMNS
    if name == "products":
        return product_rows(tickets), PRODUCT_COLUMNS
    if name == "offers":
        return offer_rows(offers), OFFER_COLUMNS
    if name == "leaflets":
        return leaflet_rows(leaflets), LEAFLET_COLUMNS
    raise ValueError(f"Unknown dataset {name}, use one of {', '.join(DATASETS)}")


def export_file(cache, name="all", file_format="csv", today=None):
    """
    Export of the cache content (see LidlPlusApi.cached_data) as bytes.

    name is one of DATASETS, exported as CSV or JSON, or "all": a ZIP file with every table and the complete cache.
    today is the local date for the status of the leaflets.
    """
    if name not in (*DATASETS, "all") or file_format not in FORMATS:
        raise ValueError(f"Unknown export {name} as {file_format}, use one of {', '.join((*DATASETS, 'all'))}")
    tickets = list((cache.get("tickets") or {}).values())
    items = analytics.ticket_items(tickets)
    offers = analytics.mark_bought_offers(analytics.archived_offers(cache.get("offers")), items)
    leaflets = analytics.archived_leaflets(cache.get("leaflets"), today)
    if name == "all" or file_format == "zip":
        return to_zip(tickets, offers, cache, leaflets)
    rows, columns = dataset(name, tickets, offers, leaflets)
    if file_format == "json":
        return json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")
    return to_csv(rows, columns).encode("utf-8")


def to_zip(tickets, offers, cache, leaflets=()):
    """ZIP file with a CSV file per dataset and the complete cache as JSON"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in DATASETS:
            rows, columns = dataset(name, tickets, offers, leaflets)
            archive.writestr(f"{name}.csv", to_csv(rows, columns))
        archive.writestr("lidl_plus_cache.json", json.dumps(cache, ensure_ascii=False, indent=2))
    return output.getvalue()
