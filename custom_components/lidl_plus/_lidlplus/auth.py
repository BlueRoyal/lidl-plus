"""
Browser-free login for Lidl Plus using direct HTTP requests + PKCE.
No Selenium required — suitable for Home Assistant.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import urllib.parse

import requests

_CLIENT_ID = "LidlPlusNativeClient"
_AUTH_API = "https://accounts.lidl.com"
_APP = "com.lidlplus.app"
_TIMEOUT = 30


# ── PKCE helpers ─────────────────────────────────────────────────────────────

def _generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for OAuth PKCE."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _build_auth_url(country: str, language: str) -> tuple[str, str]:
    """Build the OAuth authorization URL. Returns (url, code_verifier)."""
    verifier, challenge = _generate_pkce()
    params = {
        "client_id": _CLIENT_ID,
        "response_type": "code",
        "scope": "openid profile offline_access lpprofile lpapis",
        "redirect_uri": f"{_APP}://callback",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "Country": country.upper(),
        "language": f"{language.lower()}-{country.upper()}",
    }
    url = f"{_AUTH_API}/connect/authorize?{urllib.parse.urlencode(params)}"
    return url, verifier


# ── Token exchange ────────────────────────────────────────────────────────────

def _exchange_code(code: str, code_verifier: str) -> dict:
    """Exchange authorization code for access + refresh tokens."""
    secret = base64.b64encode(f"{_CLIENT_ID}:secret".encode()).decode()
    headers = {
        "Authorization": f"Basic {secret}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": f"{_APP}://callback",
        "code_verifier": code_verifier,
    }
    resp = requests.post(
        f"{_AUTH_API}/connect/token", headers=headers, data=payload, timeout=_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()


def _extract_code(text: str) -> str | None:
    """Find authorization code in a URL or HTML body."""
    match = re.search(r"[?&]code=([0-9A-Fa-z_\-]+)", text)
    return match.group(1) if match else None


def _extract_csrf(html: str) -> str:
    """Extract __RequestVerificationToken from an HTML page."""
    match = re.search(
        r'<input[^>]+name="__RequestVerificationToken"[^>]+value="([^"]+)"', html
    )
    if not match:
        match = re.search(
            r'name="__RequestVerificationToken"[^>]+value="([^"]+)"', html
        )
    return match.group(1) if match else ""


def _extract_return_url(html: str) -> str:
    """Extract the ReturnUrl hidden field."""
    match = re.search(r'name="ReturnUrl"[^>]+value="([^"]+)"', html)
    if match:
        return urllib.parse.unquote(match.group(1).replace("&amp;", "&"))
    return ""


# ── Public API ────────────────────────────────────────────────────────────────

class LidlLoginSession:
    """
    Manages a stateful browser-free login session.

    Usage:
        session = LidlLoginSession(country, language)
        result = session.start(email, password)

        if result == "ok":
            refresh_token = session.refresh_token
        elif result == "2fa":
            session.submit_2fa(code)
            refresh_token = session.refresh_token
    """

    def __init__(self, country: str, language: str) -> None:
        self.country = country.upper()
        self.language = language.lower()
        self.refresh_token: str | None = None
        self._session = requests.Session()
        self._session.headers["User-Agent"] = (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
        )
        self._code_verifier: str = ""
        self._pending_csrf: str = ""
        self._pending_return_url: str = ""
        self._pending_url: str = ""

    def start(self, email: str, password: str) -> str:
        """
        Begin login flow.
        Returns "ok" (login complete) or "2fa" (code required).
        Raises LoginError on bad credentials.
        """
        from .exceptions import LoginError

        auth_url, self._code_verifier = _build_auth_url(self.country, self.language)

        # Follow OAuth redirect chain to reach the login page
        resp = self._session.get(auth_url, allow_redirects=True, timeout=_TIMEOUT)
        login_page = resp.text

        csrf = _extract_csrf(login_page)
        return_url = _extract_return_url(login_page)

        # POST credentials to the login endpoint
        login_resp = self._session.post(
            f"{_AUTH_API}/Account/Login",
            data={
                "input-email": email,
                "Password": password,
                "__RequestVerificationToken": csrf,
                "ReturnUrl": return_url,
            },
            allow_redirects=False,
            timeout=_TIMEOUT,
        )

        # Check for credential errors in response body
        if login_resp.status_code == 200:
            body = login_resp.text
            err_match = re.search(r'app-errors="\\{[^:]*?:.(.*?).}"', body)
            if err_match:
                raise LoginError(err_match.group(1))
            # Generic error fallback
            if "invalid" in body.lower() and "password" in body.lower():
                raise LoginError("Invalid email or password")

        location = login_resp.headers.get("Location", "")

        # Check if we already have the authorization code (no 2FA)
        code = _extract_code(location) or _extract_code(login_resp.url)
        if code:
            self._finalize(code)
            return "ok"

        # Follow redirect to 2FA page
        if location:
            next_url = (
                location if location.startswith("http")
                else urllib.parse.urljoin(_AUTH_API, location)
            )
            twofactor_resp = self._session.get(
                next_url, allow_redirects=True, timeout=_TIMEOUT
            )
        else:
            twofactor_resp = login_resp

        # Check again for code after following redirects
        code = _extract_code(twofactor_resp.url) or _extract_code(twofactor_resp.text)
        if code:
            self._finalize(code)
            return "ok"

        # Store state needed for 2FA submission
        self._pending_csrf = _extract_csrf(twofactor_resp.text)
        self._pending_return_url = _extract_return_url(twofactor_resp.text)
        self._pending_url = twofactor_resp.url

        return "2fa"

    def submit_2fa(self, code: str) -> None:
        """Submit the 2FA verification code to complete login."""
        from .exceptions import LoginError

        # Determine the form action URL (usually the current page or a known endpoint)
        post_url = self._pending_url or f"{_AUTH_API}/Account/LoginVerification"

        verify_resp = self._session.post(
            post_url,
            data={
                "VerificationCode": code,
                "__RequestVerificationToken": self._pending_csrf,
                "ReturnUrl": self._pending_return_url,
            },
            allow_redirects=False,
            timeout=_TIMEOUT,
        )

        # Follow redirect chain to find the authorization code
        location = verify_resp.headers.get("Location", "")
        for _ in range(10):
            auth_code = _extract_code(location)
            if auth_code:
                self._finalize(auth_code)
                return
            if not location:
                break
            next_url = (
                location if location.startswith("http")
                else urllib.parse.urljoin(_AUTH_API, location)
            )
            resp = self._session.get(next_url, allow_redirects=False, timeout=_TIMEOUT)
            location = resp.headers.get("Location", resp.url)

        raise LoginError("2FA verification failed — could not retrieve authorization code")

    def _finalize(self, code: str) -> None:
        """Exchange code for tokens and store the refresh token."""
        tokens = _exchange_code(code, self._code_verifier)
        self.refresh_token = tokens["refresh_token"]
