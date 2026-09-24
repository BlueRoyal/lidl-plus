"""Tests for the API client, the HTTP session is replaced so nothing goes to the network."""

# pylint: disable=protected-access,redefined-outer-name

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest
import requests

from lidlplus import LidlPlusApi
from lidlplus.exceptions import AuthenticationError, MissingLogin
from sample_data import Receipt, flyer, leaflet, leaflet_overview, offer, ticket

MILK_RECEIPT = Receipt().article("0001", "Milch", "1,09").build()

TOKEN_URL = "https://accounts.lidl.com/connect/token"
TICKETS_URL = "https://tickets.lidlplus.com/api/v2/DE/tickets"
TICKET_DETAIL_URL = "https://tickets.lidlplus.com/api/v3/DE/tickets/"
COUPONS_URL = "https://coupons.lidlplus.com/api/v2/DE"
PROMOTIONS_URL = "https://coupons.lidlplus.com/app/api//v1/promotionslist"


class FakeResponse:
    """Minimal stand-in for requests.Response"""

    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class FakeSession:
    """Answers requests with the handler registered last for the URL prefix."""

    def __init__(self):
        self.calls = []
        self.handlers = []

    def on(self, method, url_prefix, handler):
        self.handlers.append((method, url_prefix, handler))

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for handler_method, prefix, handler in reversed(self.handlers):
            if handler_method == method and url.startswith(prefix):
                result = handler(url, **kwargs)
                return result if isinstance(result, FakeResponse) else FakeResponse(payload=result)
        raise AssertionError(f"Unexpected request {method} {url}")

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def urls(self):
        return [call[1] for call in self.calls]


def token_response(**_):
    return FakeResponse(payload={"access_token": "access-1", "refresh_token": "refresh-2", "expires_in": 3600})


@pytest.fixture
def session():
    fake = FakeSession()
    fake.on("POST", TOKEN_URL, lambda url, **kwargs: token_response())
    return fake


@pytest.fixture
def api(session, tmp_path):
    lidl = LidlPlusApi("de", "de", refresh_token="refresh-1", cache_file=str(tmp_path / "cache.json"))
    lidl._session = session
    return lidl


def test_token_is_renewed_and_rotated(api, session):
    session.on("GET", "https://profile.lidlplus.com", lambda url, **kwargs: FakeResponse(text='"123456"\n'))
    assert api.loyalty_id() == "123456"
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("POST", TOKEN_URL)
    assert kwargs["data"] == {"refresh_token": "refresh-1", "grant_type": "refresh_token"}
    assert session.calls[1][1] == "https://profile.lidlplus.com/profile/api/v1/DE/loyalty"
    assert session.calls[1][2]["headers"]["Authorization"] == "Bearer access-1"
    assert api.refresh_token == "refresh-2"
    # The access token is reused until shortly before it expires
    api.loyalty_id()
    assert session.urls().count(TOKEN_URL) == 1
    api._expires = datetime.now(timezone.utc) + timedelta(seconds=30)
    api.loyalty_id()
    assert session.urls().count(TOKEN_URL) == 2


def test_refresh_token_is_kept_if_not_rotated(api, session):
    session.on("POST", TOKEN_URL, lambda url, **kwargs: {"access_token": "a", "expires_in": 3600})
    session.on("GET", COUPONS_URL, lambda url, **kwargs: {"sections": []})
    api.coupons()
    assert api.refresh_token == "refresh-1"


def test_rejected_refresh_token(api, session):
    session.on("POST", TOKEN_URL, lambda url, **kwargs: FakeResponse(400, {"error": "invalid_grant"}))
    with pytest.raises(AuthenticationError, match="invalid_grant"):
        api.coupons()


def test_auth_server_error_is_no_authentication_error(api, session):
    session.on("POST", TOKEN_URL, lambda url, **kwargs: FakeResponse(503, text="unavailable"))
    with pytest.raises(requests.HTTPError):
        api.coupons()


def test_missing_login(session):
    lidl = LidlPlusApi("de", "DE")
    lidl._session = session
    with pytest.raises(MissingLogin):
        lidl.coupons()
    assert not session.calls


def test_rejected_access_token_is_renewed_once(api, session):
    responses = iter([FakeResponse(401, {}), FakeResponse(payload={"sections": []})])
    session.on("GET", COUPONS_URL, lambda url, **kwargs: next(responses))
    assert api.coupons() == {"sections": []}
    assert session.urls().count(TOKEN_URL) == 2


