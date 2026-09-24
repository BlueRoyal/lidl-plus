"""
Parsing helpers and analytics for Lidl Plus receipts

All functions are pure: they work on ticket or item lists that were loaded before,
so the (potentially large) cache file only has to be read once.
"""

import re
import unicodedata
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser

_FRACTION = re.compile(r"\.(\d+)")


def to_float(value, default=0.0):
    """Convert a number or a decimal string like "1,49", "1.49" or "1.234,56" to float."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value if value is not None else "").strip().replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return default


def parse_datetime(value):
    """Parse an ISO 8601 timestamp into an aware datetime (UTC if it has no offset), None if invalid."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Python < 3.11 only accepts 3 or 6 fractional digits, .NET APIs send up to 7
    text = _FRACTION.sub(lambda match: "." + match.group(1)[:6].ljust(6, "0"), text, count=1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def section_entries(response, key):
    """Entries of an API response grouped in sections, some endpoints return a plain list instead."""
    if isinstance(response, list):
        entries = response
    elif isinstance(response, dict):
        sections = [section for section in response.get("sections") or [] if isinstance(section, dict)]
        entries = [entry for section in sections for entry in section.get(key) or []]
    else:
        entries = []
    return [entry for entry in entries if isinstance(entry, dict)]


def coupon_is_activated(coupon):
    """Check the activated flags used by the different coupon API versions."""
    return bool(coupon.get("isActivated") or coupon.get("activated") or coupon.get("isActive"))


def is_within_validity(start, end, now=None):
    """Check if now lies between the (optional) start and end timestamps of a coupon."""
    now = now or datetime.now(timezone.utc)
    start_date, end_date = parse_datetime(start), parse_datetime(end)
    if start_date and start_date > now:
        return False
    return not (end_date and end_date < now)


# Version of parse_receipt(), cached receipts are parsed again when it changes
ITEMS_VERSION = 2

_AMOUNT = re.compile(r"-?\d+(?:\.\d{3})*,\d+")
# Second line of a weighed article: "0,786 kg x 1,29 EUR/kg"
_WEIGHT_LINE = re.compile(r"^(?P<quantity>\d+(?:,\d+)?)\s*(?P<unit>[^\d\s]+)\s*x\s*(?P<price>\d+,\d+)")
# Second line of a deposit return: "-29 x 0,25"
_COUNT_LINE = re.compile(r"^(?P<count>-?\d+)\s*x\s*(?P<price>\d+,\d+)$")
# data attributes that are mapped to their own item fields
_ITEM_ATTRIBUTES = {"art_id", "art_description", "unit_price", "art_quantity", "tax_type"}


class _ReceiptLines(HTMLParser):
    """Collect text, classes and data attributes per receipt line, all spans of a line share its id"""

    def __init__(self):
        super().__init__()
        self.lines = {}
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag != "span":
            return
        attributes = dict(attrs)
        self._current = attributes.get("id")
        if not self._current:
            return
        line = self.lines.setdefault(self._current, {"id": self._current, "classes": set(), "data": {}, "text": ""})
        line["classes"].update((attributes.get("class") or "").split())
        line["data"].update(
            {key[5:].replace("-", "_"): value for key, value in attributes.items() if key.startswith("data-") and value}
        )

    def handle_endtag(self, tag):
        if tag == "span":
            self._current = None

    def handle_data(self, data):
        if self._current:
            self.lines[self._current]["text"] += data


def _label(text):
    """Text of a receipt line without its amounts, e.g. "Lidl Plus Rabatt" of "Lidl Plus Rabatt -0,30" """
    return " ".join(_AMOUNT.sub("", text).split())


def _new_item(line, text):
    data = line["data"]
    amounts = _AMOUNT.findall(text[len(" ".join(data.get("art_description", "").split())) :])
    quantity = to_float(data.get("art_quantity") or "1", default=1.0)
    item = {
        "id": data["art_id"],
        "name": data.get("art_description") or "",
        "unit_price": data.get("unit_price") or "",
        "quantity": quantity,
        "tax_type": data.get("tax_type") or "",
        # Amount printed on the receipt, before discounts
        "total": to_float(amounts[-1]) if amounts else round(to_float(data.get("unit_price")) * quantity, 2),
        "unit": "",
        "is_deposit": (data.get("art_description") or "").lower().startswith("pfand"),
        "discounts": [],
        "discount": 0.0,
    }
    extra = {key: value for key, value in data.items() if key not in _ITEM_ATTRIBUTES}
    if extra:
        item["extra"] = extra
    return item


def _receipt_details(lines):
    """VAT table, payments, total savings and receipt data (store, till, receipt number) of a receipt"""
    details = {"vat": [], "payments": [], "total_savings": None, "info": {}}
    for line in lines:
        data, text = line["data"], line["text"]
        amounts = _AMOUNT.findall(text)
        if "tax_percentage" in data:
            details["vat"].append(
                {
                    "tax_type": data.get("tax_type") or "",
                    "rate": to_float(data["tax_percentage"]),
                    "net": to_float(data.get("tax_base_amount")),
                    "tax": to_float(data.get("tax_amount")),
                }
            )
        elif "tender_description" in data:
            details["payments"].append(
                {"method": data["tender_description"], "amount": to_float(amounts[-1]) if amounts else None}
            )
        elif "store" in data and not details["info"]:
            details["info"] = {key: data[key] for key in ("store", "till", "sequence_number", "date") if key in data}
        elif line["id"].startswith("purchase_summary") and text.lower().startswith("gesamter preisvorteil"):
            details["total_savings"] = abs(to_float(amounts[-1])) if amounts else None
    return details


def _add_discount(item, line, amount):
    discount = {"text": _label(line["text"]), "amount": to_float(amount)}
    if line["data"].get("promotion_id"):
        discount["promotion_id"] = line["data"]["promotion_id"]
    item["discounts"].append(discount)
    item["discount"] = round(item["discount"] + discount["amount"], 2)


def _purchase_list(lines, rates):
    """Articles with their discounts, deposit returns and lines that could not be assigned"""
    result = {"items": [], "deposit_returns": [], "other_lines": []}
    # Discounts and further lines belong to the article above, deposit counts to the return above
    below_article = below_deposit_return = False
    for line in lines:
        data, text = line["data"], line["text"]
        amounts = _AMOUNT.findall(text)
        if below_deposit_return and (match := _COUNT_LINE.match(text)):
            # "-29 x 0,25": number of returned bottles and deposit per bottle
            count, per_bottle = abs(int(match.group("count"))), to_float(match.group("price"))
            result["deposit_returns"][-1].update(count=count, unit_amount=per_bottle)
        elif "article" in line["classes"] and data.get("art_id"):
            if text.startswith(" ".join(data.get("art_description", "").split())) or not below_article:
                result["items"].append(_new_item(line, text))
                result["items"][-1]["tax_rate"] = rates.get(data.get("tax_type"))
                below_article = True
            # Further lines of an article are no purchase of their own, e.g. "0,786 kg x 1,29 EUR/kg"
            elif match := _WEIGHT_LINE.match(text):
                result["items"][-1]["unit"] = match.group("unit")
            else:
                result["items"][-1].setdefault("details", []).append(text)
        elif text.lower().startswith("pfandrückgabe") and amounts:
            result["deposit_returns"].append({"amount": to_float(amounts[-1]), "tax_type": text.split()[-1]})
            below_article = False
        elif below_article and amounts and ("discount" in line["classes"] or amounts[-1].startswith("-")):
            _add_discount(result["items"][-1], line, amounts[-1])
        elif amounts:
            result["other_lines"].append(text)
        below_deposit_return = text.lower().startswith("pfandrückgabe")
    return result


def parse_receipt(receipt_html):
    """
    Extract everything the HTML version of a receipt contains.

    Returns a dict with the "items" (articles with their discounts), "deposit_returns",
    "vat" (tax rates), "payments", "total_savings", "info" (store, till, receipt number)
    and "other_lines" (lines with an amount that could not be assigned).
    """
    parser = _ReceiptLines()
    parser.feed(receipt_html or "")
    parser.close()
    lines = [dict(line, text=" ".join(line["text"].split())) for line in parser.lines.values()]
    details = _receipt_details(lines)
    rates = {entry["tax_type"]: entry["rate"] for entry in details["vat"]}
    purchase_lines = [line for line in lines if line["id"].startswith("purchase_list_line")]
    return {**_purchase_list(purchase_lines, rates), **details}


def parse_receipt_items(receipt_html):
    """Extract the articles of an HTML receipt as structured list"""
    return parse_receipt(receipt_html)["items"]


def _store_name(ticket, default=""):
    return (ticket.get("store") or {}).get("name") or default


def ticket_items(tickets):
    """All items across all tickets as flat list with date, store and ticket id."""
    result = []
    for ticket in tickets:
        date = ticket.get("date") or ""
        store = _store_name(ticket)
        ticket_id = ticket.get("id") or ""
        for item in ticket.get("_items") or []:
            result.append({**item, "date": date, "store": store, "ticket_id": ticket_id})
    return result


def total_spending(tickets):
    """Sum of all ticket totals."""
    return round(sum(to_float(ticket.get("totalAmount")) for ticket in tickets), 2)


def average_basket(tickets):
    """Average total amount per shopping trip."""
    if not tickets:
        return 0.0
    return round(total_spending(tickets) / len(tickets), 2)


def spending_by_month(tickets):
    """Total spending grouped by month (YYYY-MM)."""
    result = defaultdict(float)
    for ticket in tickets:
        month = str(ticket.get("date") or "")[:7]
        if month:
            result[month] += to_float(ticket.get("totalAmount"))
    return {month: round(total, 2) for month, total in sorted(result.items())}


def spending_by_store(tickets):
    """Total spending grouped by store name, highest first."""
    result = defaultdict(float)
    for ticket in tickets:
        result[_store_name(ticket, "Unknown")] += to_float(ticket.get("totalAmount"))
    ranking = sorted(result.items(), key=lambda entry: entry[1], reverse=True)
    return {store: round(total, 2) for store, total in ranking}


def shopping_frequency_days(tickets):
    """Average number of days between shopping trips."""
    dates = sorted(date for date in (parse_datetime(ticket.get("date")) for ticket in tickets) if date)
    if len(dates) < 2:
        return None
    gaps = [(later - earlier).days for earlier, later in zip(dates, dates[1:])]
    return round(sum(gaps) / len(gaps), 1)


def price_history(items, item_id):
    """All purchases of a specific item, oldest first."""
    return sorted((item for item in items if item["id"] == item_id), key=lambda item: item["date"])


def last_seen(items, item_id):
    """Last purchase of a specific item, None if it was never bought."""
    purchases = [item for item in items if item["id"] == item_id]
    return max(purchases, key=lambda item: item["date"]) if purchases else None


def frequently_bought(items, limit=10):
    """Top N most frequently bought items by total quantity (deposits are left out)."""
    counts = Counter()
    names = {}
    for item in items:
        if item.get("is_deposit"):
            continue
        counts[item["id"]] += to_float(item.get("quantity"), 1.0)
        names[item["id"]] = item.get("name") or names.get(item["id"], "")
    return [
        {"id": item_id, "name": names[item_id], "total_quantity": round(quantity, 3)}
        for item_id, quantity in counts.most_common(limit)
    ]


def restock_suggestions(items, min_purchases=3, now=None):
    """Items overdue for restocking based on their average purchase interval."""
    purchases = defaultdict(dict)
    names = {}
    for item in items:
        date = parse_datetime(item.get("date"))
        if date is None or item.get("is_deposit"):
            continue
        # Several lines of the same article on one receipt count as a single purchase
        purchases[item["id"]][item.get("ticket_id") or item["date"]] = date
        names[item["id"]] = item.get("name") or names.get(item["id"], "")
    now = now or datetime.now(timezone.utc)
    suggestions = []
    for item_id, dates_by_ticket in purchases.items():
        dates = sorted(dates_by_ticket.values())
        if len(dates) < max(min_purchases, 2):
            continue
        intervals = [(later - earlier).days for earlier, later in zip(dates, dates[1:])]
        avg_interval = sum(intervals) / len(intervals)
        days_since_last = (now - dates[-1]).days
        overdue_by = days_since_last - avg_interval
        if overdue_by > 0:
            suggestions.append(
                {
                    "id": item_id,
                    "name": names[item_id],
                    "avg_interval_days": round(avg_interval, 1),
                    "days_since_last": days_since_last,
                    "overdue_by_days": round(overdue_by, 1),
                }
            )
    return sorted(suggestions, key=lambda suggestion: suggestion["overdue_by_days"], reverse=True)


def item_total(item):
    """Amount paid for a receipt line, after its discounts."""
    if "total" in item:
        return round(to_float(item["total"]) + to_float(item.get("discount")), 2)
    # Items parsed by versions before 0.5.0
    return round(to_float(item.get("unit_price")) * to_float(item.get("quantity"), 1.0), 2)


def category_spending(items):
    """
    Spending on articles with a reduced VAT rate (mostly food) and with the standard rate.

    The standard rate is the highest one of the items, so this works for every country.
    Deposits are left out, they are paid back on return.
    """
    rates = [item["tax_rate"] for item in items if item.get("tax_rate") is not None]
    standard_rate = max(rates) if rates else None
    totals = {"reduced": 0.0, "standard": 0.0}
    for item in items:
        if item.get("is_deposit"):
            continue
        rate = item.get("tax_rate")
        # Without the VAT table of the receipt: A is the reduced rate on German receipts
        reduced = item.get("tax_type") == "A" if rate is None else rate < standard_rate
        totals["reduced" if reduced else "standard"] += item_total(item)
    return {category: round(total, 2) for category, total in totals.items()}


def product_summary(items, history_length=20):
    """
    Purchases aggregated per article, most frequently bought first (deposits are left out).

    Prices are per piece or per kg; "total_spent" and "avg_price" are what was paid after discounts.
    """
    products = {}
    for item in sorted(items, key=lambda entry: entry.get("date") or ""):
        if item.get("is_deposit"):
            continue
        price = to_float(item.get("unit_price"))
        quantity = to_float(item.get("quantity"), 1.0)
        date = item.get("date") or ""
        store = item.get("store") or ""
        product = products.setdefault(
            item["id"],
            {
                "id": item["id"],
                "name": "",
                "purchase_count": 0,
                "receipts": set(),
                "total_quantity": 0.0,
                "total_spent": 0.0,
                "savings": 0.0,
                "unit": item.get("unit") or "",
                "tax_rate": item.get("tax_rate"),
                "first_date": date,
                "prices": [],
            },
        )
        product["name"] = item.get("name") or product["name"]
        product["purchase_count"] += 1
        product["receipts"].add(item.get("ticket_id") or date)
        product["total_quantity"] += quantity
        product["total_spent"] += item_total(item)
        product["savings"] -= to_float(item.get("discount"))
        product.update(last_date=date, last_store=store, last_price=price)
        if price > 0:
            entry = {"date": date, "price": price, "store": store}
            if item.get("discount"):
                entry["discount"] = to_float(item["discount"])
            product["prices"].append(entry)

    result = []
    for product in products.values():
        total_quantity = round(product.pop("total_quantity"), 3)
        total_spent = round(product.pop("total_spent"), 2)
        prices = product.pop("prices")
        result.append(
            {
                **product,
                "receipts": len(product["receipts"]),
                "total_quantity": total_quantity,
                "total_spent": total_spent,
                "savings": round(product["savings"], 2),
                "avg_price": round(total_spent / total_quantity, 2) if total_quantity else 0.0,
                "price_history": prices[-history_length:],
            }
        )
    result.sort(key=lambda product: product["purchase_count"], reverse=True)
    return result


def total_savings(items):
    """Money saved with discounts (Lidl Plus coupons, price advantages, promotions)."""
    return round(-sum(to_float(item.get("discount")) for item in items), 2)


def savings_by_month(items):
    """Money saved with discounts grouped by month (YYYY-MM)."""
    result = defaultdict(float)
    for item in items:
        if item.get("discount") and item.get("date"):
            result[item["date"][:7]] -= to_float(item["discount"])
    return {month: round(total, 2) for month, total in sorted(result.items())}


def visited_stores(tickets):
    """Stores of the receipts with visits, spending and last visit, most visited first."""
    stores = {}
    for ticket in tickets:
        store = ticket.get("store") or {}
        key = store.get("id") or store.get("name")
        if not key:
            continue
        entry = stores.setdefault(
            key,
            {
                "id": store.get("id") or "",
                "name": store.get("name") or "",
                "address": store.get("address") or "",
                "postal_code": store.get("postalCode") or "",
                "locality": store.get("locality") or "",
                "visits": 0,
                "spent": 0.0,
                "last_visit": "",
            },
        )
        entry["visits"] += 1
        entry["spent"] += to_float(ticket.get("totalAmount"))
        entry["last_visit"] = max(entry["last_visit"], ticket.get("date") or "")
    ranking = sorted(stores.values(), key=lambda entry: (entry["visits"], entry["last_visit"]), reverse=True)
    return [{**entry, "spent": round(entry["spent"], 2)} for entry in ranking]


def _strip_footnotes(text):
    """Remove footnote markers like in "-46%¹⁾" """
    cleaned = "".join(char for char in str(text or "") if unicodedata.category(char) != "No")
    return cleaned.replace("⁾", "").replace("⁽", "").strip()


def normalize_offer(offer):
    """Flat view of an offer as returned by LidlPlusApi.store_offers()"""
    price_box = offer.get("priceBox") or {}
    price = price_box.get("largePartNumeric")
    return {
        "id": offer.get("id") or "",
        "title": offer.get("title") or "",
        "brand": offer.get("brand") or "",
        "type": offer.get("offerType") or "",
        "image": offer.get("imageUrl") or "",
        # The fields without UTC carry the local time with a wrong +00:00 offset
        "start": offer.get("startValidityDateUTC") or offer.get("startValidityDate") or "",
        "end": offer.get("endValidityDateUTC") or offer.get("endValidityDate") or "",
        "price": price,
        "regular_price": price_box.get("smallPartNumeric"),
        # e.g. "-3€" or "-50%" for offers without a fixed price
        "price_text": price_box.get("largePartString") or ("" if price is None else f"{price:.2f}"),
        "discount": _strip_footnotes(price_box.get("discountMessage")),
        "packaging": offer.get("packaging") or "",
        "price_per_unit": offer.get("pricePerUnit") or "",
        "product_ids": [str(product_id) for product_id in offer.get("productIds") or []],
    }


def offer_status(offer, now=None):
    """ "upcoming", "current" or "expired" for a normalized offer."""
    now = now or datetime.now(timezone.utc)
    start, end = parse_datetime(offer.get("start")), parse_datetime(offer.get("end"))
    if start and start > now:
        return "upcoming"
    if end and end < now:
        return "expired"
    return "current"


def archived_offers(archive, now=None):
    """All offers of the offer archive in the cache, normalized, with status, stores and first/last seen."""
    offers = []
    for entry in (archive or {}).values():
        offer = normalize_offer(entry.get("offer") or {})
        offer["stores"] = entry.get("stores") or []
        offer["first_seen"] = entry.get("first_seen")
        offer["last_seen"] = entry.get("last_seen")
        offer["status"] = offer_status(offer, now)
        offers.append(offer)
    return sorted(offers, key=lambda offer: (offer["start"], offer["title"]))


def _leaflet_identifier(flyer):
    """Identifier for LidlPlusApi.leaflet, taken from the JSON link or the address of the leaflet"""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(flyer.get("flyerJson") or "").query)
    if query.get("flyer_identifier"):
        return query["flyer_identifier"][0]
    # https://www.lidl.de/l/prospekte/<identifier>/ar/0
    parts = urllib.parse.urlparse(flyer.get("flyerUrlAbsolute") or "").path.strip("/").split("/")
    return parts[parts.index("ar") - 1] if "ar" in parts[1:] else (parts[-1] if parts else "")


