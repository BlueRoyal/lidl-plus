"""The Lidl Plus integration."""

from __future__ import annotations

import glob
import logging
import os
import shutil
import threading

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import STORAGE_DIR
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from ._lidlplus.api import LidlPlusApi
from .article_api import async_setup_article_api
from .const import CONF_COUNTRY, CONF_LANGUAGE, CONF_REFRESH_TOKEN, DOMAIN, KEY_LOYALTY_ID
from .coordinator import LidlPlusConfigEntry, LidlPlusCoordinator
from .panel import async_register_panel, async_setup_panel_api, async_unregister_panel
from .rest_api import async_setup_rest_api
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Files of versions before 1.2.0: a cache shared by all entries and panel data served without authentication
_LEGACY_CACHE_FILE = "lidl_plus_cache.json"
_LEGACY_PANEL_DATA_FILE = "www/lidl_plus/data.json"
# Entries are set up in parallel, the migration of the shared files must run one after the other
_STORAGE_LOCK = threading.Lock()


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the services, the APIs and the panel."""
    async_setup_services(hass)
    async_setup_panel_api(hass)
    async_setup_article_api(hass)
    async_setup_rest_api(hass)
    # Registered independently of the entries, so the panel stays while an entry is reloaded or cannot be set up
    await async_register_panel(hass)
    return True


def _cache_path(hass: HomeAssistant, entry: LidlPlusConfigEntry) -> str:
    return hass.config.path(STORAGE_DIR, f"{DOMAIN}_cache_{entry.entry_id}.json")


def _removed_cache_path(hass: HomeAssistant, unique_id: str) -> str:
    """Cache of a removed entry, a new entry of the same account continues with it."""
    return hass.config.path(STORAGE_DIR, f"{DOMAIN}_removed_{slugify(unique_id)}.json")


def _move_without_overwriting(source: str, target: str) -> str:
    """Move a file, an existing target gets a timestamp in its name so nothing is replaced."""
    if os.path.exists(target):
        base, extension = os.path.splitext(target)
        target = f"{base}_{dt_util.utcnow():%Y%m%d%H%M%S}{extension}"
    os.replace(source, target)
    return target


def _prepare_storage(hass: HomeAssistant, entry: LidlPlusConfigEntry) -> str:
    """Return the receipt cache of the entry. Files of older versions are copied or moved, never deleted."""
    with _STORAGE_LOCK:
        return _prepare_storage_locked(hass, entry)


def _prepare_storage_locked(hass: HomeAssistant, entry: LidlPlusConfigEntry) -> str:
    cache_path = _cache_path(hass, entry)
    storage_dir = os.path.dirname(cache_path)
    os.makedirs(storage_dir, exist_ok=True)
    if not os.path.exists(cache_path):
        removed_cache = _removed_cache_path(hass, entry.unique_id) if entry.unique_id else ""
        legacy_cache = hass.config.path(_LEGACY_CACHE_FILE)
        if removed_cache and os.path.exists(removed_cache):
            os.replace(removed_cache, cache_path)
            _LOGGER.info("Continuing with the receipt cache of the removed entry of this account")
        elif os.path.exists(legacy_cache) and not glob.glob(
            os.path.join(glob.escape(storage_dir), f"{DOMAIN}_cache_*.json")
        ):
            # Up to version 1.1.0 all entries shared one cache. The first entry gets a copy, the original
            # stays as backup, so going back to an older version keeps working as well.
            temp_path = f"{cache_path}.tmp"
            shutil.copyfile(legacy_cache, temp_path)
            os.replace(temp_path, cache_path)
            _LOGGER.info("Copied the receipt cache %s to %s, the old file stays as backup", legacy_cache, cache_path)
    public_data = hass.config.path(_LEGACY_PANEL_DATA_FILE)
    if os.path.exists(public_data):
        # Everything in www/ can be downloaded without login, the panel uses the websocket API now
        backup = _move_without_overwriting(
            public_data, hass.config.path(STORAGE_DIR, f"{DOMAIN}_panel_data_backup.json")
        )
        _LOGGER.info("Moved %s out of the public www folder to %s", public_data, backup)
    return cache_path


@callback
def _async_migrate_unique_id(hass: HomeAssistant, entry: LidlPlusConfigEntry) -> None:
    """Identify entries of older versions by their loyalty ID instead of country and language."""
    loyalty_id = entry.runtime_data.data.get(KEY_LOYALTY_ID)
    if not loyalty_id or entry.unique_id == loyalty_id:
        return
    if hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, loyalty_id):
        return  # the same account is configured twice, keep both entries as they are
    hass.config_entries.async_update_entry(entry, unique_id=loyalty_id)


async def async_setup_entry(hass: HomeAssistant, entry: LidlPlusConfigEntry) -> bool:
    """Set up Lidl Plus from a config entry."""
    cache_path = await hass.async_add_executor_job(_prepare_storage, hass, entry)
    api = LidlPlusApi(
        language=entry.data[CONF_LANGUAGE],
        country=entry.data[CONF_COUNTRY],
        refresh_token=entry.data[CONF_REFRESH_TOKEN],
        cache_file=cache_path,
    )
    coordinator = LidlPlusCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    _async_migrate_unique_id(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # The panel was removed if all entries were deleted before this one was added
    await async_register_panel(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LidlPlusConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: LidlPlusConfigEntry) -> None:
    """Keep the receipt cache of the removed entry, remove the panel with the last entry."""

    def _keep_cache() -> None:
        cache_path = _cache_path(hass, entry)
        if os.path.exists(cache_path):
            # Lidl may not return old receipts anymore, so the cache is kept for a new entry of the account
            target = _move_without_overwriting(cache_path, _removed_cache_path(hass, entry.unique_id or entry.entry_id))
            _LOGGER.info("Kept the receipt cache of the removed entry as %s", target)

    await hass.async_add_executor_job(_keep_cache)
    if not hass.config_entries.async_entries(DOMAIN, include_ignore=False):
        async_unregister_panel(hass)