def test_tickets_pagination(api, session):
    def tickets_page(url, params, **kwargs):
        return {"tickets": [{"id": f"p{params['pageNumber']}"}], "totalCount": 25, "size": 10}

    session.on("GET", TICKETS_URL, tickets_page)
    assert [entry["id"] for entry in api.tickets(only_favorite=True)] == ["p1", "p2", "p3"]
    params = [call[2]["params"] for call in session.calls if call[1] == TICKETS_URL]
    # The favorite filter has to be sent for every page
    assert params == [{"pageNumber": number, "onlyFavorite": "True"} for number in (1, 2, 3)]


def test_tickets_without_receipts(api, session):
    session.on("GET", TICKETS_URL, lambda url, **kwargs: {"tickets": [], "totalCount": 0, "size": 10})
    assert not api.tickets()


def test_sync_keeps_progress_after_error(api, session):
    session.on(
        "GET",
        TICKETS_URL,
        lambda url, **kwargs: {"tickets": [{"id": "t1"}, {"id": "t2"}], "totalCount": 2, "size": 10},
    )

    def detail(url, **kwargs):
        if url.endswith("/t2"):
            return FakeResponse(500, {})
        receipt = MILK_RECEIPT
        return {"id": "t1", "date": "2026-01-01T10:00:00+01:00", "totalAmount": "1,09", "htmlPrintedReceipt": receipt}

    session.on("GET", TICKET_DETAIL_URL, detail)
    with pytest.raises(requests.HTTPError):
        api.sync()
    cached = api.cached_tickets()
    assert [entry["id"] for entry in cached] == ["t1"]
    assert cached[0]["_items"][0]["name"] == "Milch"

    # The next sync only fetches the missing ticket
    session.on("GET", TICKET_DETAIL_URL, lambda url, **kwargs: {"id": "t2", "date": "2026-01-02", "totalAmount": 2})
    assert api.sync() == 1
    assert sorted(entry["id"] for entry in api.cached_tickets()) == ["t1", "t2"]
    assert session.urls().count(TICKET_DETAIL_URL + "t1") == 1


def test_sync_skips_broken_ticket(api, session):
    session.on(
        "GET",
        TICKETS_URL,
        lambda url, **kwargs: {"tickets": [{"id": "t1"}, {"id": "t2"}], "totalCount": 2, "size": 10},
    )

    def detail(url, **kwargs):
        if url.endswith("/t1"):
            return FakeResponse(404, {})
        return {"id": "t2", "date": "2026-01-02", "totalAmount": 2}

    session.on("GET", TICKET_DETAIL_URL, detail)
    # A receipt that cannot be loaded does not block the others and is tried again next time
    assert api.sync() == 1
    assert [entry["id"] for entry in api.cached_tickets()] == ["t2"]
    assert api.sync() == 0
    assert session.urls().count(TICKET_DETAIL_URL + "t1") == 2


def test_sync_stops_on_server_error(api, session):
    session.on("GET", TICKETS_URL, lambda url, **kwargs: {"tickets": [{"id": "t1"}], "totalCount": 1, "size": 10})
    session.on("GET", TICKET_DETAIL_URL, lambda url, **kwargs: FakeResponse(503, {}))
    with pytest.raises(requests.HTTPError):
        api.sync()


def test_corrupt_cache_is_moved_aside(api, tmp_path):
    cache = tmp_path / "cache.json"
    cache.write_text("{broken", encoding="utf-8")
    assert not api.cached_tickets()
    assert not cache.exists()
    # The broken file is kept with a timestamp in its name, so an older one is never replaced
    backups = list(tmp_path.glob("cache.json.corrupt-*"))
    assert [backup.read_text(encoding="utf-8") for backup in backups] == ["{broken"]


def test_cache_write_leaves_no_temporary_files(api, tmp_path):
    api._save_cache({"tickets": {"t1": ticket("t1", "2026-01-01", 1)}})
    assert [path.name for path in tmp_path.iterdir()] == ["cache.json"]
    assert api.cached_tickets()[0]["id"] == "t1"