def normalize_leaflet(flyer, category="", subcategory=""):
    """Flat view of a leaflet of the leaflet overview (LidlPlusApi.leaflets)"""
    # Some leaflets stay published after the days of their offers, like the one about permanently low prices
    ends = [day for day in (flyer.get("offerEndDate"), flyer.get("endDate")) if day]
    return {
        "id": flyer.get("id") or "",
        "identifier": _leaflet_identifier(flyer),
        "name": flyer.get("name") or "",
        "title": flyer.get("title") or "",
        "category": category or "",
        "subcategory": subcategory or "",
        # Days of the offers (or of the publication, if it ends later), the end day included
        "start": flyer.get("offerStartDate") or flyer.get("startDate") or "",
        "end": max(ends) if ends else "",
        # Days the leaflet is published, usually a week before the offers start
        "published": flyer.get("startDate") or "",
        "pdf": flyer.get("hiResPdfUrl") or flyer.get("pdfUrl") or "",
        "thumbnail": flyer.get("thumbnailUrl") or "",
        "url": flyer.get("flyerUrlAbsolute") or "",
    }


def _dicts(values):
    """The dicts of a list of the leaflet API, which may contain null or be missing"""
    return [value for value in values if isinstance(value, dict)] if isinstance(values, list) else []


def leaflet_overview(overview):
    """All leaflets of the leaflet overview, normalized"""
    return [
        normalize_leaflet(flyer, category.get("name"), subcategory.get("name"))
        for category in _dicts((overview if isinstance(overview, dict) else {}).get("categories"))
        for subcategory in _dicts(category.get("subcategories"))
        for flyer in _dicts(subcategory.get("flyers"))
    ]


