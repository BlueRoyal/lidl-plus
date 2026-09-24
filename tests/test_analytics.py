"""Tests for the parsing helpers and analytics."""

from datetime import date, datetime, timedelta, timezone

import pytest

from lidlplus import analytics
from sample_data import OPENING_HOURS, Receipt, directory_page, flyer, item, leaflet, leaflet_overview, offer, ticket


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1.0),
        (1.5, 1.5),
        ("1,49", 1.49),
        ("1.49", 1.49),
        ("1.234,56", 1234.56),
        ("-0,50", -0.5),
        (" 2 ", 2.0),
        ("", 0.0),
        (None, 0.0),
        ("abc", 0.0),
    ],
)
def test_to_float(value, expected):
    assert analytics.to_float(value) == expected


def test_to_float_default():
    assert analytics.to_float("", default=1.0) == 1.0


def test_parse_datetime():
    parsed = analytics.parse_datetime("2024-03-15T18:23:45.1234567+01:00")
    assert parsed == datetime(2024, 3, 15, 17, 23, 45, 123456, tzinfo=timezone.utc)
    assert analytics.parse_datetime("2024-03-15T18:23:45Z") == datetime(2024, 3, 15, 18, 23, 45, tzinfo=timezone.utc)
    # Without offset the timestamp is treated as UTC, so it can be compared with aware datetimes
    assert analytics.parse_datetime("2024-03-15").tzinfo == timezone.utc
    assert analytics.parse_datetime("") is None
    assert analytics.parse_datetime(None) is None
    assert analytics.parse_datetime("not a date") is None


def test_section_entries():
    response = {"sections": [{"coupons": [{"id": 1}, "broken"]}, None, {"coupons": None}, {"coupons": [{"id": 2}]}]}
    assert analytics.section_entries(response, "coupons") == [{"id": 1}, {"id": 2}]
    assert analytics.section_entries([{"id": 3}], "coupons") == [{"id": 3}]
    assert not analytics.section_entries(None, "coupons")
    assert not analytics.section_entries("unexpected", "coupons")


def test_coupon_is_activated():
    assert analytics.coupon_is_activated({"isActivated": True})
    assert analytics.coupon_is_activated({"activated": True})
    assert not analytics.coupon_is_activated({"isActivated": False})


def test_is_within_validity():
    now = datetime(2026, 5, 10, 12, tzinfo=timezone.utc)
    assert analytics.is_within_validity("2026-05-01T00:00:00Z", "2026-05-31T23:59:59Z", now)
    assert not analytics.is_within_validity("2026-05-11T00:00:00+02:00", None, now)
    assert not analytics.is_within_validity(None, "2026-05-09T23:59:59Z", now)
    assert analytics.is_within_validity(None, None, now)


SAMPLE_RECEIPT = (
    Receipt()
    .article("0082052", "Feldsalat", "1,49")
    .discount("Lidl Plus Rabatt", "-0,30", promotion_id="100001000-DE-TEMPLATE-DEOF000037182-1")
    .weighed("0082345", "Zucchini", "1,29", "0,786", "1,01")
    .article("0080460", "Traub dunk 500g", "1,59", total="6,36", quantity="4")
    .discount("Preisvorteil", "-0,60")
    .article("0012345", "Pfand 0,25 EM", "0,25", total="1,50", quantity="6", tax="B")
    .article("0099999", "Monster Energy", "1,99", tax="B")
    .deposit_return("-3,75", 15)
    .savings("0,90")
    .payment("Kreditkarte", "7,70")
    .build()
)


