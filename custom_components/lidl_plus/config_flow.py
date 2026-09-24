"""Config flow for Lidl Plus."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

import requests
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from ._lidlplus.api import LidlPlusApi
from ._lidlplus.exceptions import LoginError, MissingLogin
from .const import (
    CONF_BESTTIME_API_KEY,
    CONF_COUNTRY,
    CONF_LANGUAGE,
    CONF_OFFER_STORES,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    KEY_STORES,
)

_LOGGER = logging.getLogger(__name__)

# Entries created before version 1.2.0 used "<COUNTRY>_<language>" instead of the loyalty ID
_LEGACY_UNIQUE_ID = re.compile(r"^[A-Z]{2}_[a-z]{2}$")

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_COUNTRY, default="DE"): str,
        vol.Required(CONF_LANGUAGE, default="de"): str,
        vol.Required(CONF_REFRESH_TOKEN): str,
    }
)
STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_REFRESH_TOKEN): str})
STEP_RECONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_COUNTRY): str,
        vol.Required(CONF_LANGUAGE): str,
        vol.Optional(CONF_REFRESH_TOKEN): str,
    }
)


class LidlPlusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for Lidl Plus."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> LidlPlusOptionsFlow:
        """Choose the stores whose offers are loaded."""
        return LidlPlusOptionsFlow()

    def __init__(self) -> None:
        # The auth server may replace a refresh token during the validation, even if the validation
        # fails afterwards (e.g. wrong country). The replacement is used when the form is sent again.
        self._replaced_tokens: dict[str, str] = {}

    async def _async_validate(
        self, country: str, language: str, entered_token: str, entry: ConfigEntry | None = None
    ) -> tuple[str | None, str, dict[str, str]]:
        """Return account ID (None if Lidl does not tell it), current refresh token and form errors."""
        token = self._replaced_tokens.get(entered_token, entered_token)
        api = LidlPlusApi(language=language, country=country, refresh_token=token)
        account_id: str | None = None
        errors: dict[str, str] = {}
        try:
            # Login and country are checked with the receipts, the loyalty ID is optional
            account_id = await self.hass.async_add_executor_job(api.account_id)
        except (LoginError, MissingLogin):
            errors["base"] = "invalid_auth"
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (401, 403):
                errors["base"] = "invalid_auth"
            elif status in (400, 404):
                errors["base"] = "invalid_country"
            else:
                errors["base"] = "cannot_connect"
        except requests.RequestException:
            errors["base"] = "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error while validating the Lidl Plus refresh token")
            errors["base"] = "unknown"

        if api.refresh_token != token:
            self._replaced_tokens[entered_token] = api.refresh_token
            if errors and entry is not None and entry.data[CONF_REFRESH_TOKEN] == token:
                self._async_keep_replaced_token(entry, api.refresh_token)
        return account_id, api.refresh_token, errors

    @callback
    def _async_keep_replaced_token(self, entry: ConfigEntry, token: str) -> None:
        """The stored token was replaced during a failed validation, the old one is invalid now."""
        self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_REFRESH_TOKEN: token})
        if entry.state is ConfigEntryState.LOADED:
            # The running coordinator still holds the old token
            self.hass.config_entries.async_schedule_reload(entry.entry_id)

    def _abort_if_other_account(self, entry: ConfigEntry, account_id: str) -> None:
        """Make sure reauth/reconfigure do not switch to a different Lidl Plus account."""
        if not _LEGACY_UNIQUE_ID.match(entry.unique_id or ""):
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return
        # Entries of older versions get the loyalty ID as unique ID, unless another entry has it already
        other = self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, account_id)
        if other is not None and other.entry_id != entry.entry_id:
            raise AbortFlow("already_configured")

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Add a Lidl Plus account with a refresh token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            country = user_input[CONF_COUNTRY].strip().upper()
            language = user_input[CONF_LANGUAGE].strip().lower()
            account_id, refresh_token, errors = await self._async_validate(
                country, language, user_input[CONF_REFRESH_TOKEN].strip()
            )
            if not errors:
                # Without the loyalty ID the account is identified by country and language, like before 1.2.0
                if entry := await self.async_set_unique_id(account_id or f"{country}_{language.lower()}"):
                    # Adding a configured account again replaces its token, also when its setup had failed
                    return self.async_update_reload_and_abort(
                        entry, data_updates={CONF_REFRESH_TOKEN: refresh_token}, reason="already_configured"
                    )
                return self.async_create_entry(
                    title=f"Lidl Plus ({country})",
                    data={CONF_COUNTRY: country, CONF_LANGUAGE: language, CONF_REFRESH_TOKEN: refresh_token},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(STEP_USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start a reauthentication when the refresh token was rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for a new refresh token."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            account_id, refresh_token, errors = await self._async_validate(
                entry.data[CONF_COUNTRY], entry.data[CONF_LANGUAGE], user_input[CONF_REFRESH_TOKEN].strip(), entry
            )
            if not errors:
                if not account_id:
                    # Without the loyalty ID the account of the token cannot be compared
                    return self.async_update_reload_and_abort(entry, data_updates={CONF_REFRESH_TOKEN: refresh_token})
                await self.async_set_unique_id(account_id)
                self._abort_if_other_account(entry, account_id)
                return self.async_update_reload_and_abort(
                    entry, unique_id=account_id, data_updates={CONF_REFRESH_TOKEN: refresh_token}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"title": entry.title},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change country, language or refresh token of an existing entry."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            country = user_input[CONF_COUNTRY].strip().upper()
            language = user_input[CONF_LANGUAGE].strip().lower()
            token = (user_input.get(CONF_REFRESH_TOKEN) or "").strip() or entry.data[CONF_REFRESH_TOKEN]
            account_id, refresh_token, errors = await self._async_validate(country, language, token, entry)
            if not errors:
                data_updates = {CONF_COUNTRY: country, CONF_LANGUAGE: language, CONF_REFRESH_TOKEN: refresh_token}
                if not account_id:
                    # Without the loyalty ID the account of the token cannot be compared
                    return self.async_update_reload_and_abort(entry, data_updates=data_updates)
                await self.async_set_unique_id(account_id)
                self._abort_if_other_account(entry, account_id)
                return self.async_update_reload_and_abort(entry, unique_id=account_id, data_updates=data_updates)

        source = user_input or entry.data
        suggested = {CONF_COUNTRY: source[CONF_COUNTRY], CONF_LANGUAGE: source[CONF_LANGUAGE]}
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(STEP_RECONFIGURE_SCHEMA, suggested),
            errors=errors,
        )


def _store_label(store: dict) -> str:
    """ "DE1234 · Name, Address, 12345 Town" """
    locality = " ".join(filter(None, [store.get("postal_code") or store.get("postalCode"), store.get("locality")]))
    details = ", ".join(filter(None, [store.get("name"), store.get("address"), locality]))
    return f"{store.get('id') or store.get('storeKey')} · {details}"


class LidlPlusOptionsFlow(OptionsFlow):
    """Choose the stores whose offers are loaded, found in the receipts or by a search."""

    def __init__(self) -> None:
        self._choices: dict[str, str] = {}
        self._selected: list[str] = []
        self._api_key: str | None = None

    def _schema(self, default: list[str]) -> vol.Schema:
        options = [SelectOptionDict(value=key, label=label) for key, label in self._choices.items()]
        return vol.Schema(
            {
                vol.Optional(CONF_OFFER_STORES, default=default): SelectSelector(
                    SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.LIST)
                ),
                vol.Optional("search"): str,
                vol.Optional(CONF_BESTTIME_API_KEY, description={"suggested_value": self._api_key}): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Stores of the receipts, the configured ones and a search for further stores."""
        errors: dict[str, str] = {}
        if self._api_key is None:
            self._api_key = self.config_entry.options.get(CONF_BESTTIME_API_KEY) or ""
        if not self._choices:
            coordinator = getattr(self.config_entry, "runtime_data", None)
            data = (coordinator.data if coordinator else None) or {}
            for store in data.get(KEY_STORES, []):
                self._choices[store["id"]] = f"{_store_label(store)} ({store['visits']}×)"
            for key in self.config_entry.options.get(CONF_OFFER_STORES, []):
                self._choices.setdefault(key, key)
        if user_input is not None:
            self._selected = user_input.get(CONF_OFFER_STORES, [])
            self._api_key = (user_input.get(CONF_BESTTIME_API_KEY) or "").strip()
            if query := (user_input.get("search") or "").strip():
                try:
                    found = await self.hass.async_add_executor_job(self._search, query)
                except requests.RequestException:
                    errors["base"] = "cannot_connect"
                else:
                    if not found:
                        errors["search"] = "no_stores"
                    for store in found:
                        self._choices.setdefault(store["storeKey"], _store_label(store))
            else:
                return self._async_save()
        default = self._selected or self.config_entry.options.get(CONF_OFFER_STORES, [])
        return self.async_show_form(step_id="init", data_schema=self._schema(default), errors=errors)

    def _search(self, query: str) -> list[dict]:
        api = LidlPlusApi(language=self.config_entry.data[CONF_LANGUAGE], country=self.config_entry.data[CONF_COUNTRY])
        # Nearest stores to the home of Home Assistant first
        stores = api.search_stores(query, self.hass.config.latitude, self.hass.config.longitude)
        return [store for store in stores if store.get("storeKey")]

    @callback
    def _async_save(self) -> ConfigFlowResult:
        # Without a selection the most visited store of the receipts is used
        options: dict[str, Any] = {CONF_OFFER_STORES: self._selected}
        if self._api_key:
            options[CONF_BESTTIME_API_KEY] = self._api_key
        if coordinator := getattr(self.config_entry, "runtime_data", None):
            # Load the offers of the new stores right away. The refresh starts before the flow manager saves
            # the result, so the options are saved here first (saving the same options again changes nothing).
            self.hass.config_entries.async_update_entry(self.config_entry, options=options)
            self.hass.async_create_task(coordinator.async_request_refresh())
        return self.async_create_entry(data=options)