def normalize_leaflet_product(product):
    """Flat view of a product of a leaflet, these are articles of the online shop"""
    link = urllib.parse.urlparse(product.get("url") or "")
    url = product.get("url") or ""
    if product.get("canonicalUrl") and link.netloc:
        # Without the tracking parameters of the leaflet viewer
        url = f"{link.scheme}://{link.netloc}{product['canonicalUrl']}"
    return {
        "id": str(product.get("productId") or ""),
        "title": product.get("title") or "",
        "brand": product.get("brand") or "",
        "price": to_float(product.get("price"), None),
        "category": product.get("categoryPrimary") or "",
        "description": unescape(product.get("description") or ""),
        "image": product.get("image") or "",
        "url": url,
    }


def leaflet_details(flyer):
    """
    Pages (images, text and description) and products of a leaflet as returned by LidlPlusApi.leaflet

    Products that are shown on several pages are listed once, with the numbers of all their pages.
    """
    flyer = flyer if isinstance(flyer, dict) else {}
    pages, page_numbers = [], defaultdict(set)
    for page in _dicts(flyer.get("pages")):
        pages.append(
            {
                "number": page.get("number"),
                "image": page.get("image") or "",
                "thumbnail": page.get("thumbnail") or "",
                # Words printed on the page, the only source for the food offers of a leaflet
                "text": page.get("keyWords") or "",
                "description": page.get("altText") or "",
            }
        )
        for link in _dicts(page.get("links")):
            if isinstance(page.get("number"), int):
                page_numbers[link.get("id")].add(page["number"])
    # Products by the id of their link on the page, a list is accepted as well
    products_by_link = flyer.get("products")
    if isinstance(products_by_link, list):
        products_by_link = dict(enumerate(products_by_link))
    products = {}
    for link_id, product in (products_by_link if isinstance(products_by_link, dict) else {}).items():
        if not isinstance(product, dict):
            continue
        entry = normalize_leaflet_product(product)
        entry = products.setdefault(entry["id"] or str(link_id), {**entry, "pages": set()})
        entry["pages"].update(page_numbers[link_id])
    return {
        "pages": pages,
        "products": [{**product, "pages": sorted(product["pages"])} for product in products.values()],
    }