def test_parse_receipt():
    receipt = analytics.parse_receipt(SAMPLE_RECEIPT)
    items = receipt.pop("items")
    assert [(item["name"], item["quantity"], item["total"], item["discount"]) for item in items] == [
        ("Feldsalat", 1.0, 1.49, -0.3),
        # The weight line below the article is no purchase of its own
        ("Zucchini", 0.786, 1.01, 0.0),
        ("Traub dunk 500g", 4.0, 6.36, -0.6),
        ("Pfand 0,25 EM", 6.0, 1.5, 0.0),
        ("Monster Energy", 1.0, 1.99, 0.0),
    ]
    assert items[0]["discounts"] == [
        {"text": "Lidl Plus Rabatt", "amount": -0.3, "promotion_id": "100001000-DE-TEMPLATE-DEOF000037182-1"}
    ]
    assert items[2]["discounts"] == [{"text": "Preisvorteil", "amount": -0.6}]
    assert items[0]["unit_price"] == "1,49"
    assert items[1]["unit"] == "kg"
    assert [item["is_deposit"] for item in items] == [False, False, False, True, False]
    # The VAT table of the receipt tells which tax type is the reduced rate
    assert [item["tax_rate"] for item in items] == [7.0, 7.0, 7.0, 19.0, 19.0]
    assert receipt == {
        "deposit_returns": [{"amount": -3.75, "tax_type": "B", "count": 15, "unit_amount": 0.25}],
        "vat": [
            {"tax_type": "A", "rate": 7.0, "net": 1.0, "tax": 0.07},
            {"tax_type": "B", "rate": 19.0, "net": 1.0, "tax": 0.07},
        ],
        "payments": [{"method": "Kreditkarte", "amount": 7.7}],
        "total_savings": 0.9,
        "info": {"store": "1234", "till": "3", "sequence_number": "4711", "date": "19.09.2026"},
        "other_lines": [],
    }
    # Everything on the receipt is covered: articles, discounts and deposit returns add up to the payment
    paid = sum(analytics.item_total(item) for item in items) + receipt["deposit_returns"][0]["amount"]
    assert round(paid, 2) == 7.7


def test_parse_receipt_keeps_unknown_lines():
    html = (
        Receipt()
        .article("1", "Brot", "2,49")
        ._purchase_line(["Neue Zeile"], "article", art_id="1", art_description="Brot", unit_price="2,49")
        .build()
        .replace(
            '</span>\n<span class="purchase_summary">',
            '<span id="purchase_list_line_9">Gutschein 5,00</span></span>\n<span class="purchase_summary">',
        )
    )
    receipt = analytics.parse_receipt(html)
    assert receipt["items"][0]["details"] == ["Neue Zeile"]
    assert receipt["other_lines"] == ["Gutschein 5,00"]


def test_parse_receipt_items_empty():
    assert not analytics.parse_receipt_items(None)
    assert analytics.parse_receipt("")["items"] == []


TICKETS = [
    ticket(
        "t1", "2026-03-02T10:00:00+01:00", "12,50", items=[item("a", "Milch", "1,09", 2), item("b", "Brot", "2,49")]
    ),
    ticket("t2", "2026-03-20T10:00:00+01:00", 7.5, store="Lidl Nord", items=[item("a", "Milch", "1,19")]),
    ticket("t3", "2026-04-01T10:00:00+02:00", "20", items=[item("a", "Milch", "1,19"), item("c", "Kaffee", "5,99")]),
    ticket("t4", "", "3,00"),
]


def test_spending():
    assert analytics.spending_by_month(TICKETS) == {"2026-03": 20.0, "2026-04": 20.0}
    assert analytics.spending_by_store(TICKETS) == {"Lidl Musterstadt": 35.5, "Lidl Nord": 7.5}
    assert analytics.total_spending(TICKETS) == 43.0
    assert analytics.average_basket(TICKETS) == 10.75
    assert analytics.average_basket([]) == 0.0


def test_spending_by_store_without_store():
    assert analytics.spending_by_store([{"totalAmount": 1, "store": None}]) == {"Unknown": 1.0}


def test_shopping_frequency_days():
    # 18 days, then 11 days and 23 hours (daylight saving time starts in between)
    assert analytics.shopping_frequency_days(TICKETS) == 14.5
    assert analytics.shopping_frequency_days(TICKETS[:1]) is None


def test_items_and_history():
    items = analytics.ticket_items(TICKETS)
    assert len(items) == 5
    assert items[0] == {
        **TICKETS[0]["_items"][0],
        "date": TICKETS[0]["date"],
        "store": "Lidl Musterstadt",
        "ticket_id": "t1",
    }
    assert [entry["ticket_id"] for entry in analytics.price_history(items, "a")] == ["t1", "t2", "t3"]
    assert analytics.last_seen(items, "a")["ticket_id"] == "t3"
    assert analytics.last_seen(items, "unknown") is None
    assert analytics.frequently_bought(items, 2) == [
        {"id": "a", "name": "Milch", "total_quantity": 4.0},
        {"id": "b", "name": "Brot", "total_quantity": 1.0},
    ]


