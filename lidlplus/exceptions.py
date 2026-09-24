"""
Exceptions
"""


class WebBrowserException(Exception):
    """No Browser installed"""


class LoginError(Exception):
    """Login failed"""


class AuthenticationError(LoginError):
    """Refresh token or authorization code rejected by the Lidl auth server"""


class LegalTermsException(Exception):
    """Not accepted legal terms"""


class MissingLogin(Exception):
    """Login necessary"""
