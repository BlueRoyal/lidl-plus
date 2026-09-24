"""
Article database: every article known from the receipts, the offers of the Lidl Plus app and the leaflets,
together with the details added by hand (barcodes, package size, nutrition values, ingredients, notes, photos)

Receipts and offers know the article numbers of Lidl, the products of a leaflet only their number in the online
shop. Food offers are no products of a leaflet at all, they are only printed on its pages, so they are found by the
text of the pages.

Keys of the articles:
- "nr:<article number>": bought articles and offers of a single article
- "offer:<name>": offers of several articles (e.g. all flavours of a brand), named like the offer
- "web:<product number>": products of the online shop in the leaflets
- "own:<id>": articles added by hand
"""

import re
from html import unescape

from . import analytics

# Nutrition values per 100 g or 100 ml, as printed on German packages
NUTRIENTS = (
    "energy_kj",
    "energy_kcal",
    "fat",
    "saturated_fat",
    "carbohydrates",
    "sugars",
    "fiber",
    "protein",
    "salt",
)
NUTRITION_BASES = ("100g", "100ml")
# Kinds of photos of an article
IMAGE_KINDS = ("nutrition", "ingredients", "front", "other")
# Details that can be added to every article, name and brand replace the ones of Lidl
USER_TEXT_FIELDS = ("name", "brand", "package_size", "ingredients", "notes")
# Words of offer titles that say nothing about the article: "Nektarinen, lose" is printed as "Nektarinen"
_FILLER_WORDS = frozenset(
    "je lose und oder mit ohne der die das den dem des ein eine von vom für aus auf im in am an zum zur ca "
    "stück stk packung pack sorte sorten versch verschiedene diverse artikel produkte sortiment".split()
)
# Offers and leaflets listed with an article, the newest ones
_LIST_LENGTH = 20


def _words(text):
    """Lower case words of a text, soft hyphens removed: "Kartoffel\u00ad Salate" is "kartoffelsalate" """
    return re.findall(r"[0-9a-zäöüß]+", re.sub("\u00ad\\s*", "", unescape(str(text or ""))).lower())


def offer_name(offer):
    """Name of a normalized offer with its brand, most titles start with the brand already"""
    brand, title = offer.get("brand") or "", offer.get("title") or ""
    if not brand or title.lower().startswith(brand.lower()):
        return title
    return f"{brand} {title}"


def packaging(offer):
    """Package of an offer like "Je 500 g", without the purchase limit and the prices of the further lines"""
    lines = str(offer.get("packaging") or "").strip().splitlines()
    return re.sub(r"\s*\((?:max|ca)\.?[^)]*\)", "", lines[0], flags=re.IGNORECASE).strip() if lines else ""


def offer_article_key(offer):
    """Key of the article of a normalized offer: its article number, the name for offers of several articles"""
    numbers = offer.get("product_ids") or []
    if len(numbers) == 1:
        return f"nr:{numbers[0]}"
    return f"offer:{analytics.url_slug(offer_name(offer)) or offer.get('id') or ''}"


def product_article_key(product):
    """Key of a product of a leaflet (see analytics.normalize_leaflet_product)"""
    return f"web:{product.get('id') or analytics.url_slug(product.get('title'))}"


def _page_texts(pages):
    """Words and all words written together of every page with text, by page number"""
    texts = {}
    for page in pages or []:
        words = _words(page.get("text"))
        if words and page.get("number") is not None:
            texts[page["number"]] = (set(words), "".join(words))
    return texts


def _offer_words(offer):
    return list(
        dict.fromkeys(word for word in _words(offer.get("title")) if len(word) > 2 and word not in _FILLER_WORDS)
    )


def _mentioned(words, text):
    """True if a page (see _page_texts) mentions the words, one of them may be missing from four words on"""
    page_words, joined = text
    # Compound words may be split on the page: "Kirschtomatensauce" is printed as "Kirsch Tomaten Sauce".
    # A single word must be printed as it is, "Haustier" is not found in "Haustiere".
    found = sum(1 for word in words if word in page_words or (len(words) > 1 and len(word) >= 5 and word in joined))
    return bool(words) and found >= (len(words) if len(words) < 4 else len(words) - 1)


def _category_discount(offer):
    """Discounts on a whole category like "auf Haustier-Artikel" (-50 %) are no articles printed on a page"""
    return str(offer.get("title") or "").lower().startswith("auf ")


def _during(offer, leaflet):
    """True if an offer is valid on a day of a leaflet"""
    # Offers have their times in UTC, a day earlier than the local days of the leaflet at most
    start, end = (leaflet.get("start") or "")[:10], (leaflet.get("end") or "")[:10]
    if start and (offer.get("end") or "")[:10] and offer["end"][:10] < start:
        return False
    return not (end and (offer.get("start") or "")[:10] and offer["start"][:10] > end)


