"""Shared test setup."""

import importlib.util

# The Home Assistant tests need pytest-homeassistant-custom-component, see requirements_test_ha.txt
collect_ignore = [] if importlib.util.find_spec("pytest_homeassistant_custom_component") else ["ha"]