def test_analytics_with_string_amounts(api):
    today = datetime.now().strftime("%Y-%m-%d")
    api._save_cache(
        {
            "tickets": {
                "t1": ticket("t1", f"{today}T10:00:00", "12,34"),
                "t2": ticket("t2", f"{today}T11:00:00", 0.66),
            }
        }
    )
    assert api.current_month_spending() == 13.0
    assert api.average_basket() == 6.5
    assert api.spending_by_store() == {"Lidl Musterstadt": 13.0}


def test_parse_ticket_items():
    items = LidlPlusApi.parse_ticket_items({"htmlPrintedReceipt": MILK_RECEIPT})
    assert items[0]["name"] == "Milch"
    assert not LidlPlusApi.parse_ticket_items({})


def test_activate_all_coupons(api, session):
    now = datetime.now(timezone.utc)
    past, future = (now - timedelta(days=1)).isoformat(), (now + timedelta(days=1)).isoformat()
    coupons = [
        {"id": "c1", "title": "Valid", "isActivated": False, "startValidityDate": past, "endValidityDate": future},
        {"id": "c2", "title": "Active", "isActivated": True, "startValidityDate": past, "endValidityDate": future},
        {"id": "c3", "title": "Expired", "isActivated": False, "startValidityDate": past, "endValidityDate": past},
        {"id": "c4", "title": "Future", "isActivated": False, "startValidityDate": future, "endValidityDate": future},
        {"id": "c5", "title": "Refused", "isActivated": False},
    ]
    promotion = {
        "promotionId": "p1",
        "title": "Promo",
        "isActivated": False,
        "validity": {"start": past, "end": future},
    }
    session.on("GET", COUPONS_URL, lambda url, **kwargs: {"sections": [{"coupons": coupons}]})
    session.on("GET", PROMOTIONS_URL, lambda url, **kwargs: {"sections": [{"promotions": [promotion]}]})
    session.on("POST", "https://coupons.lidlplus.com/api/v1/DE/c1/activation", lambda url, **kwargs: {})
    session.on(
        "POST", "https://coupons.lidlplus.com/api/v1/DE/c5/activation", lambda url, **kwargs: FakeResponse(409, {})
    )
    session.on(
        "POST",
        "https://coupons.lidlplus.com/app/api//v1/promotions/p1/activation",
        lambda url, **kwargs: FakeResponse(204, text=""),
    )
    assert api.activate_all_coupons() == {"activated": ["Valid", "Promo"], "failed": ["Refused"]}


def test_activate_all_coupons_without_api_v1(api, session):
    session.on("GET", COUPONS_URL, lambda url, **kwargs: {"sections": []})
    session.on("GET", PROMOTIONS_URL, lambda url, **kwargs: FakeResponse(404, {}))
    assert api.activate_all_coupons() == {"activated": [], "failed": []}


def test_activate_all_coupons_with_other_response_shapes(api, session):
    # A plain list instead of sections, and an unexpected answer of the optional API v1
    session.on("GET", COUPONS_URL, lambda url, **kwargs: [{"id": "c1", "title": "Listed"}])
    session.on("GET", PROMOTIONS_URL, lambda url, **kwargs: "maintenance")
    session.on("POST", "https://coupons.lidlplus.com/api/v1/DE/c1/activation", lambda url, **kwargs: {})
    assert api.activate_all_coupons() == {"activated": ["Listed"], "failed": []}


def test_cache_of_older_version_is_parsed_again(api, session, tmp_path):
    html = Receipt().weighed("0082345", "Zucchini", "1,29", "0,786", "1,01").build()
    # Version 0.4 counted the weight line as a second purchase
    old_items = [{"id": "0082345", "name": "Zucchini", "unit_price": "1,29", "quantity": 0.786, "tax_type": "A"}] * 2
    old_cache = {"tickets": {"t1": {"id": "t1", "date": "2026-09-01", "htmlPrintedReceipt": html, "_items": old_items}}}
    (tmp_path / "cache.json").write_text(json.dumps(old_cache), encoding="utf-8")

    items = api.cached_tickets()[0]["_items"]
    assert [(entry["name"], entry["total"], entry["unit"], entry["tax_rate"]) for entry in items] == [
        ("Zucchini", 1.01, "kg", 7.0)
    ]

    # The next sync saves the result, also without new receipts
    session.on("GET", TICKETS_URL, lambda url, **kwargs: {"tickets": [{"id": "t1"}], "totalCount": 1, "size": 10})
    assert api.sync() == 0
    saved = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
    assert saved["items_version"] == 2
    assert "upgraded" not in saved
    assert saved["tickets"]["t1"]["_receipt"]["vat"][0]["rate"] == 7.0
    assert saved["tickets"]["t1"]["htmlPrintedReceipt"] == html


