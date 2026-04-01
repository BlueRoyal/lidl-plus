"""Config flow for Lidl Plus."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import CONF_COUNTRY, CONF_LANGUAGE, CONF_REFRESH_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_COUNTRY, default="DE"): str,
        vol.Required(CONF_LANGUAGE, default="de"): str,
        vol.Required(CONF_REFRESH_TOKEN): str,
    }
)


class LidlPlusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Lidl Plus."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            from ._lidlplus.api import LidlPlusApi

            api = LidlPlusApi(
                language=user_input[CONF_LANGUAGE],
                country=user_input[CONF_COUNTRY],
                refresh_token=user_input[CONF_REFRESH_TOKEN],
            )
            try:
                await self.hass.async_add_executor_job(api._renew_token)
            except Exception:  # noqa: BLE001
                errors["base"] = "invalid_auth"
            else:
                await self.async_set_unique_id(
                    f"{user_input[CONF_COUNTRY].upper()}_{user_input[CONF_LANGUAGE].lower()}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Lidl Plus ({user_input[CONF_COUNTRY].upper()})",
                    data={
                        CONF_COUNTRY: user_input[CONF_COUNTRY].upper(),
                        CONF_LANGUAGE: user_input[CONF_LANGUAGE].lower(),
                        CONF_REFRESH_TOKEN: user_input[CONF_REFRESH_TOKEN],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
            description_placeholders={
                "token_help": (
                    "Token holen: pip install lidl-plus[auth] "
                    "→ lidl-plus --language=de --country=DE --user=email auth"
                )
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> LidlPlusOptionsFlow:
        return LidlPlusOptionsFlow(config_entry)


class LidlPlusOptionsFlow(config_entries.OptionsFlow):
    """Update the refresh token when it expires."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_REFRESH_TOKEN: user_input[CONF_REFRESH_TOKEN]},
            )
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(CONF_REFRESH_TOKEN): str}
            ),
        )
