"""Services of the Lidl Plus integration."""

from __future__ import annotations

import logging
import os

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from ._lidlplus import export
from .const import DOMAIN, EXPORT_DIR, SERVICE_ACTIVATE_ALL_COUPONS, SERVICE_EXPORT, SERVICE_SYNC
from .coordinator import LidlPlusCoordinator

_LOGGER = logging.getLogger(__name__)


EXPORT_SCHEMA = vol.Schema(
    {
        vol.Optional("dataset", default="all"): vol.In([*export.DATASETS, "all"]),
        vol.Optional("format", default="csv"): vol.In(["csv", "json"]),
    }
)


def _write_new_file(folder: str, name: str, content: bytes) -> str:
    """Write a file, a number is added to the name instead of replacing an existing file"""
    os.makedirs(folder, exist_ok=True)
    base, extension = os.path.splitext(os.path.join(folder, name))
    path, number = base + extension, 1
    while os.path.exists(path):
        number += 1
        path = f"{base}_{number}{extension}"
    with open(path, "xb") as file:
        file.write(content)
    return path


def _loaded_coordinators(hass: HomeAssistant) -> list[LidlPlusCoordinator]:
    coordinators = [entry.runtime_data for entry in hass.config_entries.async_loaded_entries(DOMAIN)]
    if not coordinators:
        raise HomeAssistantError("No Lidl Plus account is loaded")
    return coordinators


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services, they act on all configured Lidl Plus accounts."""

    async def async_sync(call: ServiceCall) -> None:
        """Force an immediate sync of new receipts."""
        for coordinator in _loaded_coordinators(hass):
            await coordinator.async_request_refresh()

    async def async_activate_all_coupons(call: ServiceCall) -> ServiceResponse:
        """Activate every currently valid coupon (API v1 and v2)."""
        activated: list[str] = []
        failed: list[str] = []
        for coordinator in _loaded_coordinators(hass):
            try:
                result = await coordinator.async_call_api(coordinator.api.activate_all_coupons)
            except Exception as exc:  # noqa: BLE001
                raise HomeAssistantError(f"Could not activate the Lidl Plus coupons: {exc}") from exc
            activated += result["activated"]
            failed += result["failed"]
            await coordinator.async_request_refresh()
        _LOGGER.info("Activated %s Lidl Plus coupons, %s failed", len(activated), len(failed))
        return {"activated": activated, "failed": failed}

    async def async_export(call: ServiceCall) -> ServiceResponse:
        """Write the data of every account to a file in the export folder of the configuration."""
        dataset = call.data["dataset"]
        file_format = "zip" if dataset == "all" else call.data["format"]
        timestamp = dt_util.now().strftime("%Y-%m-%d_%H-%M-%S")
        files: list[str] = []
        coordinators = _loaded_coordinators(hass)
        for coordinator in coordinators:
            # With several accounts the file names contain the name of the account
            account = f"_{slugify(coordinator.config_entry.title)}" if len(coordinators) > 1 else ""
            name = f"lidl_plus{account}_{dataset}_{timestamp}.{file_format}"
            try:
                content = await coordinator.async_export(dataset, file_format)
                path = await hass.async_add_executor_job(_write_new_file, hass.config.path(EXPORT_DIR), name, content)
            except OSError as exc:
                raise HomeAssistantError(f"Could not write the Lidl Plus export: {exc}") from exc
            files.append(path)
        _LOGGER.info("Exported the Lidl Plus data to %s", ", ".join(files))
        return {"files": files}

    hass.services.async_register(DOMAIN, SERVICE_SYNC, async_sync)
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT,
        async_export,
        schema=EXPORT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ACTIVATE_ALL_COUPONS,
        async_activate_all_coupons,
        supports_response=SupportsResponse.OPTIONAL,
    )