def test_frequently_bought_rounds_weights():
    items = [item("x", "Bananen", "1,29", 0.1), item("x", "Bananen", "1,29", 0.2)]
    assert analytics.frequently_bought(items)[0]["total_quantity"] == 0.3


def test_restock_suggestions():
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    dates = [now - timedelta(days=days) for days in (50, 40, 30)]
    tickets = [
        ticket(f"t{index}", date.isoformat(), 1, items=[item("a", "Milch", "1,09"), item("a", "Milch", "1,09")])
        for index, date in enumerate(dates)
    ]
    suggestions = analytics.restock_suggestions(analytics.ticket_items(tickets), now=now)
    # Two lines on the same receipt are one purchase, so the interval is 10 days and not 0 or 5
    assert suggestions == [
        {"id": "a", "name": "Milch", "avg_interval_days": 10.0, "days_since_last": 30, "overdue_by_days": 20.0}
    ]
    assert not analytics.restock_suggestions(analytics.ticket_items(tickets), min_purchases=4, now=now)
    assert analytics.restock_suggestions(analytics.ticket_items(tickets), min_purchases=1, now=now)


def test_category_spending():
    items = [
        item("a", "Milch", "1,09", discount=-0.09),
        item("b", "Shampoo", "3,95", tax="B"),
        item("p", "Pfand 0,25", "0,25", tax="B", deposit=True),
    ]
    # Reduced rate (mostly food) after discounts, deposits are left out
    assert analytics.category_spending(items) == {"reduced": 1.0, "standard": 3.95}
    # Austrian rates
    assert analytics.category_spending(
        [item("a", "Brot", "2,00", rate=10.0), item("b", "Seife", "1,00", rate=20.0)]
    ) == {
        "reduced": 2.0,
        "standard": 1.0,
    }
    # Items of old caches without VAT table: A is the reduced rate on German receipts
    old_items = [
        {"id": "a", "unit_price": "1,00", "quantity": 2, "tax_type": "A"},
        {"id": "b", "unit_price": "3", "tax_type": "B"},
    ]
    assert analytics.category_spending(old_items) == {"reduced": 2.0, "standard": 3.0}


def test_savings():
    items = [
        {**item("a", "Milch", "1,09", discount=-0.3), "date": "2026-08-02T10:00:00"},
        {**item("b", "Brot", "2,49", discount=-0.5), "date": "2026-09-01T10:00:00"},
        {**item("c", "Käse", "3,49"), "date": "2026-09-01T10:00:00"},
    ]
    assert analytics.total_savings(items) == 0.8
    assert analytics.savings_by_month(items) == {"2026-08": 0.3, "2026-09": 0.5}


def test_visited_stores():
    tickets = [
        ticket("t1", "2026-09-01T10:00:00", 10, store_id="DE1234"),
        ticket("t2", "2026-09-10T10:00:00", "5,50", store_id="DE1234"),
        ticket("t3", "2026-08-01T10:00:00", 20, store="Lidl Nord", store_id="DE2067"),
        {"id": "t4", "date": "2026-08-01", "totalAmount": 1, "store": None},
    ]
    stores = analytics.visited_stores(tickets)
    assert [(store["id"], store["visits"], store["spent"], store["last_visit"]) for store in stores] == [
        ("DE1234", 2, 15.5, "2026-09-10T10:00:00"),
        ("DE2067", 1, 20.0, "2026-08-01T10:00:00"),
    ]
    assert stores[0]["postal_code"] == "12345"


def test_product_summary():
    tickets = [
        ticket(
            "t1",
            "2026-09-01T10:00:00",
            1,
            items=[item("a", "Milch", "1,09", 2), item("p", "Pfand", "0,25", deposit=True)],
        ),
        ticket("t2", "2026-09-05T10:00:00", 1, items=[item("a", "Milch 3,5%", "1,19", discount=-0.2)]),
    ]
    products = analytics.product_summary(analytics.ticket_items(tickets))
    assert len(products) == 1
    milk = products[0]
    assert {
        key: milk[key] for key in ("name", "purchase_count", "receipts", "total_quantity", "total_spent", "savings")
    } == {
        "name": "Milch 3,5%",
        "purchase_count": 2,
        "receipts": 2,
        "total_quantity": 3.0,
        "total_spent": 3.17,
        "savings": 0.2,
    }
    assert milk["avg_price"] == 1.06
    assert milk["last_price"] == 1.19
    assert milk["first_date"] == "2026-09-01T10:00:00"
    assert milk["price_history"][-1] == {
        "date": "2026-09-05T10:00:00",
        "price": 1.19,
        "store": "Lidl Musterstadt",
        "discount": -0.2,
    }