def test_search_stores(api, session):
    stores = [{"storeKey": "DE1234", "name": "Musterstadt", "postalCode": "12345"}]
    session.on("GET", "https://stores.lidlplus.com/api/v1/autocomplete/DE", lambda url, **kwargs: stores)
    assert api.search_stores("12345", 52.5, 13.4) == stores
    kwargs = session.calls[-1][2]
    assert kwargs["params"] == {"input": "12345", "language": "de", "latitude": 52.5, "longitude": 13.4}
    # Public endpoint: no login, but the headers of the app
    assert "Authorization" not in kwargs["headers"]
    assert kwargs["headers"]["X-Client-Platform"] == "android"
    assert TOKEN_URL not in session.urls()


def test_sync_offers_keeps_history(api, session, tmp_path):
    offers_url = "https://offers.lidlplus.com/app/api/v4/DE/DE1234/offers"
    first = offer("o1", "Kaffee", ["0080100"], "2026-09-20T22:00:00Z", "2026-09-26T21:59:59Z")
    second = offer("o2", "Brot", ["0082052"], "2026-09-27T22:00:00Z", "2026-10-03T21:59:59Z")
    session.on("GET", offers_url, lambda url, **kwargs: {"offers": [first], "totalOffers": 1})
    assert api.sync_offers(["DE1234"]) == 1
    session.on("GET", offers_url, lambda url, **kwargs: {"offers": [second], "totalOffers": 1})
    assert api.sync_offers(["DE1234"]) == 1

    # The first offer is not returned anymore, but stays in the cache
    offers = {entry["id"]: entry for entry in api.cached_offers()}
    assert sorted(offers) == ["o1", "o2"]
    assert offers["o1"]["stores"] == ["DE1234"]
    assert offers["o1"]["first_seen"] <= offers["o1"]["last_seen"]
    saved = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
    assert saved["offers"]["o2"]["offer"]["title"] == "Brot"


def test_cached_data(api, tmp_path):
    (tmp_path / "cache.json").write_text(json.dumps({"tickets": {}, "items_version": 1}), encoding="utf-8")
    assert api.cached_data() == {"tickets": {}, "items_version": 2}


LEAFLETS_URL = "https://endpoints.leaflets.schwarz/v4/overview"
FLYER_URL = "https://endpoints.leaflets.schwarz/v4/flyer"


def loaded_flyers(session):
    return [call[2]["params"]["flyer_identifier"] for call in session.calls if call[1] == FLYER_URL]


def test_sync_leaflets(api, session, tmp_path, caplog):
    today = datetime.now().date()
    current = leaflet(
        "l1", "Aktionsprospekt", "aktion-1", str(today - timedelta(days=1)), str(today + timedelta(days=4))
    )
    expired = leaflet("l2", "Preisführer", "preis", str(today - timedelta(days=90)), str(today - timedelta(days=30)))
    upcoming = leaflet(
        "l3", "Aktionsprospekt", "aktion-2", str(today + timedelta(days=6)), str(today + timedelta(days=11))
    )
    overview = {"response": leaflet_overview(("Filial-Angebote", [current, expired]))}
    session.on("GET", LEAFLETS_URL, lambda url, **kwargs: overview["response"])
    pages = flyer(("Kaffee", [("100001", "Akku-Bohrschrauber", "39.99")]))
    session.on("GET", FLYER_URL, lambda url, **kwargs: pages)

    assert api.sync_leaflets() == 2
    assert session.calls[0][2]["params"] == {"client_locale": "lidl/de-DE", "region_id": 0}
    # Leaflets need no login
    assert TOKEN_URL not in session.urls()
    # Only the leaflet that has not ended is loaded with its pages and products
    assert loaded_flyers(session) == ["aktion-1"]

    # Known leaflets are not loaded again, new ones are; a failed leaflet is tried again with the next sync
    overview["response"] = leaflet_overview(("Filial-Angebote", [current, upcoming]))
    session.on("GET", FLYER_URL, lambda url, **kwargs: FakeResponse(status_code=503))
    assert api.sync_leaflets() == 1
    assert "aktion-2" in caplog.text
    session.on("GET", FLYER_URL, lambda url, **kwargs: pages)
    session.calls.clear()
    assert api.sync_leaflets() == 0
    assert loaded_flyers(session) == ["aktion-2"]

    # A corrected leaflet comes with a new PDF file and is loaded again
    overview["response"] = leaflet_overview(("Filial-Angebote", [{**current, "hiResPdfUrl": "https://new.pdf"}]))
    session.calls.clear()
    assert api.sync_leaflets() == 0
    assert loaded_flyers(session) == ["aktion-1"]

    leaflets = {entry["id"]: entry for entry in api.cached_leaflets()}
    assert sorted(leaflets) == ["l1", "l2", "l3"]
    assert leaflets["l2"]["status"] == "expired"
    assert not leaflets["l2"]["products"]
    assert leaflets["l1"]["pdf"] == "https://new.pdf"
    assert leaflets["l1"]["products"][0]["pages"] == [1]
    saved = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
    assert saved["leaflets"]["l1"]["details_of"] == "https://new.pdf"