def offers_of_leaflet(leaflet, offers):
    """
    The offers (normalized) printed on the pages of a leaflet, with the numbers of these pages (key "pages").
    Only the offers of the days of the leaflet are compared, the most often printed first.
    """
    texts = _page_texts(leaflet.get("pages"))
    if not texts:
        return []
    found = []
    for offer in offers:
        if not _during(offer, leaflet) or _category_discount(offer):
            continue
        words = _offer_words(offer)
        pages = [number for number, text in texts.items() if _mentioned(words, text)]
        if pages:
            found.append({**offer, "pages": sorted(pages)})
    return sorted(found, key=lambda offer: (-len(offer["pages"]), offer["pages"][0], offer_name(offer)))


def _price_trend(history):
    """ "up", "down" or "stable": the last price of an article compared to the one before"""
    prices = [entry["price"] for entry in history if entry.get("price")]
    if len(prices) < 2:
        return "stable"
    if prices[-1] > prices[-2] * 1.005:
        return "up"
    if prices[-1] < prices[-2] * 0.995:
        return "down"
    return "stable"


def _offer_summary(offer):
    return {
        "id": offer.get("id") or "",
        "title": offer_name(offer),
        "price": offer.get("price"),
        "price_text": offer.get("price_text") or "",
        "regular_price": offer.get("regular_price"),
        "discount": offer.get("discount") or "",
        "packaging": packaging(offer),
        "start": offer.get("start") or "",
        "end": offer.get("end") or "",
        "status": offer.get("status") or "",
    }


def _leaflet_summary(leaflet, pages, price=None):
    return {
        "id": leaflet.get("id") or "",
        "name": leaflet.get("name") or "",
        "title": leaflet.get("title") or "",
        "start": leaflet.get("start") or "",
        "end": leaflet.get("end") or "",
        "status": leaflet.get("status") or "",
        "pages": pages,
        "price": price,
    }


def _prepend(entries, entry):
    """Newest entry first, every one once"""
    entries[:] = [entry] + [other for other in entries if other["id"] != entry["id"]][: _LIST_LENGTH - 1]


def empty_article(key):
    """An article without any details"""
    return {
        "key": key,
        "name": "",
        "brand": "",
        "article_numbers": [],
        # Names printed on the receipts, the newest first
        "receipt_names": [],
        "product_id": "",
        "image": "",
        "packaging": "",
        "price_per_unit": "",
        "category": "",
        "description": "",
        "url": "",
        # "receipts", "offers", "leaflets" or "own"
        "sources": [],
        "purchase_count": 0,
        "total_spent": 0.0,
        "savings": 0.0,
        "avg_price": None,
        "last_price": None,
        "last_bought": "",
        "unit": "",
        "price_trend": "stable",
        "offers": [],
        "leaflets": [],
        # Days the article was bought or offered first and last
        "first_seen": "",
        "last_seen": "",
    }


def _seen(article, source, *days):
    if source not in article["sources"]:
        article["sources"].append(source)
    days = [str(day)[:10] for day in days if day]
    if days:
        article["first_seen"] = min([article["first_seen"], *days]) if article["first_seen"] else min(days)
        article["last_seen"] = max([article["last_seen"], *days])


def _article(catalog, key):
    if key not in catalog:
        catalog[key] = empty_article(key)
    return catalog[key]


def _add_bought(catalog, tickets):
    """The bought articles, named like on the last receipt"""
    items = analytics.ticket_items(tickets)
    names = {}
    for item in sorted(items, key=lambda entry: entry.get("date") or "", reverse=True):
        if not item.get("is_deposit") and item.get("name"):
            names.setdefault(item["id"], {})[item["name"]] = True
    for product in analytics.product_summary(items):
        entry = _article(catalog, f"nr:{product['id']}")
        entry.update(
            name=product["name"],
            article_numbers=[product["id"]],
            receipt_names=list(names.get(product["id"], {})),
            purchase_count=product["purchase_count"],
            total_spent=product["total_spent"],
            savings=product["savings"],
            avg_price=product["avg_price"],
            last_price=product.get("last_price"),
            last_bought=product.get("last_date") or "",
            unit=product.get("unit") or "",
            price_trend=_price_trend(product.get("price_history") or []),
        )
        _seen(entry, "receipts", product.get("first_date"), product.get("last_date"))