def test_frequently_bought_skips_deposits():
    items = [item("p", "Pfand 0,25 EM", "0,25", 24, deposit=True), item("a", "Milch", "1,09")]
    assert [entry["id"] for entry in analytics.frequently_bought(items)] == ["a"]


NOW = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
OFFERS = [
    offer(
        "o1",
        "Erasco Fertiggericht",
        ["0003051", "7714056"],
        "2026-09-23T22:00:01Z",
        "2026-09-26T21:59:59Z",
        brand="ERASCO",
    ),
    offer("o2", "Kaffee", ["0080100"], "2026-09-27T22:00:01Z", "2026-10-03T21:59:59Z"),
    offer("o3", "Brot", ["0082052"], "2026-09-10T22:00:01Z", "2026-09-13T21:59:59Z"),
]


def test_normalize_offer():
    normalized = analytics.normalize_offer(OFFERS[0])
    assert normalized == {
        "id": "o1",
        "title": "Erasco Fertiggericht",
        "brand": "ERASCO",
        "type": "StoreSpecialPriceDiscount",
        "image": "https://example.invalid/o1.jpg",
        "start": "2026-09-23T22:00:01Z",
        "end": "2026-09-26T21:59:59Z",
        "price": 1.77,
        "regular_price": 3.29,
        "price_text": "1.77",
        "discount": "-46%",
        "packaging": "Je 800 g",
        "price_per_unit": "1 kg = 2.21",
        "product_ids": ["0003051", "7714056"],
    }
    # Offers without a fixed price
    percentage = {"id": "x", "priceBox": {"largePartString": "-50%", "discountMessage": "Spare"}}
    assert analytics.normalize_offer(percentage)["price_text"] == "-50%"
    assert analytics.normalize_offer(percentage)["price"] is None


def test_offer_status_and_archive():
    archive = {
        offer_id: {
            "offer": data,
            "stores": ["DE1234"],
            "first_seen": "2026-09-20T08:00:00",
            "last_seen": "2026-09-24T08:00:00",
        }
        for offer_id, data in (("o1", OFFERS[0]), ("o2", OFFERS[1]), ("o3", OFFERS[2]))
    }
    offers = analytics.archived_offers(archive, now=NOW)
    assert [(entry["id"], entry["status"]) for entry in offers] == [
        ("o3", "expired"),
        ("o1", "current"),
        ("o2", "upcoming"),
    ]
    assert offers[0]["stores"] == ["DE1234"]
    assert offers[0]["first_seen"] == "2026-09-20T08:00:00"

    bought = [
        item("0082052", "Brot", "2,49"),
        item("0080100", "Kaffee gemahlen", "4,99"),
        item("x", "Pfand", "0,25", deposit=True),
    ]
    analytics.mark_bought_offers(offers, bought)
    assert [entry["bought_products"] for entry in offers] == [
        [{"id": "0082052", "name": "Brot"}],
        [],
        [{"id": "0080100", "name": "Kaffee gemahlen"}],
    ]


WEEKLY = leaflet("l1", "Aktionsprospekt", "aktionsprospekt-21-09-2026-26-09-2026-1fb6af", "2026-09-21", "2026-09-26")
NEXT_WEEK = leaflet("l2", "Aktionsprospekt", "aktionsprospekt-28-09-2026-03-10-2026-321560", "2026-09-28", "2026-10-03")
TRAVEL = leaflet("l3", "Reise-Highlights", "reise-highlights-09-2026", "2026-09-01", "2026-09-20")
PAGES = flyer(
    ("Romana Salat Stück Kopfsalat Kaffee gemahlen", []),
    ("Werkzeug Akku-Bohrschrauber", [("100001", "Akku-Bohrschrauber", "39.99"), ("100002", "Bit-Set", "4.99")]),
    ("Werkzeug", [("100001", "Akku-Bohrschrauber", "39.99")]),
)["flyer"]


