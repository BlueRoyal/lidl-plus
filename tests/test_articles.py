"""Tests for the article database: catalog, offers on leaflet pages, details added by hand and the search."""

import pytest

from lidlplus import analytics, articles
from sample_data import flyer, item, leaflet, offer, ticket


def _offers(*entries):
    return analytics.archived_offers({entry["id"]: {"offer": entry, "stores": ["DE1234"]} for entry in entries})


def _leaflet(leaflet_id, start, end, *pages):
    """A normalized leaflet with pages and products, pages as (printed words, products)"""
    entry = analytics.normalize_leaflet(leaflet(leaflet_id, "Aktionsprospekt", leaflet_id, start, end))
    details = analytics.leaflet_details(flyer(*pages)["flyer"])
    return {**entry, "status": analytics.leaflet_status(entry), **details}


TICKETS = [
    ticket(
        "t1", "2026-05-02T10:00:00", "5,00", items=[item("0001", "Skyr Natur", "1,29"), item("0002", "Tomaten", "1,89")]
    ),
    ticket(
        "t2",
        "2026-05-12T18:30:00",
        "5,00",
        items=[
            item("0001", "Milbona Skyr", "1,49", 2),
            item("0006151", "Pfand 0,25", "0,25", deposit=True),
            item("0003", "Schweinenack.Kräuter", "5,99"),
        ],
    ),
]
OFFERS = _offers(
    offer("o1", "Milbona Skyr Natur", ["0001"], "2026-05-10T22:00:00Z", "2026-05-16T21:59:59Z", price=0.99),
    offer(
        "o2",
        "Grillmeister Schweine-Nackensteaks",
        ["0003", "0004", "0005"],
        "2026-05-10T22:00:00Z",
        "2026-05-16T21:59:59Z",
        price=4.69,
    ),
    offer("o3", "Kaffee Crema", ["0009"], "2026-05-10T22:00:00Z", "2026-05-16T21:59:59Z", brand="BELLAROM"),
    # An older offer of the same article, its price is not the newest one
    offer("o0", "Milbona Skyr Natur", ["0001"], "2026-04-01T22:00:00Z", "2026-04-05T21:59:59Z", price=1.09),
)
LEAFLETS = [
    _leaflet(
        "l1",
        "2026-05-11",
        "2026-05-16",
        ("Lidl Plus -26% Milbona Skyr Natur Normalpreis Grillmeister Schweine- Nackensteaks", []),
        ("Bellarom Kaffee Crema 1-Kg-Packung", [("100001", "Akku-Bohrschrauber", "39.99")]),
    ),
    # The next week: the drill again, the offers of the week before are not on its pages
    _leaflet("l2", "2026-05-18", "2026-05-23", ("Milbona Skyr Natur", [("100001", "Akku-Bohrschrauber", "34.99")])),
]


def test_offer_name_and_packaging():
    assert articles.offer_name({"title": "Milbona Skyr", "brand": "MILBONA"}) == "Milbona Skyr"
    assert articles.offer_name({"title": "Kaffee Crema", "brand": "BELLAROM"}) == "BELLAROM Kaffee Crema"
    assert articles.offer_name({"title": "Lauchzwiebeln", "brand": ""}) == "Lauchzwiebeln"
    assert articles.packaging({"packaging": "Je 800 g/750 ml (Max. 24 Stück)\nNormalpreis: 1.99"}) == "Je 800 g/750 ml"
    assert articles.packaging({"packaging": "Ca. 1,2 kg (max. 24 Stück)"}) == "Ca. 1,2 kg"
    assert articles.packaging({"packaging": ""}) == ""


def test_article_keys():
    assert articles.offer_article_key({"product_ids": ["0001"], "title": "Skyr"}) == "nr:0001"
    several = {"product_ids": ["1", "2"], "title": "Fertiggericht", "brand": "ERASCO"}
    assert articles.offer_article_key(several) == "offer:erasco-fertiggericht"
    assert articles.product_article_key({"id": "100001", "title": "Bohrer"}) == "web:100001"


