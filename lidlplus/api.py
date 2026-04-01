"""
Lidl Plus api
"""

import base64
import html
import json
import logging
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import requests

from lidlplus.exceptions import (
    WebBrowserException,
    LoginError,
    LegalTermsException,
    MissingLogin,
)

try:
    from getuseragent import UserAgent
    from oic.oic import Client
    from oic.utils.authn.client import CLIENT_AUTHN_METHOD
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions
    from selenium.webdriver.support.ui import WebDriverWait
    from seleniumwire import webdriver
    from seleniumwire.utils import decode
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.firefox import GeckoDriverManager
    from webdriver_manager.core.os_manager import ChromeType
except ImportError:
    pass


class LidlPlusApi:
    """Lidl Plus api connector"""

    _CLIENT_ID = "LidlPlusNativeClient"
    _AUTH_API = "https://accounts.lidl.com"
    _TICKET_API = "https://tickets.lidlplus.com/api/v2"
    _TICKET_DETAIL_API = "https://tickets.lidlplus.com/api/v3"
    _COUPONS_API = "https://coupons.lidlplus.com/api"
    _COUPONS_V1_API = "https://coupons.lidlplus.com/app/api/"
    _PROFILE_API = "https://profile.lidlplus.com/profile/api"
    _APP = "com.lidlplus.app"
    _OS = "iOs"
    _TIMEOUT = 120

    def __init__(self, language, country, refresh_token="", cache_file=None):
        self._login_url = ""
        self._code_verifier = ""
        self._refresh_token = refresh_token
        self._expires = None
        self._token = ""
        self._country = country.upper()
        self._language = language.lower()
        self._cache_file = cache_file

    @property
    def refresh_token(self):
        """Lidl Plus api refresh token"""
        return self._refresh_token

    @property
    def token(self):
        """Current token to query api"""
        return self._token

    def _register_oauth_client(self):
        if self._login_url:
            return self._login_url
        client = Client(client_authn_method=CLIENT_AUTHN_METHOD, client_id=self._CLIENT_ID)
        client.provider_config(self._AUTH_API)
        code_challenge, self._code_verifier = client.add_code_challenge()
        args = {
            "client_id": client.client_id,
            "response_type": "code",
            "scope": ["openid profile offline_access lpprofile lpapis"],
            "redirect_uri": f"{self._APP}://callback",
            **code_challenge,
        }
        auth_req = client.construct_AuthorizationRequest(request_args=args)
        self._login_url = auth_req.request(client.authorization_endpoint)
        return self._login_url

    def _init_chrome(self, headless=True):
        user_agent = UserAgent(self._OS.lower()).Random()
        logging.getLogger("WDM").setLevel(logging.NOTSET)
        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument("headless")
        options.add_experimental_option("mobileEmulation", {"userAgent": user_agent})
        for chrome_type in [ChromeType.GOOGLE, ChromeType.MSEDGE, ChromeType.CHROMIUM]:
            try:
                service = Service(ChromeDriverManager(chrome_type=chrome_type).install())
                return webdriver.Chrome(service=service, options=options)
            except AttributeError:
                continue
        raise WebBrowserException("Unable to find a suitable Chrome driver")

    def _init_firefox(self, headless=True):
        user_agent = UserAgent(self._OS.lower()).Random()
        logging.getLogger("WDM").setLevel(logging.NOTSET)
        options = webdriver.FirefoxOptions()
        if headless:
            options.headless = True
        profile = webdriver.FirefoxProfile()
        profile.set_preference("general.useragent.override", user_agent)
        return webdriver.Firefox(
            executable_path=GeckoDriverManager().install(),
            firefox_binary="/usr/bin/firefox",
            options=options,
            firefox_profile=profile,
        )

    def _get_browser(self, headless=True):
        try:
            return self._init_chrome(headless=headless)
        # pylint: disable=broad-except
        except Exception as exc1:
            try:
                return self._init_firefox(headless=headless)
            except Exception as exc2:
                raise WebBrowserException from exc1 and exc2

    def _auth(self, payload):
        default_secret = base64.b64encode(f"{self._CLIENT_ID}:secret".encode()).decode()
        headers = {
            "Authorization": f"Basic {default_secret}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        kwargs = {"headers": headers, "data": payload, "timeout": self._TIMEOUT}
        response = requests.post(f"{self._AUTH_API}/connect/token", **kwargs).json()
        self._expires = datetime.utcnow() + timedelta(seconds=response["expires_in"])
        self._token = response["access_token"]
        self._refresh_token = response["refresh_token"]

    def _renew_token(self):
        payload = {"refresh_token": self._refresh_token, "grant_type": "refresh_token"}
        return self._auth(payload)

    def _authorization_code(self, code):
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": f"{self._APP}://callback",
            "code_verifier": self._code_verifier,
        }
        return self._auth(payload)

    @property
    def _register_link(self):
        args = {
            "Country": self._country,
            "language": f"{self._language}-{self._country}",
        }
        params = "&".join([f"{key}={value}" for key, value in args.items()])
        return f"{self._register_oauth_client()}&{params}"

    @staticmethod
    def _accept_legal_terms(browser, wait, accept=True):
        wait.until(expected_conditions.visibility_of_element_located((By.ID, "checkbox_Accepted"))).click()
        if not accept:
            title = browser.find_element(By.TAG_NAME, "h2").text
            raise LegalTermsException(title)
        browser.find_element(By.TAG_NAME, "button").click()

    def _parse_code(self, browser, wait, accept_legal_terms=True):
        for request in reversed(browser.requests):
            if f"{self._AUTH_API}/connect" not in request.url:
                continue
            location = request.response.headers.get("Location", "")
            if "legalTerms" in location:
                self._accept_legal_terms(browser, wait, accept=accept_legal_terms)
                return self._parse_code(browser, wait, False)
            if code := re.findall("code=([0-9A-F]+)", location):
                return code[0]
        return ""

    def _click(self, browser, button, request=""):
        del browser.requests
        browser.backend.storage.clear_requests()
        browser.find_element(*button).click()
        self._check_input_error(browser)
        if request and browser.wait_for_request(request, 10):
            self._check_input_error(browser)

    @staticmethod
    def _check_input_error(browser):
        if errors := browser.find_elements(By.CLASS_NAME, "input-error-message"):
            for error in errors:
                if error.text:
                    raise LoginError(error.text)

    def _check_login_error(self, browser):
        response = browser.wait_for_request(f"{self._AUTH_API}/Account/Login.*", 10).response
        body = html.unescape(decode(response.body, response.headers.get("Content-Encoding", "identity")).decode())
        if error := re.findall('app-errors="\\{[^:]*?:.(.*?).}', body):
            raise LoginError(error[0])

    def _check_2fa_auth(self, browser, wait, verify_mode="phone", verify_token_func=None):
        if verify_mode not in ["phone", "email"]:
            raise ValueError(f'Unknown 2fa-mode "{verify_mode}" - Only "phone" or "email" supported')
        response = browser.wait_for_request(f"{self._AUTH_API}/Account/Login.*", 10).response
        if "/connect/authorize/callback" not in (response.headers.get("Location") or ""):
            element = wait.until(expected_conditions.visibility_of_element_located((By.CLASS_NAME, verify_mode)))
            element.find_element(By.TAG_NAME, "button").click()
            verify_code = verify_token_func()
            browser.find_element(By.NAME, "VerificationCode").send_keys(verify_code)
            self._click(browser, (By.CLASS_NAME, "role_next"))

    def login(self, email, password, **kwargs):
        """Simulate app auth"""
        browser = self._get_browser(headless=kwargs.get("headless", True))
        browser.get(self._register_link)
        wait = WebDriverWait(browser, 10)
        wait.until(expected_conditions.visibility_of_element_located((By.XPATH, '//*[@id="duple-button-block"]/button[1]/span'))).click()
        #wait.until(expected_conditions.visibility_of_element_located((By.NAME, "EmailOrPhone"))).send_keys(phone)
        wait.until(expected_conditions.element_to_be_clickable((By.NAME, "input-email"))).send_keys(email)
        wait.until(expected_conditions.element_to_be_clickable((By.NAME, "Password"))).send_keys(password)
        self._click(browser, (By.XPATH, '//*[@id="duple-button-block"]/button'))
        self._check_login_error(browser)
        self._check_2fa_auth(
            browser,
            wait,
            kwargs.get("verify_mode", "phone"),
            kwargs.get("verify_token_func"),
        )
        browser.wait_for_request(f"{self._AUTH_API}/connect.*")
        code = self._parse_code(browser, wait, accept_legal_terms=kwargs.get("accept_legal_terms", True))
        self._authorization_code(code)

    def _default_headers(self):
        if (not self._token and self._refresh_token) or datetime.utcnow() >= self._expires:
            self._renew_token()
        if not self._token:
            raise MissingLogin("You need to login!")
        return {
            "Authorization": f"Bearer {self._token}",
            "App-Version": "16.46.4",
            "Operating-System": self._OS,
            "App": "com.lidl.eci.lidl.plus",
            "Accept-Language": self._language,
        }

    def tickets(self, only_favorite=False):
        """
        Get a list of all tickets.

        :param onlyFavorite: A boolean value indicating whether to only retrieve favorite tickets.
            If set to True, only favorite tickets will be returned.
            If set to False (the default), all tickets will be retrieved.
        :type onlyFavorite: bool
        """
        url = f"{self._TICKET_API}/{self._country}/tickets"
        kwargs = {"headers": self._default_headers(), "timeout": self._TIMEOUT}
        ticket = requests.get(f"{url}?pageNumber=1&onlyFavorite={only_favorite}", **kwargs).json()
        tickets = ticket["tickets"]
        for i in range(2, int(ticket["totalCount"] / ticket["size"] + 2)):
            tickets += requests.get(f"{url}?pageNumber={i}", **kwargs).json()["tickets"]
        return tickets

    def ticket(self, ticket_id):
        """Get full data of single ticket by id"""
        kwargs = {"headers": self._default_headers(), "timeout": self._TIMEOUT}
        url = f"{self._TICKET_DETAIL_API}/{self._country}/tickets"
        return requests.get(f"{url}/{ticket_id}", **kwargs).json()

    @staticmethod
    def parse_ticket_items(ticket):
        """Parse HTML receipt from ticket and return items as structured list"""
        from html.parser import HTMLParser

        class _Parser(HTMLParser):
            def __init__(self):
                super().__init__()
                self._seen = set()
                self.items = []

            def handle_starttag(self, tag, attrs):
                if tag != "span":
                    return
                a = dict(attrs)
                span_id = a.get("id", "")
                if span_id in self._seen:
                    return
                if "article" in a.get("class", "").split() and a.get("data-art-id"):
                    self._seen.add(span_id)
                    self.items.append({
                        "id": a["data-art-id"],
                        "name": a.get("data-art-description", ""),
                        "unit_price": a.get("data-unit-price", ""),
                        "quantity": int(a.get("data-art-quantity", "1")),
                        "tax_type": a.get("data-tax-type", ""),
                    })

        p = _Parser()
        p.feed(ticket.get("htmlPrintedReceipt", ""))
        return p.items

    # --- Cache ---

    def _load_cache(self):
        if not self._cache_file or not os.path.exists(self._cache_file):
            return {"tickets": {}}
        with open(self._cache_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_cache(self, cache):
        if not self._cache_file:
            return
        with open(self._cache_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

    def sync(self):
        """Fetch only new tickets and add them to the cache. Returns count of new tickets."""
        cache = self._load_cache()
        cached_ids = set(cache["tickets"].keys())
        all_refs = self.tickets()
        new_refs = [t for t in all_refs if t["id"] not in cached_ids]
        for ref in new_refs:
            ticket = self.ticket(ref["id"])
            ticket["_items"] = self.parse_ticket_items(ticket)
            cache["tickets"][ref["id"]] = ticket
        cache["last_updated"] = datetime.utcnow().isoformat()
        self._save_cache(cache)
        return len(new_refs)

    def cached_tickets(self):
        """Return all tickets from cache as list."""
        return list(self._load_cache()["tickets"].values())

    # --- Analytics ---

    @staticmethod
    def _parse_date(date_str):
        return datetime.fromisoformat(date_str.split("+")[0].split("Z")[0])

    def all_ticket_items(self):
        """All items across all tickets as flat list with date and store."""
        result = []
        for ticket in self.cached_tickets():
            date = ticket.get("date", "")
            store = ticket.get("store", {}).get("name", "")
            ticket_id = ticket.get("id", "")
            for item in ticket.get("_items", []):
                result.append({**item, "date": date, "store": store, "ticket_id": ticket_id})
        return result

    def price_history(self, item_id):
        """Price changes for a specific item across all receipts."""
        items = [i for i in self.all_ticket_items() if i["id"] == item_id]
        return sorted(items, key=lambda x: x["date"])

    def frequently_bought(self, limit=10):
        """Top N most frequently bought items by total quantity."""
        counts = Counter()
        names = {}
        for item in self.all_ticket_items():
            counts[item["id"]] += item["quantity"]
            names[item["id"]] = item["name"]
        return [
            {"id": iid, "name": names[iid], "total_quantity": qty}
            for iid, qty in counts.most_common(limit)
        ]

    def spending_by_month(self):
        """Total spending grouped by month (YYYY-MM)."""
        result = defaultdict(float)
        for ticket in self.cached_tickets():
            month = ticket.get("date", "")[:7]
            result[month] = round(result[month] + ticket.get("totalAmount", 0), 2)
        return dict(sorted(result.items()))

    def spending_by_store(self):
        """Total spending grouped by store name."""
        result = defaultdict(float)
        for ticket in self.cached_tickets():
            store = ticket.get("store", {}).get("name", "Unknown")
            result[store] = round(result[store] + ticket.get("totalAmount", 0), 2)
        return dict(sorted(result.items(), key=lambda x: x[1], reverse=True))

    def last_seen(self, item_id):
        """Last purchase info for a specific item id."""
        items = [i for i in self.all_ticket_items() if i["id"] == item_id]
        if not items:
            return None
        return max(items, key=lambda x: x["date"])

    def current_month_spending(self):
        """Total spending in the current calendar month."""
        month = datetime.utcnow().strftime("%Y-%m")
        return self.spending_by_month().get(month, 0.0)

    def average_basket(self):
        """Average total amount per shopping trip."""
        tickets = self.cached_tickets()
        if not tickets:
            return 0.0
        return round(sum(t.get("totalAmount", 0) for t in tickets) / len(tickets), 2)

    def shopping_frequency_days(self):
        """Average number of days between shopping trips."""
        tickets = self.cached_tickets()
        if len(tickets) < 2:
            return None
        dates = sorted(self._parse_date(t["date"]) for t in tickets)
        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        return round(sum(gaps) / len(gaps), 1)

    def restock_suggestions(self, min_purchases=3):
        """Items overdue for restocking based on average purchase interval."""
        item_dates = defaultdict(list)
        item_names = {}
        for item in self.all_ticket_items():
            item_dates[item["id"]].append(item["date"])
            item_names[item["id"]] = item["name"]
        now = datetime.utcnow()
        suggestions = []
        for item_id, dates in item_dates.items():
            if len(dates) < min_purchases:
                continue
            parsed = sorted(self._parse_date(d) for d in dates)
            intervals = [(parsed[i + 1] - parsed[i]).days for i in range(len(parsed) - 1)]
            avg_interval = sum(intervals) / len(intervals)
            days_since_last = (now - parsed[-1]).days
            overdue_by = days_since_last - avg_interval
            if overdue_by > 0:
                suggestions.append({
                    "id": item_id,
                    "name": item_names[item_id],
                    "avg_interval_days": round(avg_interval, 1),
                    "days_since_last": days_since_last,
                    "overdue_by_days": round(overdue_by, 1),
                })
        return sorted(suggestions, key=lambda x: x["overdue_by_days"], reverse=True)

    def coupon_promotions_v1(self):
        """Get list of all coupons API V1"""
        url = f"{self._COUPONS_V1_API}/v1/promotionslist"
        kwargs = {"headers": {**self._default_headers(), "Country": self._country}, "timeout": self._TIMEOUT}
        return requests.get(url, **kwargs).json()

    def activate_coupon_promotion_v1(self, promotion_id):
        """Activate single coupon by id API V1"""
        url = f"{self._COUPONS_V1_API}/v1/promotions/{promotion_id}/activation"
        kwargs = {"headers": {**self._default_headers(), "Country": self._country}, "timeout": self._TIMEOUT}
        return requests.post(url, **kwargs)

    def coupons(self):
        """Get list of all coupons"""
        url = f"{self._COUPONS_API}/v2/{self._country}"
        kwargs = {"headers": self._default_headers(), "timeout": self._TIMEOUT}
        return requests.get(url, **kwargs).json()

    def activate_coupon(self, coupon_id):
        """Activate single coupon by id"""
        url = f"{self._COUPONS_API}/v1/{self._country}/{coupon_id}/activation"
        kwargs = {"headers": self._default_headers(), "timeout": self._TIMEOUT}
        return requests.post(url, **kwargs).json()

    def deactivate_coupon(self, coupon_id):
        """Deactivate single coupon by id"""
        url = f"{self._COUPONS_API}/v1/{self._country}/{coupon_id}/activation"
        kwargs = {"headers": self._default_headers(), "timeout": self._TIMEOUT}
        return requests.delete(url, **kwargs).json()

    def loyalty_id(self):
        """Get your loyalty ID"""
        url = f"{self._PROFILE_API}/v1/{self._country}/loyalty"
        kwargs = {"headers": self._default_headers(), "timeout": self._TIMEOUT}
        response = requests.get(url, **kwargs)
        response.raise_for_status()
        return response.text