def test_leaflet_overview():
    overview = leaflet_overview(("Filial-Angebote", [WEEKLY, NEXT_WEEK]), ("Lidl-Reisen", [TRAVEL]))
    leaflets = analytics.leaflet_overview(overview)
    assert [(entry["id"], entry["category"]) for entry in leaflets] == [
        ("l1", "Filial-Angebote"),
        ("l2", "Filial-Angebote"),
        ("l3", "Lidl-Reisen"),
    ]
    assert leaflets[0] == {
        "id": "l1",
        "identifier": "aktionsprospekt-21-09-2026-26-09-2026-1fb6af",
        "name": "Aktionsprospekt",
        "title": "2026-09-21 – 2026-09-26",
        "category": "Filial-Angebote",
        "subcategory": "Filial-Angebote aktuell",
        "start": "2026-09-21",
        "end": "2026-09-26",
        "published": "2026-01-01",
        "pdf": "https://assets.example.invalid/l1-hires-01.pdf",
        "thumbnail": "https://images.example.invalid/l1.jpg",
        "url": "https://www.lidl.de/l/prospekte/aktionsprospekt-21-09-2026-26-09-2026-1fb6af/ar/0?lf=HHZ",
        "regions": ["0"],
    }
    # Without the JSON link the identifier is taken from the address of the leaflet
    assert analytics.normalize_leaflet({**WEEKLY, "flyerJson": ""})["identifier"] == leaflets[0]["identifier"]
    assert (
        analytics.normalize_leaflet({"flyerUrlAbsolute": "https://www.lidl.de/l/prospekte/abc"})["identifier"] == "abc"
    )
    assert analytics.normalize_leaflet({})["identifier"] == ""
    assert not analytics.leaflet_overview(None)


def test_leaflet_published_after_its_offers():
    # Like "Der Preisführer macht Deutschland": its offer days ended, but it is still published
    permanent = {**WEEKLY, "offerStartDate": "2025-12-08", "offerEndDate": "2026-02-21", "endDate": "2026-12-08"}
    normalized = analytics.normalize_leaflet(permanent)
    assert (normalized["start"], normalized["end"]) == ("2025-12-08", "2026-12-08")
    assert analytics.leaflet_status(normalized, today=date(2026, 9, 24)) == "current"


def test_leaflet_data_with_unexpected_values():
    overview = {"categories": [None, {"name": "A", "subcategories": [None, {"flyers": [None, WEEKLY, "x"]}]}]}
    assert [leaflet["id"] for leaflet in analytics.leaflet_overview(overview)] == ["l1"]
    assert not analytics.leaflet_overview({"categories": None})
    assert not analytics.leaflet_overview(["unexpected"])
    details = analytics.leaflet_details(
        {
            "pages": [None, {"number": 1, "keyWords": "Kaffee", "links": [None, {"id": "a"}]}],
            "products": [None, {"productId": 7, "title": "Wasserkocher", "price": "19.99"}],
        }
    )
    assert [page["number"] for page in details["pages"]] == [1]
    assert [(product["id"], product["price"], product["pages"]) for product in details["products"]] == [
        ("7", 19.99, [])
    ]
    assert analytics.leaflet_details({"products": {"a": None, "b": {"title": "Ohne Id"}}})["products"][0]["title"] == (
        "Ohne Id"
    )
    assert analytics.leaflet_details(None) == {"pages": [], "products": []}


def test_leaflet_status():
    weekly = analytics.normalize_leaflet(WEEKLY)
    assert analytics.leaflet_status(weekly, today=date(2026, 9, 20)) == "upcoming"
    assert analytics.leaflet_status(weekly, today=date(2026, 9, 21)) == "current"
    # The last day of the offers is included
    assert analytics.leaflet_status(weekly, today=date(2026, 9, 26)) == "current"
    assert analytics.leaflet_status(weekly, today=date(2026, 9, 27)) == "expired"
    assert analytics.leaflet_status({}, today=date(2026, 9, 27)) == "current"


