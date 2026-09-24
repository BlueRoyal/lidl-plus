"""Tests for the export as CSV, JSON and ZIP."""

import io
import json
import zipfile
from datetime import date

import pytest

from lidlplus import analytics, export
from sample_data import Receipt, flyer, leaflet, offer, ticket


def parsed_ticket(ticket_id, date, total, html, **kwargs):
    entry = ticket(ticket_id, date, total, **kwargs)
    entry["htmlPrintedReceipt"] = html
    receipt = analytics.parse_receipt(html)
    entry["_items"] = receipt.pop("items")
    entry["_receipt"] = receipt
    entry["collectingModel"] = {"points": 12}
    return entry


TICKETS = [
    parsed_ticket(
        "t1",
        "2026-09-19T17:01:49",
        7.7,
        Receipt()
        .article("0082052", "Feldsalat", "1,49")
        .discount("Lidl Plus Rabatt", "-0,30", promotion_id="P1")
        .weighed("0082345", "Zucchini", "1,29", "0,786", "1,01")
        .article("0012345", "Pfand 0,25 EM", "0,25", total="1,50", quantity="6", tax="B")
        .deposit_return("-1,50", 6)
        .payment("Kreditkarte", "2,20")
        .build(),
    ),
    parsed_ticket("t0", "2026-09-01T10:00:00", 1.49, Receipt().article("0082052", "Feldsalat", "1,49").build()),
]
OFFER_ARCHIVE = {
    "o1": {
        "offer": offer("o1", "Feldsalat", ["0082052"], "2026-09-20T22:00:00Z", "2026-09-26T21:59:59Z"),
        "stores": ["DE1234"],
    }
}
OFFERS = analytics.mark_bought_offers(analytics.archived_offers(OFFER_ARCHIVE), analytics.ticket_items(TICKETS))


LEAFLET_ARCHIVE = {
    "l1": {
        "leaflet": analytics.normalize_leaflet(
            leaflet("l1", "Aktionsprospekt", "aktion-1", "2026-09-21", "2026-09-26")
        ),
        "details": analytics.leaflet_details(
            flyer(("Werkzeug", [("100001", "Akku-Bohrschrauber", "39.99"), ("100002", "Bit-Set", "4.99")]))["flyer"]
        ),
    },
    # Leaflets without products have no rows
    "l0": {"leaflet": analytics.normalize_leaflet(leaflet("l0", "Reisen", "reisen", "2026-09-01", "2026-09-30"))},
}
LEAFLETS = analytics.archived_leaflets(LEAFLET_ARCHIVE)


def test_to_csv_for_german_spreadsheets():
    text = export.to_csv([{"a": 1.5, "b": True, "c": None, "d": "x;y", "e": 0.786}], ["a", "b", "c", "d", "e"])
    assert text.startswith("﻿")
    assert text[1:].splitlines() == ["a;b;c;d;e", '1,5;ja;;"x;y";0,786']


def test_receipt_rows():
    rows = export.receipt_rows(TICKETS)
    assert [row["id"] for row in rows] == ["t1", "t0"]
    assert {
        key: rows[0][key] for key in ("store_id", "total", "articles", "savings", "deposit_returned", "payment")
    } == {
        "store_id": "DE1234",
        "total": 7.7,
        "articles": 3,
        "savings": 0.3,
        "deposit_returned": 1.5,
        "payment": "Kreditkarte",
    }
    assert rows[0]["receipt_number"] == "4711"
    assert rows[0]["lidl_plus_points"] == 12


def test_item_rows():
    rows = export.item_rows(TICKETS)
    assert [(row["receipt_id"], row["name"], row["quantity"], row["unit"], row["paid"]) for row in rows] == [
        ("t1", "Feldsalat", 1.0, "", 1.19),
        ("t1", "Zucchini", 0.786, "kg", 1.01),
        ("t1", "Pfand 0,25 EM", 6.0, "", 1.5),
        ("t0", "Feldsalat", 1.0, "", 1.49),
    ]
    assert rows[0]["discounts"] == "Lidl Plus Rabatt -0,30"
    assert rows[2]["deposit"] is True