def leaflet_status(leaflet, today=None):
    """ "upcoming", "current" or "expired" for a normalized leaflet, its days are local dates"""
    today = (today or datetime.now().date()).isoformat()
    if leaflet.get("start") and leaflet["start"][:10] > today:
        return "upcoming"
    if leaflet.get("end") and leaflet["end"][:10] < today:
        return "expired"
    return "current"


def archived_leaflets(archive, today=None):
    """All leaflets of the leaflet archive in the cache with status, pages and products, oldest first."""
    leaflets = []
    for entry in (archive or {}).values():
        details = entry.get("details") or {}
        leaflet = dict(entry.get("leaflet") or {})
        leaflet.update(
            status=leaflet_status(leaflet, today),
            first_seen=entry.get("first_seen"),
            last_seen=entry.get("last_seen"),
            pages=details.get("pages") or [],
            products=details.get("products") or [],
        )
        leaflets.append(leaflet)
    return sorted(leaflets, key=lambda leaflet: (leaflet.get("start") or "", leaflet.get("name") or ""))


def leaflet_summary(leaflet):
    """A leaflet without its pages and products, but with their number"""
    summary = {key: value for key, value in leaflet.items() if key not in ("pages", "products")}
    summary.update(page_count=len(leaflet.get("pages") or []), product_count=len(leaflet.get("products") or []))
    return summary


