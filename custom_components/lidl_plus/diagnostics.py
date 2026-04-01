"""Diagnostics for Lidl Plus — adds 'Download Diagnostics' button in HA UI."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_REFRESH_TOKEN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics data — token is redacted for safety."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    data = coordinator.data or {}

    return {
        "config": {
            "country": entry.data.get("country"),
            "language": entry.data.get("language"),
            "refresh_token": "**REDACTED**",
        },
        "coordinator_data": {
            k: v
            for k, v in data.items()
            # Redact coupon list (may contain personal data), keep counts
            if k not in ("coupons",)
        },
        "last_update_success": coordinator.last_update_success,
        "last_exception": str(coordinator.last_exception) if coordinator.last_exception else None,
    }