def test_upcoming_leaflets_are_loaded_again(api, session, tmp_path):
    today = datetime.now().date()
    current = leaflet(
        "l1", "Aktionsprospekt", "aktion-1", str(today - timedelta(days=1)), str(today + timedelta(days=4))
    )
    upcoming = leaflet(
        "l3", "Aktionsprospekt", "aktion-2", str(today + timedelta(days=6)), str(today + timedelta(days=11))
    )
    session.on("GET", LEAFLETS_URL, lambda url, **kwargs: leaflet_overview(("Filial-Angebote", [current, upcoming])))
    answer = {"flyer": flyer(("Kaffee", []))}
    session.on("GET", FLYER_URL, lambda url, **kwargs: answer["flyer"])
    api.sync_leaflets()
    session.calls.clear()
    api.sync_leaflets()
    assert not loaded_flyers(session), "loaded less than a day ago"

    def sync_a_day_later():
        cache_file = tmp_path / "cache.json"
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
        for entry in cache["leaflets"].values():
            entry["details_loaded"] = "2020-01-01T00:00:00+00:00"
        cache_file.write_text(json.dumps(cache), encoding="utf-8")
        session.calls.clear()
        api.sync_leaflets()
        return loaded_flyers(session)

    def pages_of_upcoming_leaflet():
        return [len(entry["pages"]) for entry in api.cached_leaflets() if entry["id"] == "l3"][0]

    # Until their offers start leaflets may still get pages and products, the current one is complete
    answer["flyer"] = flyer(("Kaffee", []), ("Tee", [("100003", "Wasserkocher", "19.99")]))
    assert sync_a_day_later() == ["aktion-2"]
    assert pages_of_upcoming_leaflet() == 2
    # An empty answer keeps the pages loaded before
    answer["flyer"] = {"success": False}
    assert sync_a_day_later() == ["aktion-2"]
    assert pages_of_upcoming_leaflet() == 2


def test_sync_leaflets_of_some_categories(api, session):
    today = datetime.now().date()
    travel = leaflet("l1", "Reisen", "reisen", str(today), str(today + timedelta(days=30)))
    session.on("GET", LEAFLETS_URL, lambda url, **kwargs: leaflet_overview(("Lidl-Reisen", [travel])))
    assert api.sync_leaflets(categories=("Filial-Angebote",)) == 1
    assert FLYER_URL not in session.urls()
    assert api.cached_leaflets()[0]["name"] == "Reisen"


def test_leaflet(api, session):
    session.on("GET", FLYER_URL, lambda url, **kwargs: {"success": False})
    assert api.leaflet("unknown") == {}