def test_product_and_offer_rows():
    products = export.product_rows(TICKETS)
    assert [(row["article_id"], row["purchase_count"], row["total_spent"]) for row in products] == [
        ("0082052", 2, 2.68),
        ("0082345", 1, 1.01),
    ]
    offers = export.offer_rows(OFFERS)
    assert offers[0]["bought_products"] == "Feldsalat"
    assert offers[0]["stores"] == "DE1234"


def test_leaflet_rows():
    rows = export.leaflet_rows(LEAFLETS)
    assert [(row["leaflet_id"], row["product_id"], row["price"]) for row in rows] == [
        ("l1", "100001", 39.99),
        ("l1", "100002", 4.99),
    ]
    assert rows[0]["pdf"] == "https://assets.example.invalid/l1-hires-01.pdf"
    assert rows[0]["url"] == "https://www.lidl.de/p/100001"
    assert rows[0]["start"] == "2026-09-21"


def test_dataset():
    rows, columns = export.dataset("items", TICKETS, OFFERS)
    assert columns == export.ITEM_COLUMNS
    assert len(rows) == 4
    rows, columns = export.dataset("leaflets", TICKETS, OFFERS, LEAFLETS)
    assert columns == export.LEAFLET_COLUMNS
    assert len(rows) == 2
    # Leaflets are optional
    assert not export.dataset("leaflets", TICKETS, OFFERS)[0]
    with pytest.raises(ValueError):
        export.dataset("unknown", TICKETS, OFFERS)


def test_to_zip():
    cache = {"tickets": {entry["id"]: entry for entry in TICKETS}, "offers": {}}
    with zipfile.ZipFile(io.BytesIO(export.to_zip(TICKETS, OFFERS, cache, LEAFLETS))) as archive:
        assert sorted(archive.namelist()) == [
            "items.csv",
            "leaflets.csv",
            "lidl_plus_cache.json",
            "offers.csv",
            "products.csv",
            "receipts.csv",
        ]
        assert len(archive.read("leaflets.csv").decode("utf-8-sig").splitlines()) == 3
        # The complete data including the HTML receipts
        assert (
            json.loads(archive.read("lidl_plus_cache.json"))["tickets"]["t1"]["htmlPrintedReceipt"]
            == TICKETS[0]["htmlPrintedReceipt"]
        )
        assert archive.read("items.csv").decode("utf-8-sig").splitlines()[0] == ";".join(export.ITEM_COLUMNS)


def test_export_file():
    cache = {"tickets": {entry["id"]: entry for entry in TICKETS}, "offers": OFFER_ARCHIVE, "leaflets": LEAFLET_ARCHIVE}
    lines = export.export_file(cache, "items", "csv").decode("utf-8-sig").splitlines()
    assert lines[0] == ";".join(export.ITEM_COLUMNS)
    assert len(lines) == 5
    offers = json.loads(export.export_file(cache, "offers", "json"))
    assert offers[0]["bought_products"] == "Feldsalat"
    assert len(json.loads(export.export_file(cache, "leaflets", "json"))) == 2
    with zipfile.ZipFile(io.BytesIO(export.export_file(cache, "all"))) as archive:
        assert "leaflets.csv" in archive.namelist()
    # A ZIP file always contains everything
    with zipfile.ZipFile(io.BytesIO(export.export_file(cache, "items", "zip"))) as archive:
        assert "receipts.csv" in archive.namelist()
    assert export.export_file({"tickets": {}}, "receipts").decode("utf-8-sig").strip() == ";".join(
        export.RECEIPT_COLUMNS
    )
    # The status of the leaflets depends on the day
    rows = json.loads(export.export_file(cache, "leaflets", "json", today=date(2026, 9, 27)))
    assert {row["status"] for row in rows} == {"expired"}
    with pytest.raises(ValueError):
        export.export_file(cache, "items", "xlsx")
    with pytest.raises(ValueError):
        export.export_file(cache, "unknown")