def test_offers_of_leaflet():
    found = articles.offers_of_leaflet(LEAFLETS[0], OFFERS)
    # Printed on the most pages first, then by page and name
    assert [(offer["id"], offer["pages"]) for offer in found] == [("o2", [1]), ("o1", [1]), ("o3", [2])]
    # Offers of other weeks are not compared
    assert [offer["id"] for offer in articles.offers_of_leaflet(LEAFLETS[1], OFFERS)] == []
    assert articles.offers_of_leaflet({"pages": []}, OFFERS) == []


@pytest.mark.parametrize(
    ("title", "text", "found"),
    [
        # Compound words may be split and soft hyphens are removed
        ("Italiamo Kirschtomatensauce", "Italiamo Kirsch Tomaten Sauce", True),
        ("Amanie Eukalyptus-Menthol-Bonbons", "Amanie Eukalyptus Menthol Husten\u00ad Bonbons", True),
        # Filler words like "lose" do not count
        ("Nektarinen, lose", "Lose Ware Birnen", False),
        ("Nektarinen, lose", "Nektarinen", True),
        # A single word as it is
        ("Lauchzwiebeln", "Lauchzwiebeln Bund", True),
        ("Lauchzwiebeln", "Frühlingslauchzwiebeln", False),
        # Discounts on a whole category are no articles on a page
        ("auf Haustier-Artikel", "Alles für dein Haustier", False),
        # All words of short titles, one may be missing from four words on
        ("Milbona Skyr Drink", "Milbona Skyr Natur", False),
        ("Junge Winzer Grauburgunder QbA Weißwein", "Junge Winzer Grauburgunder Weißwein", True),
    ],
)
def test_offer_on_page(title, text, found):
    offers = _offers(offer("o", title, ["1"], "2026-05-10T22:00:00Z", "2026-05-16T21:59:59Z"))
    page = _leaflet("l", "2026-05-11", "2026-05-16", (text, []))
    assert bool(articles.offers_of_leaflet(page, offers)) is found


def test_article_catalog():
    catalog = articles.article_catalog(TICKETS, OFFERS, LEAFLETS)
    assert sorted(catalog) == [
        "nr:0001",
        "nr:0002",
        "nr:0003",
        "nr:0009",
        "offer:grillmeister-schweine-nackensteaks",
        "web:100001",
    ]

    skyr = catalog["nr:0001"]
    # The offer gives the name, the receipts their names, newest first
    assert skyr["name"] == "Milbona Skyr Natur"
    assert skyr["receipt_names"] == ["Milbona Skyr", "Skyr Natur"]
    assert skyr["sources"] == ["receipts", "offers", "leaflets"]
    assert skyr["purchase_count"] == 2
    assert skyr["last_price"] == 1.49
    assert skyr["price_trend"] == "up"
    assert skyr["packaging"] == "Je 800 g"
    assert [entry["id"] for entry in skyr["offers"]] == ["o1", "o0"]
    assert skyr["offers"][0]["price"] == 0.99
    assert [(entry["id"], entry["pages"]) for entry in skyr["leaflets"]] == [("l1", [1])]
    assert (skyr["first_seen"], skyr["last_seen"]) == ("2026-04-01", "2026-05-12")

    # Deposits are no articles
    assert "nr:0006151" not in catalog
    # An offer of several articles is an article of its own, the bought variant lists it
    family = catalog["offer:grillmeister-schweine-nackensteaks"]
    assert family["article_numbers"] == ["0003", "0004", "0005"]
    assert family["purchase_count"] == 0
    assert catalog["nr:0003"]["name"] == "Schweinenack.Kräuter"
    assert [entry["id"] for entry in catalog["nr:0003"]["offers"]] == ["o2"]
    # An offer that was never bought
    assert catalog["nr:0009"]["name"] == "BELLAROM Kaffee Crema"
    assert catalog["nr:0009"]["sources"] == ["offers", "leaflets"]

    drill = catalog["web:100001"]
    assert drill["name"] == "Akku-Bohrschrauber"
    assert drill["brand"] == "PARKSIDE"
    assert drill["description"] == "Farbe: grün"
    # The newest leaflet first, with its price
    assert [(entry["id"], entry["price"], entry["pages"]) for entry in drill["leaflets"]] == [
        ("l2", 34.99, [1]),
        ("l1", 39.99, [2]),
    ]