def _add_offers(catalog, offers):
    """The articles of the offers (sorted by their start), the newest offer gives name, brand, picture and package"""
    bought = set(catalog)
    for offer in offers:
        key = offer_article_key(offer)
        entry = _article(catalog, key)
        entry.update(
            name=offer_name(offer) or entry["name"],
            brand=offer.get("brand") or entry["brand"],
            image=offer.get("image") or entry["image"],
            packaging=packaging(offer) or entry["packaging"],
            price_per_unit=offer.get("price_per_unit") or entry["price_per_unit"],
        )
        entry["article_numbers"] = list(dict.fromkeys(entry["article_numbers"] + list(offer.get("product_ids") or [])))
        summary = _offer_summary(offer)
        _prepend(entry["offers"], summary)
        _seen(entry, "offers", offer.get("start"))
        if key.startswith("offer:"):
            # The bought variants of an offer of several articles list the offer as well
            for number in offer.get("product_ids") or []:
                if f"nr:{number}" in bought:
                    _prepend(catalog[f"nr:{number}"]["offers"], summary)


def _add_leaflets(catalog, leaflets, offers):
    """The products of the online shop in the leaflets, and the leaflets that show the offers on their pages"""
    for leaflet in sorted(leaflets, key=lambda entry: entry.get("start") or ""):
        for product in leaflet.get("products") or []:
            entry = _article(catalog, product_article_key(product))
            entry.update(
                name=product.get("title") or entry["name"],
                brand=product.get("brand") or entry["brand"],
                product_id=product.get("id") or entry["product_id"],
                image=product.get("image") or entry["image"],
                category=product.get("category") or entry["category"],
                description=product.get("description") or entry["description"],
                url=product.get("url") or entry["url"],
            )
            _prepend(entry["leaflets"], _leaflet_summary(leaflet, product.get("pages") or [], product.get("price")))
            _seen(entry, "leaflets", leaflet.get("start"))
        for offer in offers_of_leaflet(leaflet, offers):
            entry = catalog[offer_article_key(offer)]
            _prepend(entry["leaflets"], _leaflet_summary(leaflet, offer["pages"], offer.get("price")))
            _seen(entry, "leaflets", leaflet.get("start"))


def article_catalog(tickets, offers=(), leaflets=()):
    """
    Every article known from the receipts (tickets of the cache), the offers (analytics.archived_offers)
    and the leaflets (analytics.archived_leaflets, with pages and products), by key (see above).

    An article lists the offers and leaflets it was part of, the newest first.
    """
    catalog = {}
    offers = sorted(offers, key=lambda offer: offer.get("start") or "")
    _add_bought(catalog, tickets)
    _add_offers(catalog, offers)
    _add_leaflets(catalog, leaflets, offers)
    return catalog


def normalize_barcode(text):
    """
    Digits of an EAN/GTIN barcode (8, 12, 13 or 14 digits with a valid check digit), None if it is none.
    Spaces and dashes are ignored.
    """
    digits = re.sub(r"[\s-]", "", str(text or ""))
    if not digits.isdigit() or len(digits) not in (8, 12, 13, 14):
        return None
    # GS1 check digit: weights 3 and 1 from the right, the check digit left out
    total = sum(int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(digits[:-1])))
    return digits if (10 - total % 10) % 10 == int(digits[-1]) else None


def barcode_key(code):
    """The same barcode as EAN-13 and as UPC-A (12 digits) has the same key: the GTIN-14 with leading zeros"""
    return str(code).zfill(14)


def _has_nutrition(article):
    return any(article.get("nutrition", {}).get(nutrient) is not None for nutrient in NUTRIENTS)


def merge_user_data(catalog, user_data):
    """
    The articles of the catalog with the details added by hand (user_data by article key), and the articles added
    by hand. Name and brand added by hand replace the ones of Lidl, which stay as "lidl_name" and "lidl_brand".
    """
    articles = {}
    for key, data in (user_data or {}).items():
        if key not in catalog and not data.get("name"):
            continue  # details of an article that is not known (anymore)
        article = dict(catalog.get(key) or {**empty_article(key), "sources": ["own"]})
        article.update(lidl_name=article["name"], lidl_brand=article["brand"])
        for field in ("name", "brand"):
            if data.get(field):
                article[field] = data[field]
        # A picture of another database (like Open Food Facts), if Lidl has none
        article["image"] = article["image"] or data.get("image") or ""
        article.update(
            barcodes=list(data.get("barcodes") or []),
            package_size=data.get("package_size") or "",
            nutrition=dict(data.get("nutrition") or {}),
            nutrition_basis=data.get("nutrition_basis") or NUTRITION_BASES[0],
            ingredients=data.get("ingredients") or "",
            notes=data.get("notes") or "",
            images=list(data.get("images") or []),
            sources_of_details=dict(data.get("sources") or {}),
            updated=data.get("updated") or "",
            # Anything added by hand, also a name instead of the one of Lidl
            has_details=any(
                data.get(field) for field in (*USER_TEXT_FIELDS, "barcodes", "nutrition", "images", "image")
            ),
        )
        articles[key] = article
    for key, article in catalog.items():
        if key not in articles:
            articles[key] = {
                **article,
                "lidl_name": article["name"],
                "lidl_brand": article["brand"],
                "barcodes": [],
                "package_size": "",
                "nutrition": {},
                "nutrition_basis": NUTRITION_BASES[0],
                "ingredients": "",
                "notes": "",
                "images": [],
                "sources_of_details": {},
                "updated": "",
                "has_details": False,
            }
    for article in articles.values():
        article["has_nutrition"] = _has_nutrition(article)
    return articles


