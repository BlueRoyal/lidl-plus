"""
Lidl Plus api
"""

import base64
import html
import json
import logging
import math
import os
import re
import tempfile
import threading
from datetime import datetime, timedelta, timezone

import requests

from . import analytics
from .exceptions import (
    AuthenticationError,
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
    from selenium.webdriver.firefox.service import Service as FirefoxService
    from selenium.webdriver.support import expected_conditions
    from selenium.webdriver.support.ui import WebDriverWait
    from seleniumwire import webdriver
    from seleniumwire.utils import decode
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.firefox import GeckoDriverManager
    from webdriver_manager.core.os_manager import ChromeType
except ImportError:
    pass

_LOGGER = logging.getLogger(__name__)


class LidlPlusApi:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """Lidl Plus api connector"""

    _CLIENT_ID = "LidlPlusNativeClient"
    _AUTH_API = "https://accounts.lidl.com"
    _TICKET_API = "https://tickets.lidlplus.com/api/v2"
    _TICKET_DETAIL_API = "https://tickets.lidlplus.com/api/v3"
    _COUPONS_API = "https://coupons.lidlplus.com/api"
    _COUPONS_V1_API = "https://coupons.lidlplus.com/app/api/"
    _PROFILE_API = "https://profile.lidlplus.com/profile/api"
    _STORES_API = "https://stores.lidlplus.com/api"
    _OFFERS_API = "https://offers.lidlplus.com/app/api"
    # App version sent to the public store and offer endpoints
    _PUBLIC_APP_VERSION = "17.0.5"
    _LEAFLETS_API = "https://endpoints.leaflets.schwarz/v4"
    # Upcoming leaflets may still be incomplete and are loaded again after this time
    _LEAFLET_RELOAD_INTERVAL = timedelta(hours=20)
    # The public store directory of lidl.de tells the offer region of a store, it is checked again after this time
    _STORE_DIRECTORY = "https://www.lidl.de/s/de-DE/filialen"
    _OFFER_REGION_RECHECK = timedelta(days=30)
    _APP = "com.lidlplus.app"
    _OS = "iOs"
    _TIMEOUT = 120
    # Renew the access token shortly before it expires
    _TOKEN_EXPIRY_MARGIN = timedelta(seconds=60)
    # Responses of the receipt detail API that concern only this receipt
    _SKIPPED_TICKET_ERRORS = (400, 404, 410)

    def __init__(self, language, country, refresh_token="", cache_file=None):
        self._login_url = ""
        self._code_verifier = ""
        self._refresh_token = refresh_token
        self._expires = None
        self._token = ""
        self._country = country.upper()
        self._language = language.lower()
        self._cache_file = cache_file
        self._session = requests.Session()
        self._token_lock = threading.Lock()
        # Two syncs at the same time would overwrite each other's changes of the cache
        self._cache_lock = threading.RLock()

    @property
    def refresh_token(self):
        """Lidl Plus api refresh token (changes when the auth server rotates it)"""
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
        last_error = None
        for chrome_type in [ChromeType.GOOGLE, ChromeType.MSEDGE, ChromeType.CHROMIUM]:
            try:
                service = Service(ChromeDriverManager(chrome_type=chrome_type).install())
                return webdriver.Chrome(service=service, options=options)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                last_error = exc
        raise WebBrowserException("Unable to find a suitable Chrome driver") from last_error

    def _init_firefox(self, headless=True):
        user_agent = UserAgent(self._OS.lower()).Random()
        logging.getLogger("WDM").setLevel(logging.NOTSET)
        options = webdriver.FirefoxOptions()
        if headless:
            options.add_argument("-headless")
        options.set_preference("general.useragent.override", user_agent)
        service = FirefoxService(GeckoDriverManager().install())
        return webdriver.Firefox(service=service, options=options)

    def _get_browser(self, headless=True):
        try:
            return self._init_chrome(headless=headless)
        except Exception as chrome_error:  # pylint: disable=broad-exception-caught
            try:
                return self._init_firefox(headless=headless)
            except Exception as firefox_error:  # pylint: disable=broad-exception-caught
                raise WebBrowserException(f"Chrome: {chrome_error} / Firefox: {firefox_error}") from firefox_error

    def _auth(self, payload):
        default_secret = base64.b64encode(f"{self._CLIENT_ID}:secret".encode()).decode()
        headers = {
            "Authorization": f"Basic {default_secret}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        kwargs = {"headers": headers, "data": payload, "timeout": self._TIMEOUT}
        response = self._session.post(f"{self._AUTH_API}/connect/token", **kwargs)
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code in (400, 401):
            raise AuthenticationError(
                data.get("error_description") or data.get("error") or f"HTTP {response.status_code}"
            )
        response.raise_for_status()
        self._expires = datetime.now(timezone.utc) + timedelta(seconds=int(data["expires_in"]))
        self._token = data["access_token"]
        # The auth server may rotate the refresh token, the old one is invalid afterwards
        self._refresh_token = data.get("refresh_token") or self._refresh_token

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

    # pylint: disable=broad-exception-caught
    def _check_2fa_auth(self, browser, verify_mode="phone", verify_token_func=None):
        if verify_mode not in ["phone", "email"]:
            raise ValueError(f'Unknown 2fa-mode "{verify_mode}" - Only "phone" or "email" supported')
        response = browser.wait_for_request(f"{self._AUTH_API}/Account/Login.*", 10).response
        location = response.headers.get("Location") or ""

        # Check login response location for direct success
        success_indicators = [
            "/connect/authorize/callback" in location,
            f"{self._APP}://callback" in location,
            "code=" in location,
        ]
        if any(success_indicators):
            return

        # Wait briefly for the browser to complete all redirects after login
        try:
            browser.wait_for_request(f"{re.escape(self._APP)}://callback.*", 3)
            return  # callback arrived — no 2FA needed
        except Exception:
            pass

        # Check all captured requests so far for the authorization code
        for request in reversed(browser.requests):
            if not request.response:
                continue
            req_location = request.response.headers.get("Location") or ""
            req_url = request.url or ""
            if re.findall("code=([0-9A-F]+)", req_location + req_url):
                return

        if verify_token_func is None:
            raise LoginError("Two factor authentication required, but no verify_token_func given")

        # 2FA is actually needed — try to find the method selection button
        try:
            element = WebDriverWait(browser, 5).until(
                expected_conditions.visibility_of_element_located((By.CLASS_NAME, verify_mode))
            )
            element.find_element(By.TAG_NAME, "button").click()
        except Exception:
            pass  # Some accounts skip method selection and go directly to code input

        verify_code = verify_token_func()

        # Try multiple selectors for the code input field
        for selector in [
            (By.NAME, "VerificationCode"),
            (By.NAME, "verificationCode"),
            (By.CSS_SELECTOR, "input[autocomplete='one-time-code']"),
            (By.CSS_SELECTOR, "input[type='tel']"),
            (By.CSS_SELECTOR, "input[type='number']"),
        ]:
            try:
                field = WebDriverWait(browser, 5).until(expected_conditions.element_to_be_clickable(selector))
                field.send_keys(verify_code)
                break
            except Exception:
                continue

        # Try multiple selectors for the submit button
        for selector in [
            (By.CLASS_NAME, "role_next"),
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.XPATH, "//button[@type='submit']"),
        ]:
            try:
                self._click(browser, selector)
                break
            except Exception:
                continue

    # pylint: enable=broad-exception-caught

    def login(self, email, password, **kwargs):
        """Simulate app auth"""
        headless = kwargs.get("headless", True)
        browser = self._get_browser(headless=headless)
        try:
            browser.get(self._register_link)
            wait = WebDriverWait(browser, 10)
            login_button = (By.XPATH, '//*[@id="duple-button-block"]/button[1]/span')
            wait.until(expected_conditions.visibility_of_element_located(login_button)).click()
            wait.until(expected_conditions.element_to_be_clickable((By.NAME, "input-email"))).send_keys(email)
            wait.until(expected_conditions.element_to_be_clickable((By.NAME, "Password"))).send_keys(password)
            self._click(browser, (By.XPATH, '//*[@id="duple-button-block"]/button'))
            self._check_login_error(browser)
            self._check_2fa_auth(
                browser,
                kwargs.get("verify_mode", "phone"),
                kwargs.get("verify_token_func"),
            )
            browser.wait_for_request(f"{self._AUTH_API}/connect.*")
            code = self._parse_code(browser, wait, accept_legal_terms=kwargs.get("accept_legal_terms", True))
            self._authorization_code(code)
        finally:
            # Keep the window open in debug mode (headless=False) to inspect problems
            if headless:
                browser.quit()

    def _ensure_token(self):
        with self._token_lock:
            if self._refresh_token and (
                not self._token
                or self._expires is None
                or datetime.now(timezone.utc) >= self._expires - self._TOKEN_EXPIRY_MARGIN
            ):
                self._renew_token()
        if not self._token:
            raise MissingLogin("You need to login!")

    def _default_headers(self):
        self._ensure_token()
        return {
            "Authorization": f"Bearer {self._token}",
            "App-Version": "16.46.4",
            "Operating-System": self._OS,
            "App": "com.lidl.eci.lidl.plus",
            "Accept-Language": self._language,
        }

    def _request(self, method, url, **kwargs):
        extra_headers = kwargs.pop("headers", {})
        for retry in (False, True):
            headers = {**self._default_headers(), **extra_headers}
            response = self._session.request(method, url, headers=headers, timeout=self._TIMEOUT, **kwargs)
            if response.status_code != 401 or retry or not self._refresh_token:
                break
            # The access token was rejected before it expired, renew it and try once more
            with self._token_lock:
                if headers["Authorization"] == f"Bearer {self._token}":
                    self._token = ""
        response.raise_for_status()
        return response

    def tickets(self, only_favorite=False):
        """
        Get a list of all tickets.

        :param only_favorite: A boolean value indicating whether to only retrieve favorite tickets.
            If set to True, only favorite tickets will be returned.
            If set to False (the default), all tickets will be retrieved.
        :type only_favorite: bool
        """
        url = f"{self._TICKET_API}/{self._country}/tickets"
        params = {"pageNumber": 1, "onlyFavorite": str(bool(only_favorite))}
        page = self._request("GET", url, params=params).json()
        tickets = list(page.get("tickets") or [])
        size = page.get("size") or 0
        pages = math.ceil((page.get("totalCount") or 0) / size) if size else 1
        for number in range(2, pages + 1):
            tickets += self._request("GET", url, params={**params, "pageNumber": number}).json().get("tickets") or []
        return tickets

    def ticket(self, ticket_id):
        """Get full data of single ticket by id"""
        return self._request("GET", f"{self._TICKET_DETAIL_API}/{self._country}/tickets/{ticket_id}").json()

    @staticmethod
    def parse_ticket_items(ticket):
        """Parse HTML receipt from ticket and return items as structured list"""
        return analytics.parse_receipt_items(ticket.get("htmlPrintedReceipt"))

    @staticmethod
    def _parse_ticket(ticket):
        """Store the parsed receipt in the ticket: "_items" (articles) and "_receipt" (everything else)"""
        receipt = analytics.parse_receipt(ticket.get("htmlPrintedReceipt"))
        ticket["_items"] = receipt.pop("items")
        ticket["_receipt"] = receipt

    # --- Cache ---

    def _load_cache(self):
        if not self._cache_file or not os.path.exists(self._cache_file):
            return {"tickets": {}, "items_version": analytics.ITEMS_VERSION}
        try:
            with open(self._cache_file, "r", encoding="utf-8") as file:
                cache = json.load(file)
            if not isinstance(cache, dict) or not isinstance(cache.get("tickets"), dict):
                raise ValueError("unexpected content")
        except ValueError as exc:
            # Keep the broken file (a timestamp keeps older ones), the next sync rebuilds the cache
            backup = f"{self._cache_file}.corrupt-{datetime.now():%Y%m%d-%H%M%S}"
            os.replace(self._cache_file, backup)
            _LOGGER.warning("Cache file %s is corrupt and was moved to %s: %s", self._cache_file, backup, exc)
            return {"tickets": {}, "items_version": analytics.ITEMS_VERSION}
        if cache.get("items_version") != analytics.ITEMS_VERSION:
            # The cache keeps the HTML of every receipt, so all of them get the details of the newer parser
            for ticket in cache["tickets"].values():
                self._parse_ticket(ticket)
            cache["items_version"] = analytics.ITEMS_VERSION
            cache["upgraded"] = True
        return cache

    def _save_cache(self, cache):
        if not self._cache_file:
            return
        cache.pop("upgraded", None)
        directory = os.path.dirname(os.path.abspath(self._cache_file))
        os.makedirs(directory, exist_ok=True)
        # Write to a temporary file first, an interrupted write must not destroy the cache
        handle, temp_path = tempfile.mkstemp(dir=directory, prefix=".lidlplus-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(cache, file, ensure_ascii=False, indent=2)
                # On the disk before it replaces the old file, so a power cut cannot leave an empty cache
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, self._cache_file)
            self._fsync_directory(directory)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    @staticmethod
    def _fsync_directory(directory):
        """Make the replacement of the file durable (not possible on Windows)"""
        directory_flag = getattr(os, "O_DIRECTORY", None)
        if directory_flag is None:
            return
        try:
            handle = os.open(directory, os.O_RDONLY | directory_flag)
        except OSError:
            return
        try:
            os.fsync(handle)
        except OSError:
            pass
        finally:
            os.close(handle)

    def sync(self):
        """Fetch only new tickets and add them to the cache. Returns count of new tickets."""
        with self._cache_lock:
            cache = self._load_cache()
            cached = cache["tickets"]
            added = 0
            try:
                new_ids = [ref["id"] for ref in self.tickets() if ref.get("id") and ref["id"] not in cached]
                for ticket_id in dict.fromkeys(new_ids):
                    try:
                        ticket = self.ticket(ticket_id)
                    except requests.HTTPError as exc:
                        if exc.response is None or exc.response.status_code not in self._SKIPPED_TICKET_ERRORS:
                            raise
                        # One broken receipt must not block all others, it is tried again with the next sync
                        _LOGGER.warning("Skipping receipt %s: %s", ticket_id, exc)
                        continue
                    self._parse_ticket(ticket)
                    cached[ticket_id] = ticket
                    added += 1
            finally:
                # Also keep the progress of an interrupted sync and receipts parsed again by a newer version
                if added or cache.get("upgraded"):
                    if added:
                        cache["last_updated"] = datetime.now(timezone.utc).isoformat()
                    self._save_cache(cache)
            return added

    def cached_tickets(self):
        """Return all tickets from cache as list."""
        return list(self._load_cache()["tickets"].values())

    def cached_data(self):
        """The complete cache: all tickets (including their HTML receipt) and all offers seen"""
        cache = self._load_cache()
        cache.pop("upgraded", None)
        return cache

    # --- Stores and offers (public, no login necessary) ---

    def _public_get(self, url, params=None):
        headers = {
            "Accept": "application/json",
            "Accept-Language": f"{self._language}-{self._country}",
            "User-Agent": f"LidlPlus/{self._PUBLIC_APP_VERSION} Android okhttp/4.12.0",
            "X-Client-Version": self._PUBLIC_APP_VERSION,
            "X-Client-Platform": "android",
        }
        response = self._session.get(url, headers=headers, params=params, timeout=self._TIMEOUT)
        response.raise_for_status()
        return response.json()

    def search_stores(self, query, latitude, longitude):
        """Stores matching a city, postal code or street, the nearest to the position first"""
        params = {"input": query, "language": self._language, "latitude": latitude, "longitude": longitude}
        stores = self._public_get(f"{self._STORES_API}/v1/autocomplete/{self._country}", params=params)
        return stores if isinstance(stores, list) else []

    def store_offers(self, store_key):
        """Current and announced offers of a store, store_key like "DE1234" (see search_stores and receipts)"""
        data = self._public_get(f"{self._OFFERS_API}/v4/{self._country}/{store_key}/offers")
        return [offer for offer in (data or {}).get("offers") or [] if isinstance(offer, dict)]

    def sync_offers(self, store_keys):
        """
        Add the offers of the stores to the cache. Offers are never removed from it, so it keeps the
        history of all offers seen. Returns the number of offers that were not in the cache before.
        """
        with self._cache_lock:
            cache = self._load_cache()
            archive = cache.setdefault("offers", {})
            now = datetime.now(timezone.utc).isoformat()
            added = 0
            changed = bool(cache.get("upgraded"))
            for store_key in store_keys:
                for offer in self.store_offers(store_key):
                    if not offer.get("id"):
                        continue
                    entry = archive.get(offer["id"])
                    if entry is None:
                        entry = archive[offer["id"]] = {"first_seen": now, "stores": []}
                        added += 1
                    changed |= self._update_archived(entry, "offer", offer, now)
                    if store_key not in entry["stores"]:
                        entry["stores"].append(store_key)
                        changed = True
            if changed:
                self._save_cache(cache)
            return added

    @staticmethod
    def _update_archived(entry, key, value, now):
        """Keep the latest version of an offer or leaflet, True if the cache has to be saved"""
        # last_seen changes only once a day, so the cache is not written by every sync
        if entry.get(key) == value and (entry.get("last_seen") or "")[:10] == now[:10]:
            return False
        entry[key] = value
        entry["last_seen"] = now
        return True

    def cached_offers(self):
        """All offers in the cache, normalized, see analytics.archived_offers"""
        return analytics.archived_offers(self._load_cache().get("offers"))

    # --- Leaflets (public, no login necessary) ---

    def _leaflet_get(self, path, params):
        response = self._session.get(
            f"{self._LEAFLETS_API}/{path}", headers={"Accept": "application/json"}, params=params, timeout=self._TIMEOUT
        )
        response.raise_for_status()
        return response.json()

    def leaflets(self, region=None):
        """
        All leaflets published for the country: the weekly leaflet of the current and the next weeks,
        special leaflets and more, as returned by the leaflet API (grouped in categories).

        The weekly leaflets differ between the offer regions (see leaflet_region), without region the
        national leaflets are returned.
        """
        # The locale has to look exactly like "lidl/de-DE", other formats are rejected
        params = {"client_locale": f"lidl/{self._language}-{self._country}", "region_id": region or 0}
        return self._leaflet_get("overview", params)

    def leaflet(self, identifier, region=None):
        """A single leaflet with pages (image, text) and products, identifier like "aktionsprospekt-...-321560" """
        params = {"flyer_identifier": identifier, "region_id": region or 0}
        return (self._leaflet_get("flyer", params) or {}).get("flyer") or {}

    def store(self, store_key):
        """Address, position and province of a store, store_key like "DE1234" """
        return self._public_get(f"{self._STORES_API}/v1/{self._country}/{store_key}")

    def _directory_page(self, path):
        """Data of a page of the store directory of lidl.de, None if the page does not exist"""
        headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 (compatible; lidl-plus)"}
        response = self._session.get(
            f"{self._STORE_DIRECTORY}/{path}/_payload.json", headers=headers, timeout=self._TIMEOUT
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def _lookup_offer_region(self, store_key):
        """(region, name) of a store from the store directory of lidl.de (only Germany), None if it is not listed"""
        if self._country != "DE":
            return None
        number = int(re.sub(r"\D", "", store_key) or 0)
        store = self.store(store_key) or {}
        city = str(store.get("locality") or "")
        paths = [analytics.url_slug(city)]
        state = self._directory_page(analytics.url_slug(store.get("province"))) if store.get("province") else None
        # The directory names cities shorter at times, e.g. "Frankfurt" for "Frankfurt am Main"
        for entry in analytics.nuxt_objects(state, "name", "url"):
            name = str(entry["name"] or "")
            if name and (city.lower() == name.lower() or city.lower().startswith(name.lower() + " ")):
                paths.append(str(entry["url"]).rstrip("/").split("/filialen/", 1)[-1])
        for path in dict.fromkeys(filter(None, paths)):
            for object_number, region in analytics.store_offer_regions(self._directory_page(path)).items():
                if int(re.sub(r"\D", "", object_number) or -1) == number:
                    return region
        return None

    def leaflet_region(self, store_key):
        """
        Offer region of a store like {"region": 10, "name": "Grevenbroich"}: the weekly leaflets differ between
        the regions. Taken from the store directory of lidl.de (only Germany) and kept in the cache for 30 days.
        None if it is not known, then the national leaflets are used.
        """
        with self._cache_lock:
            cache = self._load_cache()
            known = (cache.get("leaflet_regions") or {}).get(store_key)
            checked = analytics.parse_datetime((known or {}).get("checked"))
            now = datetime.now(timezone.utc)
            if not checked or now - checked >= self._OFFER_REGION_RECHECK:
                try:
                    region = self._lookup_offer_region(store_key)
                except (requests.RequestException, ValueError, TypeError, AttributeError, KeyError) as exc:
                    # Tried again with the next sync, the region found before stays valid
                    _LOGGER.warning("Could not find the offer region of store %s: %s", store_key, exc)
                else:
                    known = {"region": region[0] if region else None, "name": region[1] if region else ""}
                    known["checked"] = now.isoformat()
                    cache.setdefault("leaflet_regions", {})[store_key] = known
                    self._save_cache(cache)
            return known if known and known.get("region") is not None else None

    def sync_leaflets(self, categories=None, region=None):
        """
        Add the leaflets to the cache with their pages and products (of all categories or only the given ones),
        with the regional weekly leaflets of an offer region (see leaflet_region) or the national ones.
        Leaflets are never removed from it. Returns the number of leaflets that were not in the cache before.
        """
        with self._cache_lock:
            cache = self._load_cache()
            archive = cache.setdefault("leaflets", {})
            now = datetime.now(timezone.utc)
            added = 0
            changed = bool(cache.get("upgraded"))
            for leaflet in analytics.leaflet_overview(self.leaflets(region)):
                if not leaflet["id"]:
                    continue
                entry = archive.get(leaflet["id"])
                if entry is None:
                    entry = archive[leaflet["id"]] = {"first_seen": now.isoformat()}
                    added += 1
                changed |= self._update_archived(entry, "leaflet", leaflet, now.isoformat())
                if self._leaflet_details_outdated(entry, categories, now):
                    changed |= self._load_leaflet_details(entry, now, region)
            if changed:
                self._save_cache(cache)
            return added

    def _load_leaflet_details(self, entry, now, region=None):
        """Load pages and products of a leaflet, True if they were stored"""
        leaflet = entry["leaflet"]
        try:
            details = analytics.leaflet_details(self.leaflet(leaflet["identifier"], region))
        except (requests.RequestException, ValueError, TypeError, AttributeError, KeyError) as exc:
            # Tried again with the next sync
            _LOGGER.warning("Could not load leaflet %s: %s", leaflet["identifier"], exc)
            return False
        if details["pages"]:
            entry.update(
                details=details,
                details_of=leaflet["pdf"],
                details_loaded=now.isoformat(),
                details_status=analytics.leaflet_status(leaflet),
            )
            return True
        if "details" in entry:
            # An empty answer does not replace the pages loaded before
            return False
        # Without details_of the leaflet is loaded again with the next sync
        entry["details"] = details
        return True

    @classmethod
    def _leaflet_details_outdated(cls, entry, categories, now):
        """True if the pages and products of a leaflet have to be loaded (again)"""
        leaflet = entry["leaflet"]
        if not leaflet["identifier"] or (categories is not None and leaflet["category"] not in categories):
            return False
        status = analytics.leaflet_status(leaflet)
        if "details" not in entry:
            # Leaflets that ended before they were seen for the first time are not loaded
            return status != "expired"
        if entry.get("details_of") != leaflet["pdf"]:
            # A corrected leaflet is published with a new PDF file
            return True
        # Leaflets are published before they are complete: until their offers start they are loaded once a day
        loaded = analytics.parse_datetime(entry.get("details_loaded"))
        outdated = loaded is None or now - loaded >= cls._LEAFLET_RELOAD_INTERVAL
        return entry.get("details_status") == "upcoming" and status != "expired" and outdated

    def cached_leaflets(self, today=None, region=None):
        """
        The leaflets in the cache (see analytics.archived_leaflets, today: local date for the status):
        with a region the ones of this offer region (see analytics.leaflets_of_region), otherwise all
        """
        leaflets = analytics.archived_leaflets(self._load_cache().get("leaflets"), today)
        return analytics.leaflets_of_region(leaflets, region) if region is not None else leaflets

    # --- Analytics ---

    def all_ticket_items(self):
        """All items across all tickets as flat list with date and store."""
        return analytics.ticket_items(self.cached_tickets())

    def price_history(self, item_id):
        """Price changes for a specific item across all receipts."""
        return analytics.price_history(self.all_ticket_items(), item_id)

    def frequently_bought(self, limit=10):
        """Top N most frequently bought items by total quantity."""
        return analytics.frequently_bought(self.all_ticket_items(), limit)

    def spending_by_month(self):
        """Total spending grouped by month (YYYY-MM)."""
        return analytics.spending_by_month(self.cached_tickets())

    def spending_by_store(self):
        """Total spending grouped by store name."""
        return analytics.spending_by_store(self.cached_tickets())

    def last_seen(self, item_id):
        """Last purchase info for a specific item id."""
        return analytics.last_seen(self.all_ticket_items(), item_id)

    def current_month_spending(self):
        """Total spending in the current calendar month."""
        # Receipt dates are in local time, so the current month is determined locally too
        return self.spending_by_month().get(datetime.now().strftime("%Y-%m"), 0.0)

    def average_basket(self):
        """Average total amount per shopping trip."""
        return analytics.average_basket(self.cached_tickets())

    def shopping_frequency_days(self):
        """Average number of days between shopping trips."""
        return analytics.shopping_frequency_days(self.cached_tickets())

    def restock_suggestions(self, min_purchases=3):
        """Items overdue for restocking based on average purchase interval."""
        return analytics.restock_suggestions(self.all_ticket_items(), min_purchases)

    def coupon_promotions_v1(self):
        """Get list of all coupons API V1"""
        url = f"{self._COUPONS_V1_API}/v1/promotionslist"
        return self._request("GET", url, headers={"Country": self._country}).json()

    def activate_coupon_promotion_v1(self, promotion_id):
        """Activate single coupon by id API V1"""
        url = f"{self._COUPONS_V1_API}/v1/promotions/{promotion_id}/activation"
        return self._request("POST", url, headers={"Country": self._country})

    def coupons(self):
        """Get list of all coupons"""
        return self._request("GET", f"{self._COUPONS_API}/v2/{self._country}").json()

    def activate_coupon(self, coupon_id):
        """Activate single coupon by id"""
        return self._request("POST", f"{self._COUPONS_API}/v1/{self._country}/{coupon_id}/activation").json()

    def deactivate_coupon(self, coupon_id):
        """Deactivate single coupon by id"""
        return self._request("DELETE", f"{self._COUPONS_API}/v1/{self._country}/{coupon_id}/activation").json()

    def _pending_coupons(self):
        for coupon in analytics.section_entries(self.coupons(), "coupons"):
            valid = analytics.is_within_validity(coupon.get("startValidityDate"), coupon.get("endValidityDate"))
            if coupon.get("id") and valid and not analytics.coupon_is_activated(coupon):
                yield coupon.get("title") or coupon["id"], self.activate_coupon, coupon["id"]

    def _pending_promotions(self):
        try:
            promotions = analytics.section_entries(self.coupon_promotions_v1(), "promotions")
        except requests.RequestException as exc:
            _LOGGER.warning("Could not load the coupons of API v1: %s", exc)
            return
        for promotion in promotions:
            validity = promotion.get("validity") or {}
            valid = analytics.is_within_validity(validity.get("start"), validity.get("end"))
            if promotion.get("promotionId") and valid and not analytics.coupon_is_activated(promotion):
                promotion_id = promotion["promotionId"]
                yield promotion.get("title") or promotion_id, self.activate_coupon_promotion_v1, promotion_id

    def activate_all_coupons(self):
        """
        Activate every coupon that is currently valid and not activated yet.

        Covers the coupons of API v2 and the promotions of API v1, some coupons are only available there.
        Returns a dict with the titles of the "activated" and "failed" coupons.
        """
        # Load both lists before activating anything
        pending = [*self._pending_coupons(), *self._pending_promotions()]
        result = {"activated": [], "failed": []}
        for title, activate, coupon_id in pending:
            try:
                activate(coupon_id)
            except requests.RequestException as exc:
                _LOGGER.warning("Could not activate coupon %s: %s", title, exc)
                result["failed"].append(title)
            else:
                result["activated"].append(title)
        return result

    def account_id(self):
        """
        Check the login and the country with the first page of the receipts, errors of this request are raised.

        Returns the loyalty ID of the account, None if the loyalty endpoint does not answer (it fails for some
        accounts while everything else works).
        """
        self._request(
            "GET", f"{self._TICKET_API}/{self._country}/tickets", params={"pageNumber": 1, "onlyFavorite": "False"}
        )
        try:
            return self.loyalty_id() or None
        except requests.RequestException as exc:
            _LOGGER.info("The loyalty ID could not be loaded: %s", exc)
            return None

    def loyalty_id(self):
        """Get your loyalty ID"""
        response = self._request("GET", f"{self._PROFILE_API}/v1/{self._country}/loyalty")
        return response.text.strip().strip('"')