def test_article_catalog_without_data():
    assert articles.article_catalog([], [], []) == {}


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("4056489123458", None),  # wrong check digit
        ("4 056489 12345 3", "4056489123453"),
        ("4056489-123453", "4056489123453"),
        ("96385074", "96385074"),  # EAN-8
        ("036000291452", "036000291452"),  # UPC-A
        ("abc", None),
        ("", None),
        ("12345", None),
    ],
)
def test_normalize_barcode(code, expected):
    assert articles.normalize_barcode(code) == expected


def test_barcode_key():
    # The same barcode as UPC-A and as EAN-13
    assert articles.barcode_key("036000291452") == articles.barcode_key("0036000291452")


USER_DATA = {
    "nr:0001": {
        "barcodes": ["4056489123453"],
        "package_size": "500 g",
        "nutrition": {"energy_kcal": 63, "protein": 11.0},
        "ingredients": "Magermilch, Milchsäurebakterien",
        "images": [{"id": "a" * 32, "kind": "nutrition"}],
        "updated": "2026-05-13T10:00:00+00:00",
    },
    # The name replaces the one of Lidl
    "nr:0002": {"name": "Romatomaten", "notes": "lecker"},
    "own:1": {
        "name": "Duschgel",
        "brand": "Cien",
        "ingredients": "Aqua, Sodium Laureth Sulfate",
        "image": "https://images.example.invalid/duschgel.jpg",
    },
    # Details of an unknown article without name are left out
    "nr:9999": {"notes": "weg"},
}


def test_merge_user_data():
    merged = articles.merge_user_data(articles.article_catalog(TICKETS, OFFERS, LEAFLETS), USER_DATA)
    assert "nr:9999" not in merged
    skyr = merged["nr:0001"]
    assert skyr["barcodes"] == ["4056489123453"]
    assert skyr["nutrition_basis"] == "100g"
    assert skyr["has_nutrition"] and skyr["has_details"]
    assert skyr["purchase_count"] == 2
    tomatoes = merged["nr:0002"]
    assert (tomatoes["name"], tomatoes["lidl_name"]) == ("Romatomaten", "Tomaten")
    assert not tomatoes["has_nutrition"] and tomatoes["has_details"]
    own = merged["own:1"]
    assert own["sources"] == ["own"]
    assert (own["name"], own["brand"], own["purchase_count"]) == ("Duschgel", "Cien", 0)
    assert own["image"] == "https://images.example.invalid/duschgel.jpg"
    drill = merged["web:100001"]
    assert drill["barcodes"] == [] and not drill["has_details"]
    # Another name is a detail as well
    renamed = articles.merge_user_data(
        articles.article_catalog(TICKETS, OFFERS, LEAFLETS), {"nr:0003": {"name": "Nacken"}}
    )
    assert renamed["nr:0003"]["has_details"] and not renamed["nr:0002"]["has_details"]


def test_leaflets_added_by_hand():
    user_data = {
        # Also on page 2 of the leaflet the drill is known from
        "web:100001": {"leaflets": [{"id": "l1", "name": "Aktionsprospekt", "title": "t", "pages": [2, 1]}]},
        # A leaflet without products, the article is known from there only
        "own:1": {
            "name": "Duschgel",
            "leaflets": [
                {
                    "id": "l9",
                    "name": "dauerhaft günstiger!",
                    "title": "",
                    "start": "2026-01-01",
                    "end": "2099-12-31",
                    "pages": [7],
                }
            ],
        },
    }
    merged = articles.merge_user_data(articles.article_catalog(TICKETS, OFFERS, LEAFLETS), user_data)
    drill = merged["web:100001"]
    assert [(entry["id"], entry["pages"], entry.get("added_pages")) for entry in drill["leaflets"]] == [
        ("l2", [1], None),
        ("l1", [1, 2], [1, 2]),
    ]
    assert drill["has_details"]
    own = merged["own:1"]
    assert own["sources"] == ["own", "leaflets"]
    assert [(entry["id"], entry["pages"], entry["added_pages"], entry["status"]) for entry in own["leaflets"]] == [
        ("l9", [7], [7], "current")
    ]
    assert [article["key"] for article in articles.find_articles(merged, kind="leaflets", sort="name-asc")][
        0
    ] == "web:100001"
    # The catalog stays as it is
    assert "added_pages" not in articles.article_catalog(TICKETS, OFFERS, LEAFLETS)["web:100001"]["leaflets"][1]