_FILTERS = {
    "bought": lambda article: article["purchase_count"] > 0,
    "offers": lambda article: "offers" in article["sources"],
    "leaflets": lambda article: "leaflets" in article["sources"],
    "own": lambda article: "own" in article["sources"],
    "details": lambda article: article["has_details"],
    "nutrition": lambda article: article["has_nutrition"],
    "no_nutrition": lambda article: not article["has_nutrition"],
    "ingredients": lambda article: bool(article["ingredients"]),
    "photos": lambda article: bool(article["images"]),
    "barcode": lambda article: bool(article["barcodes"]),
}
FILTERS = tuple(_FILTERS)


def _price(article):
    """Last price paid, otherwise the price of the newest offer or leaflet, None if there is none"""
    if article["last_price"] is not None:
        return article["last_price"]
    prices = [entry["price"] for entry in article["offers"] + article["leaflets"] if entry.get("price") is not None]
    return prices[0] if prices else None


def _name(article):
    # "Äpfel" is sorted like "Aepfel", not after "Zucker"
    return analytics.url_slug(article["name"])


_SORTS = {
    "count-desc": (lambda article: (article["purchase_count"], article["last_seen"]), True),
    "spent-desc": (lambda article: (article["total_spent"], article["last_seen"]), True),
    "savings-desc": (lambda article: (article["savings"], article["last_seen"]), True),
    "name-asc": (lambda article: (_name(article), article["key"]), False),
    "last-desc": (lambda article: (article["last_seen"], article["purchase_count"]), True),
    "bought-desc": (lambda article: (article["last_bought"], article["last_seen"]), True),
    # Articles without a price last
    "price-desc": (lambda article: (_price(article) is not None, _price(article) or 0.0, article["last_seen"]), True),
    "price-asc": (lambda article: (_price(article) is None, _price(article) or 0.0, _name(article)), False),
    "updated-desc": (lambda article: (article["updated"], article["last_seen"]), True),
}
SORTS = tuple(_SORTS)


def _search_text(article):
    return " ".join(
        [
            article["name"],
            article["brand"],
            article.get("lidl_name") or "",
            " ".join(article["receipt_names"]),
            " ".join(article["article_numbers"]),
            " ".join(article.get("barcodes") or []),
            article["product_id"],
            article["category"],
            article["description"],
            article.get("ingredients") or "",
            article.get("notes") or "",
        ]
    ).lower()


def find_articles(  # pylint: disable=too-many-arguments
    articles, query="", kind="", sort="count-desc", *, trend="", bought_since=""
):
    """
    Articles (see merge_user_data) that contain every word of the query, of a kind of FILTERS, with a price trend
    ("up", "down" or "stable") or bought since a day (ISO date), sorted by one of SORTS.
    """
    words = str(query or "").lower().split()
    if kind and kind not in _FILTERS:
        raise ValueError(f"Unknown filter {kind}, use one of {', '.join(FILTERS)}")
    if sort not in _SORTS:
        raise ValueError(f"Unknown sort order {sort}, use one of {', '.join(SORTS)}")
    found = []
    for article in articles.values():
        if kind and not _FILTERS[kind](article):
            continue
        if trend and (not article["purchase_count"] or article["price_trend"] != trend):
            continue
        if bought_since and article["last_bought"][:10] < bought_since:
            continue
        if words:
            text = _search_text(article)
            if not all(word in text for word in words):
                continue
        found.append(article)
    key, reverse = _SORTS[sort]
    return sorted(found, key=key, reverse=reverse)


def article_list_entry(article):
    """An article without its long lists and texts, for lists of many articles"""
    entry = {
        key: value
        for key, value in article.items()
        if key not in ("offers", "leaflets", "description", "ingredients", "notes", "nutrition", "images")
    }
    entry.update(
        offer=article["offers"][0] if article["offers"] else None,
        leaflet=article["leaflets"][0] if article["leaflets"] else None,
        image_count=len(article.get("images") or []),
    )
    return entry