def search_leaflets(leaflets, query):
    """
    Products and pages of the leaflets that mention every word of the query (case insensitive),
    the leaflet with the most matches first.

    Food offers are no products of the leaflet, they are found by the text of their page.
    """
    words = query.lower().split()
    results = []
    for leaflet in leaflets:
        products = [
            product
            for product in leaflet.get("products") or []
            if all(word in f"{product['brand']} {product['title']} {product['description']}".lower() for word in words)
        ]
        pages = [
            {key: page.get(key, "") for key in ("number", "description", "image", "thumbnail")}
            for page in leaflet.get("pages") or []
            if all(word in f"{page.get('text', '')} {page.get('description', '')}".lower() for word in words)
        ]
        if words and (products or pages):
            results.append({"leaflet": leaflet_summary(leaflet), "pages": pages, "products": products})
    return sorted(results, key=lambda result: len(result["pages"]) + len(result["products"]), reverse=True)


def mark_bought_offers(offers, items):
    """Add "bought_products" (id and name of the articles you bought before) to every offer."""
    names = {}
    for item in items:
        if not item.get("is_deposit"):
            names[item["id"]] = item.get("name") or names.get(item["id"], "")
    for offer in offers:
        offer["bought_products"] = [
            {"id": product_id, "name": names[product_id]} for product_id in offer["product_ids"] if product_id in names
        ]
    return offers
