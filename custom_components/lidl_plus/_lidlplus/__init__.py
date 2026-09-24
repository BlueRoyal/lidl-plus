"""
Vendored copy of the lidlplus library for the Home Assistant integration

api.py, analytics.py, exceptions.py and export.py must stay identical to the files in lidlplus/,
tests/test_vendored_copy.py checks that. The Selenium login is not used in Home Assistant.
"""

from .api import LidlPlusApi

__all__ = ["LidlPlusApi"]