def test_leaflet_details():
    details = analytics.leaflet_details(PAGES)
    assert details["pages"][0] == {
        "number": 1,
        "image": "https://images.example.invalid/page-1.jpg",
        "thumbnail": "https://images.example.invalid/page-1-small.jpg",
        "text": "Romana Salat Stück Kopfsalat Kaffee gemahlen",
        "description": "[Seite 1]",
    }
    # A product on two pages is listed once
    assert [(product["id"], product["pages"]) for product in details["products"]] == [
        ("100001", [2, 3]),
        ("100002", [2]),
    ]
    assert details["products"][0] == {
        "id": "100001",
        "title": "Akku-Bohrschrauber",
        "brand": "PARKSIDE",
        "price": 39.99,
        "category": "Kategorien/Heimwerken",
        "description": "Farbe: grün",
        # Without the tracking parameters
        "url": "https://www.lidl.de/p/100001",
        "image": "https://www.lidl.de/assets/100001.jpg",
        "pages": [2, 3],
    }
    assert analytics.normalize_leaflet_product({"productId": 5, "price": ""})["price"] is None
    assert analytics.leaflet_details({}) == {"pages": [], "products": []}


def test_archived_and_searched_leaflets():
    archive = {
        "l2": {"leaflet": analytics.normalize_leaflet(NEXT_WEEK), "first_seen": "a", "last_seen": "b"},
        "l1": {
            "leaflet": analytics.normalize_leaflet(WEEKLY),
            "first_seen": "2026-09-19T08:00:00+00:00",
            "last_seen": "2026-09-24T08:00:00+00:00",
            "details": analytics.leaflet_details(PAGES),
        },
    }
    leaflets = analytics.archived_leaflets(archive, today=date(2026, 9, 24))
    assert [(entry["id"], entry["status"], len(entry["pages"])) for entry in leaflets] == [
        ("l1", "current", 3),
        ("l2", "upcoming", 0),
    ]
    assert leaflets[0]["first_seen"] == "2026-09-19T08:00:00+00:00"

    # Food offers are only found by the text of their page
    results = analytics.search_leaflets(leaflets, "kaffee")
    assert len(results) == 1
    assert results[0]["leaflet"]["id"] == "l1"
    assert "pages" not in results[0]["leaflet"]
    assert (results[0]["leaflet"]["page_count"], results[0]["leaflet"]["product_count"]) == (3, 2)
    assert results[0]["pages"] == [
        {
            "number": 1,
            "description": "[Seite 1]",
            "image": "https://images.example.invalid/page-1.jpg",
            "thumbnail": "https://images.example.invalid/page-1-small.jpg",
        }
    ]
    assert not results[0]["products"]
    # Every word has to match, brand and description count as well
    results = analytics.search_leaflets(leaflets, "Parkside AKKU")
    assert [product["id"] for product in results[0]["products"]] == ["100001"]
    assert not results[0]["pages"]
    assert [page["number"] for page in analytics.search_leaflets(leaflets, "werkzeug")[0]["pages"]] == [2, 3]
    assert not analytics.search_leaflets(leaflets, "grün kaffee")
    assert not analytics.search_leaflets(leaflets, "  ")
    # The leaflet with the most matches first
    many = {**leaflets[1], "id": "l3", "pages": leaflets[0]["pages"], "products": leaflets[0]["products"]}
    assert [result["leaflet"]["id"] for result in analytics.search_leaflets([leaflets[0], many], "werkzeug")] == [
        "l1",
        "l3",
    ]
    many["products"] = many["products"] + [{**leaflets[0]["products"][0], "id": "x"}]
    assert [result["leaflet"]["id"] for result in analytics.search_leaflets([leaflets[0], many], "akku")] == [
        "l3",
        "l1",
    ]


def test_leaflets_of_region():
    national = analytics.normalize_leaflet(WEEKLY)
    region_10 = analytics.normalize_leaflet({**WEEKLY, "id": "r10", "regions": [{"code": "10"}, {"code": "42"}]})
    region_2 = analytics.normalize_leaflet({**WEEKLY, "id": "r2", "regions": [{"type": "offer_region", "code": "2"}]})
    travel = analytics.normalize_leaflet(TRAVEL)
    # Saved before the regions were known
    old = {key: value for key, value in analytics.normalize_leaflet(NEXT_WEEK).items() if key != "regions"}
    leaflets = [national, region_10, region_2, travel, old]
    # The regional variant replaces the national leaflet of the same week, other regions are left out
    assert [entry["id"] for entry in analytics.leaflets_of_region(leaflets, 10)] == ["r10", "l3", "l2"]
    assert [entry["id"] for entry in analytics.leaflets_of_region(leaflets, "42")] == ["r10", "l3", "l2"]
    assert [entry["id"] for entry in analytics.leaflets_of_region(leaflets, None)] == ["l1", "l3", "l2"]
    # A region without regional variant gets the national leaflets
    assert [entry["id"] for entry in analytics.leaflets_of_region(leaflets, 99)] == ["l1", "l3", "l2"]


