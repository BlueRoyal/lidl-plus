"""Builders for Lidl Plus API data used by the tests.

The receipts follow the structure of real v3 receipts: a <pre> with spans, all spans of a
printed line share the id of the line and carry the data attributes of the line.
"""

from html import escape


def _line(line_id, parts, css="", **data):
    """A printed receipt line, every part becomes a span with the attributes of the line"""
    attributes = "".join(f' data-{key.replace("_", "-")}="{escape(str(value))}"' for key, value in data.items())
    spans = []
    for index, text in enumerate(parts):
        bold = " css_bold" if index % 2 == 0 and text.strip() else ""
        spans.append(f'<span id="{line_id}" class="{(css + bold).strip()}"{attributes}>{escape(text)}</span>')
    return "".join(spans)


class Receipt:
    """Builds the HTML of a receipt line by line"""

    def __init__(self):
        self._lines = []
        self._summary = []
        self._footer = []

    def _purchase_line(self, parts, css="", **data):
        self._lines.append(_line(f"purchase_list_line_{len(self._lines) + 2}", parts, css, **data))
        return self

    def article(self, art_id, name, unit_price, total=None, quantity=None, tax="A"):
        """ "Name  1,59 x 4  6,36 A" or "Name  1,49 A" """
        data = {"art_id": art_id, "art_quantity": quantity, "unit_price": unit_price, "tax_type": tax}
        data = {key: value for key, value in data.items() if value is not None}
        data["art_description"] = name
        price = f"{unit_price} x {quantity}" if quantity and "," not in quantity else ""
        parts = (
            [name, "   ", price, " ", total or unit_price, " ", tax]
            if price
            else [name, "   ", total or unit_price, " ", tax]
        )
        return self._purchase_line(parts, "article", **data)

    def weighed(self, art_id, name, unit_price, weight, total, tax="A"):
        """Article line and the line "0,786 kg x 1,29 EUR/kg" below it"""
        data = {"art_id": art_id, "art_quantity": weight, "unit_price": unit_price, "tax_type": tax}
        data["art_description"] = name
        self._purchase_line([name, "   ", total, " ", tax], "article", **data)
        return self._purchase_line(["  ", weight, " ", "kg x", " ", unit_price, "  ", "EUR/kg"], "article", **data)

    def discount(self, text, amount, promotion_id=None):
        """ "Lidl Plus Rabatt -0,30" with class discount, or "Preisvorteil -0,60" without"""
        if promotion_id:
            return self._purchase_line([text, "   ", amount], "discount", promotion_id=promotion_id)
        return self._purchase_line([text, "   ", amount])

    def deposit_return(self, amount, count, per_bottle="0,25", tax="B"):
        self._purchase_line(["Pfandrückgabe", "   ", amount, " ", tax])
        return self._purchase_line([f"-{count} x {per_bottle}"])

    def savings(self, amount):
        self._summary.append(_line("purchase_summary_2", ["Gesamter Preisvorteil", "   ", amount]))
        return self

    def payment(self, method, amount):
        self._summary.append(_line("purchase_tender_information_1", [method, "   ", amount], tender_description=method))
        return self

    def build(self, vat=(("A", "7"), ("B", "19")), store="1234", till="3"):
        vat_lines = "".join(
            _line(
                f"vat_info_line_{index}",
                [tax, f" {rate}%"],
                tax_type=tax,
                tax_percentage=rate,
                tax_base_amount="1,00",
                tax_amount="0,07",
            )
            for index, (tax, rate) in enumerate(vat, start=1)
        )
        info = _line(
            "receipt_data_1",
            [f"Filiale {store} Kasse {till}"],
            "receipt_data",
            store=store,
            till=till,
            sequence_number="4711",
            date="19.09.2026",
        )
        header = '<span class="header" data-till-country="DE" data-receipt-language="de">LIDL</span>\n'
        currency = _line("purchase_list_line_1", ["EUR"], "currency", currency="€")
        lines = "\n".join(self._lines)
        return (
            "<html><head><style></style></head><body><pre>"
            f'{header}<span class="purchase_list">{currency}\n'
            f"{lines}</span>\n<span class=\"purchase_summary\">{''.join(self._summary)}</span>\n"
            f'<span class="vat_info">{vat_lines}</span>{info}</pre></body></html>'
        )


def ticket(ticket_id, date, total, store="Lidl Musterstadt", items=None, store_id="DE1234"):
    """A ticket like it is stored in the cache (detail API response plus parsed items)."""
    return {
        "id": ticket_id,
        "date": date,
        "totalAmount": total,
        "store": {
            "id": store_id,
            "name": store,
            "address": "Hauptstraße 1",
            "postalCode": "12345",
            "locality": "Musterstadt",
        },
        "_items": items or [],
    }


def item(art_id, name, price, quantity=1.0, tax="A", total=None, discount=0.0, rate=None, deposit=False):
    """A parsed receipt line, total defaults to price x quantity"""
    unit_price = float(str(price).replace(",", "."))
    result = {
        "id": art_id,
        "name": name,
        "unit_price": price,
        "quantity": quantity,
        "tax_type": tax,
        "total": round(unit_price * quantity, 2) if total is None else total,
        "unit": "",
        "is_deposit": deposit,
        "discounts": [{"text": "Lidl Plus Rabatt", "amount": discount}] if discount else [],
        "discount": discount,
        "tax_rate": rate if rate is not None else {"A": 7.0, "B": 19.0}.get(tax),
    }
    return result