def test_articles_of_leaflet():
    merged = articles.merge_user_data(
        articles.article_catalog(TICKETS, OFFERS, LEAFLETS),
        {"own:1": {"name": "Bellarom Kaffee Crema"}, "own:2": {"name": "Handseife"}},
    )
    own = [article for article in merged.values() if "own" in article["sources"]]
    found = articles.articles_of_leaflet(LEAFLETS[0], own)
    assert [(article["key"], article["pages"]) for article in found] == [("own:1", [2])]
    assert articles.articles_of_leaflet({"pages": []}, own) == []


def test_find_articles():
    merged = articles.merge_user_data(articles.article_catalog(TICKETS, OFFERS, LEAFLETS), USER_DATA)

    def keys(*args, **kwargs):
        return [article["key"] for article in articles.find_articles(merged, *args, **kwargs)]

    # Every word, also in receipt names, barcodes and ingredients
    assert keys("skyr") == ["nr:0001"]
    assert keys("SKYR milbona") == ["nr:0001"]
    assert keys("4056489123453") == ["nr:0001"]
    assert keys("sodium") == ["own:1"]
    assert keys("schweinenack") == ["nr:0003"]
    # Most bought first, then by the day they were seen last
    assert keys()[:3] == ["nr:0001", "nr:0003", "nr:0002"]
    assert keys(kind="bought") == ["nr:0001", "nr:0003", "nr:0002"]
    assert keys(kind="own") == ["own:1"]
    assert keys(kind="leaflets", sort="name-asc") == [
        "web:100001",
        "nr:0009",
        "offer:grillmeister-schweine-nackensteaks",
        "nr:0001",
    ]
    assert keys(kind="nutrition") == ["nr:0001"]
    assert keys(kind="photos") == ["nr:0001"]
    assert keys(kind="barcode") == ["nr:0001"]
    assert keys(kind="ingredients", sort="name-asc") == ["own:1", "nr:0001"]
    assert set(keys(kind="no_nutrition")) == set(merged) - {"nr:0001"}
    assert keys(trend="up") == ["nr:0001"]
    assert keys(bought_since="2026-05-10") == ["nr:0001", "nr:0003"]
    # Without a price last
    # The last price paid, otherwise the price of the newest offer or leaflet
    assert keys(sort="price-asc")[:3] == ["nr:0001", "nr:0009", "nr:0002"]
    assert keys(sort="price-asc")[-1] == "own:1"
    assert keys(sort="price-desc")[0] == "web:100001"
    assert keys(sort="updated-desc")[0] == "nr:0001"
    with pytest.raises(ValueError, match="Unknown filter"):
        articles.find_articles(merged, kind="wrong")
    with pytest.raises(ValueError, match="Unknown sort"):
        articles.find_articles(merged, sort="wrong")


def test_article_list_entry():
    merged = articles.merge_user_data(articles.article_catalog(TICKETS, OFFERS, LEAFLETS), USER_DATA)
    entry = articles.article_list_entry(merged["nr:0001"])
    assert "offers" not in entry and "nutrition" not in entry and "ingredients" not in entry
    assert entry["offer"]["id"] == "o1"
    assert entry["leaflet"]["id"] == "l1"
    assert entry["image_count"] == 1
    assert articles.article_list_entry(merged["own:1"])["offer"] is None