def test_broken_leaflet_answers(api, session, tmp_path, caplog):
    today = datetime.now().date()
    good = leaflet("l1", "Aktionsprospekt", "aktion-1", str(today), str(today + timedelta(days=5)))
    broken = leaflet("l2", "Sonderprospekt", "sonder", str(today), str(today + timedelta(days=5)))
    empty = leaflet("l3", "Reisen", "reisen", str(today), str(today + timedelta(days=30)))
    session.on("GET", LEAFLETS_URL, lambda url, **kwargs: leaflet_overview(("Filial-Angebote", [good, broken, empty])))
    answers = {"aktion-1": flyer(("Kaffee", [])), "sonder": [1, 2], "reisen": {"success": False}}
    session.on("GET", FLYER_URL, lambda url, params, **kwargs: answers[params["flyer_identifier"]])

    # A leaflet with an unexpected answer does not stop the others
    assert api.sync_leaflets() == 3
    assert "sonder" in caplog.text
    leaflets = {entry["id"]: entry for entry in api.cached_leaflets()}
    assert len(leaflets["l1"]["pages"]) == 1
    # Leaflets without pages are loaded again with the next sync, the good one is complete
    session.calls.clear()
    answers["reisen"] = flyer(("Urlaub", []))
    api.sync_leaflets()
    assert sorted(loaded_flyers(session)) == ["reisen", "sonder"]
    assert len({entry["id"]: entry for entry in api.cached_leaflets()}["l3"]["pages"]) == 1


def test_public_data_is_only_saved_when_it_changed(api, session, monkeypatch):
    today = datetime.now().date()
    current = leaflet("l1", "Aktionsprospekt", "aktion-1", str(today), str(today + timedelta(days=5)))
    overview = {"response": leaflet_overview(("Filial-Angebote", [current]))}
    session.on("GET", LEAFLETS_URL, lambda url, **kwargs: overview["response"])
    session.on("GET", FLYER_URL, lambda url, **kwargs: flyer(("Kaffee", [])))
    offers_url = "https://offers.lidlplus.com/app/api/v4/DE/DE1234/offers"
    offers = {"offers": [offer("o1", "Kaffee", ["1"], "2026-09-20T22:00:00Z", "2099-01-01T00:00:00Z")]}
    session.on("GET", offers_url, lambda url, **kwargs: offers)
    api.sync_leaflets()
    api.sync_offers(["DE1234"])

    saves = []
    original_save = api._save_cache
    monkeypatch.setattr(api, "_save_cache", lambda cache: saves.append(True) or original_save(cache))
    # Nothing new on the same day: the (large) cache file is not written again
    api.sync_leaflets()
    api.sync_offers(["DE1234"])
    assert not saves
    offers["offers"] = offers["offers"] + [offer("o2", "Tee", ["2"], "2026-09-20T22:00:00Z", "2099-01-01T00:00:00Z")]
    assert api.sync_offers(["DE1234"]) == 1
    overview["response"] = leaflet_overview(("Filial-Angebote", [{**current, "title": "Korrigiert"}]))
    api.sync_leaflets()
    assert len(saves) == 2
    assert api.cached_leaflets()[0]["title"] == "Korrigiert"


def test_syncs_at_the_same_time_keep_each_others_changes(api, session):
    """Home Assistant can start a manual refresh while the scheduled one is still running"""
    today = datetime.now().date()
    current = leaflet("l1", "Aktionsprospekt", "aktion-1", str(today), str(today + timedelta(days=5)))
    session.on("GET", LEAFLETS_URL, lambda url, **kwargs: leaflet_overview(("Filial-Angebote", [current])))
    leaflet_started, go_on = threading.Event(), threading.Event()

    def slow_flyer(url, **kwargs):
        leaflet_started.set()
        go_on.wait(5)
        return flyer(("Kaffee", []))

    session.on("GET", FLYER_URL, slow_flyer)
    session.on("GET", TICKETS_URL, lambda url, **kwargs: {"tickets": [{"id": "t1"}], "totalCount": 1, "size": 10})
    session.on(
        "GET",
        TICKET_DETAIL_URL,
        lambda url, **kwargs: {
            "id": "t1",
            "date": "2026-09-20",
            "totalAmount": "1,09",
            "htmlPrintedReceipt": MILK_RECEIPT,
        },
    )
    leaflets = threading.Thread(target=api.sync_leaflets)
    leaflets.start()
    assert leaflet_started.wait(5)
    tickets = threading.Thread(target=api.sync)
    tickets.start()
    # The receipt sync waits for the leaflet sync, which would otherwise save its older copy of the cache last
    tickets.join(0.5)
    go_on.set()
    leaflets.join(5)
    tickets.join(5)
    assert [entry["id"] for entry in api.cached_tickets()] == ["t1"]
    assert [entry["id"] for entry in api.cached_leaflets()] == ["l1"]