def test_url_slug():
    assert analytics.url_slug("Mönchengladbach") == "moenchengladbach"
    assert analytics.url_slug("Nordrhein-Westfalen") == "nordrhein-westfalen"
    assert analytics.url_slug("Frankfurt am Main") == "frankfurt-am-main"
    assert analytics.url_slug("Straße 1b") == "strasse-1b"
    assert analytics.url_slug(None) == ""


def test_store_offer_regions():
    page = directory_page(
        stores=[("DE01234", 10, "Grevenbroich"), ("DE04711", 42, "Kerpen")], cities=[("Frankfurt", "/s/de-DE/x/")]
    )
    assert analytics.store_offer_regions(page) == {"DE01234": (10, "Grevenbroich"), "DE04711": (42, "Kerpen")}
    # Listed again as nearby store without the name of the region
    listed_twice = directory_page(
        stores=[("DE01234", 10, "Grevenbroich"), ("DE01234", 10, None), ("DE04711", 42, None)]
    )
    assert analytics.store_offer_regions(listed_twice) == {"DE01234": (10, "Grevenbroich"), "DE04711": (42, "")}
    assert analytics.nuxt_objects(page, "name", "url") == [{"name": "Frankfurt", "url": "/s/de-DE/x/"}]
    assert not analytics.store_offer_regions(None)
    assert not analytics.store_offer_regions([{"objectNumber": 5, "marketingData": 99}])


def test_soft_hyphens_in_leaflets():
    details = analytics.leaflet_details(
        flyer(
            (
                "Chef Select Regionale Kartoffel\u00ad Salate Filial\u00adangebote",
                [("1", "Kartoffel\u00adsalat", "0.88")],
            )
        )["flyer"]
    )
    assert details["pages"][0]["text"] == "Chef Select Regionale KartoffelSalate Filialangebote"
    assert details["products"][0]["title"] == "Kartoffelsalat"
    leaflet_entry = {**analytics.normalize_leaflet(WEEKLY), **details}
    assert analytics.search_leaflets([leaflet_entry], "kartoffelsalat")[0]["pages"][0]["number"] == 1


def test_store_directory_entries():
    page = directory_page(
        stores=[("DE01234", 10, "Grevenbroich", OPENING_HOURS), ("DE01234", 10, None), ("DE04711", 42, "Kerpen")]
    )
    entries = analytics.store_directory_entries(page)
    assert entries["DE01234"]["region"] == 10
    assert entries["DE01234"]["opening_hours"] == {
        "monday": [["07:00", "22:00"]],
        "tuesday": [["07:00", "22:00"]],
        "wednesday": [["07:00", "22:00"]],
        "thursday": [["07:00", "22:00"]],
        "friday": [["07:00", "22:00"]],
        "saturday": [["07:00", "21:00"]],
        "sunday": [],
        "special": {"2026-10-03": []},
    }
    assert entries["DE04711"] == {"region": 42, "region_name": "Kerpen", "opening_hours": None}
    assert analytics.opening_hours(None)["monday"] == []


def test_shopping_times():
    tickets = [
        ticket("t1", "2026-09-19T17:01:49", 10),
        ticket("t2", "2026-09-12T17:45:00", 5),
        ticket("t3", "2026-09-14T08:10:00+02:00", 5, store_id="DE9999"),
        ticket("t4", "", 5),
    ]
    times = analytics.shopping_times(tickets, "DE1234")
    assert (times["store"], times["receipts"]) == ("DE1234", 2)
    # Saturday 17:00, the receipts are in local time
    assert times["hours"][5][17] == 2
    assert sum(map(sum, times["hours"])) == 2
    everything = analytics.shopping_times(tickets)
    assert (everything["receipts"], everything["hours"][0][8]) == (3, 1)