def offer(offer_id, title, product_ids, start, end, price=1.77, regular=3.29, discount="-46%¹⁾", brand=""):
    """An offer as returned by the offers API"""
    return {
        "id": offer_id,
        "offerType": "StoreSpecialPriceDiscount",
        "title": title,
        "brand": brand,
        "imageUrl": f"https://example.invalid/{offer_id}.jpg",
        "priceBox": {
            "priceSymbol": "€",
            "discountMessage": discount,
            "strikethrough": True,
            "largePartNumeric": price,
            "largePartString": None,
            "smallPartNumeric": regular,
            "smallPartString": None,
        },
        "productIds": product_ids,
        "startValidityDate": start.replace("Z", "+00:00"),
        "endValidityDate": end.replace("Z", "+00:00"),
        "startValidityDateUTC": start,
        "endValidityDateUTC": end,
        "packaging": "Je 800 g",
        "pricePerUnit": "1 kg = 2.21",
    }


def leaflet(leaflet_id, name, identifier, offer_start, offer_end, pdf_version=1, regions=None):
    """A leaflet of the leaflet overview, the offer days are local dates; national without regions"""
    return {
        "id": leaflet_id,
        "name": name,
        "title": f"{offer_start} – {offer_end}",
        "pdfUrl": f"https://assets.example.invalid/{leaflet_id}-{pdf_version:02d}.pdf",
        "hiResPdfUrl": f"https://assets.example.invalid/{leaflet_id}-hires-{pdf_version:02d}.pdf",
        "thumbnailUrl": f"https://images.example.invalid/{leaflet_id}.jpg",
        "flyerUrlAbsolute": f"https://www.lidl.de/l/prospekte/{identifier}/ar/0?lf=HHZ",
        "flyerJson": f"https://endpoints.leaflets.schwarz/v4/flyer?flyer_identifier={identifier}&client=lidl",
        # Published before the offers start
        "startDate": "2026-01-01",
        "endDate": offer_end,
        "offerStartDate": offer_start,
        "offerEndDate": offer_end,
        "regions": (
            [{"type": "offer_region", "code": code} for code in regions]
            if regions
            else [{"type": "national", "code": "0"}]
        ),
    }


def leaflet_overview(*categories):
    """Response of the leaflet overview, categories as (name, leaflets)"""
    return {
        "success": True,
        "categories": [
            {
                "id": f"category-{index}",
                "name": name,
                "subcategories": [{"id": f"subcategory-{index}", "name": f"{name} aktuell", "flyers": flyers}],
            }
            for index, (name, flyers) in enumerate(categories)
        ],
    }


def flyer(*pages):
    """Response of the flyer endpoint, pages as (printed words, products) with products as (id, title, price)"""
    result = {"pages": [], "products": {}}
    for number, (text, products) in enumerate(pages, start=1):
        links = []
        for product_id, title, price in products:
            link_id = f"link-{number}-{product_id}"
            links.append({"id": link_id, "displayType": "product", "productDetails": {"productId": product_id}})
            result["products"][link_id] = {
                "productId": product_id,
                "title": title,
                "brand": "PARKSIDE",
                "price": price,
                "categoryPrimary": "Kategorien/Heimwerken",
                "canonicalUrl": f"/p/{product_id}",
                "url": f"https://www.lidl.de/p/{product_id}?flyx_source=pageflip",
                "image": f"https://www.lidl.de/assets/{product_id}.jpg",
                "description": "Farbe: gr&uuml;n",
            }
        result["pages"].append(
            {
                "number": number,
                "keyWords": text,
                "altText": f"[Seite {number}]",
                "image": f"https://images.example.invalid/page-{number}.jpg",
                "thumbnail": f"https://images.example.invalid/page-{number}-small.jpg",
                "links": links,
            }
        )
    return {"success": True, "flyer": result}


OPENING_HOURS = {
    "timezone": "Europe/Berlin",
    "regular": {
        **{day: [{"from": "07:00", "to": "22:00"}] for day in ("monday", "tuesday", "wednesday", "thursday", "friday")},
        "saturday": [{"from": "07:00", "to": "21:00"}],
        "sunday": [],
    },
    "special": [{"date": "2026-10-03", "timeRanges": []}],
}


def directory_page(stores=(), cities=()):
    """
    Data of a page of the store directory of lidl.de in the format of Nuxt: a list in which objects and lists
    refer to their values by index. stores as (object number, offer region, region name[, opening hours]),
    cities as (name, url).
    """
    payload = [{"data": 1}, ["ShallowReactive", 2], {}]

    def add(value):
        payload.append(value)
        return len(payload) - 1

    def add_value(value):
        if isinstance(value, dict):
            return add({key: add_value(child) for key, child in value.items()})
        if isinstance(value, list):
            return add([add_value(child) for child in value])
        return add(value)

    for object_number, region, name, *hours in stores:
        marketing = add(
            {"externalUrl": -1, "offerRegion": add(region), "offerRegionName": add(name), "zone": add("DE1")}
        )
        store = {"objectNumber": add(object_number), "storeName": add("Filiale"), "marketingData": marketing}
        if hours:
            store["generalOpeningHours"] = add_value(hours[0])
        add(store)
    for name, url in cities:
        add({"name": add(name), "numberOfStores": add(3), "federalState": add("Hessen"), "url": add(url)})
    return payload
