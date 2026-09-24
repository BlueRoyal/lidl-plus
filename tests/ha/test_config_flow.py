"""Tests for the config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import requests
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE, SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lidl_plus._lidlplus.exceptions import AuthenticationError
from custom_components.lidl_plus.const import (
    CONF_COUNTRY,
    CONF_LANGUAGE,
    CONF_OFFER_STORES,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)

from .conftest import LOYALTY_ID, FakeApiState

USER_INPUT = {CONF_COUNTRY: " de ", CONF_LANGUAGE: "DE", CONF_REFRESH_TOKEN: " new-token "}


def http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"HTTP {status}", response=response)


@pytest.fixture(autouse=True)
def mock_setup_entry():
    """Do not set up the entries created by the flows."""
    with patch("custom_components.lidl_plus.async_setup_entry", return_value=True) as mock:
        yield mock


async def test_user_flow(hass: HomeAssistant, api_state: FakeApiState) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Lidl Plus (DE)"
    # The validation renewed the token, so only the rotated one is still valid
    assert result["data"] == {CONF_COUNTRY: "DE", CONF_LANGUAGE: "de", CONF_REFRESH_TOKEN: "rotated-token"}
    assert result["result"].unique_id == LOYALTY_ID
    assert api_state.initial_tokens == ["new-token"]


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (AuthenticationError("invalid_grant"), "invalid_auth"),
        (http_error(401), "invalid_auth"),
        (http_error(404), "invalid_country"),
        (http_error(500), "cannot_connect"),
        (requests.ConnectionError("offline"), "cannot_connect"),
        (ValueError("unexpected"), "unknown"),
    ],
)
async def test_user_flow_errors(hass: HomeAssistant, api_state: FakeApiState, error: Exception, reason: str) -> None:
    api_state.error = error
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": reason}

    api_state.error = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_updates_token_of_configured_account(
    hass: HomeAssistant, api_state: FakeApiState, mock_setup_entry: AsyncMock
) -> None:
    # An entry whose setup failed, e.g. because the token was rejected at startup
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=LOYALTY_ID,
        data={CONF_COUNTRY: "DE", CONF_LANGUAGE: "de", CONF_REFRESH_TOKEN: "old-token"},
        state=ConfigEntryState.SETUP_ERROR,
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_REFRESH_TOKEN] == "rotated-token"
    # The entry is set up again with the new token
    assert len(mock_setup_entry.mock_calls) == 1


async def test_replaced_token_is_used_after_failed_validation(hass: HomeAssistant, api_state: FakeApiState) -> None:
    # The token is renewed before the receipts request fails because of the country
    api_state.tickets_error = http_error(404)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["errors"] == {"base": "invalid_country"}

    api_state.tickets_error = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**USER_INPUT, CONF_COUNTRY: "AT"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # The entered token may be invalid after the first attempt, its replacement is used
    assert api_state.initial_tokens == ["new-token", "rotated-token"]


async def test_reauth(hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry) -> None:
    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    api_state.error = AuthenticationError("invalid_grant")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_REFRESH_TOKEN: "bad"})
    assert result["errors"] == {"base": "invalid_auth"}

    api_state.error = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_REFRESH_TOKEN: "new-token"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_REFRESH_TOKEN] == "rotated-token"


async def test_reauth_with_other_account(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    api_state.loyalty_id = "4000000999999"
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_REFRESH_TOKEN: "new-token"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert config_entry.data[CONF_REFRESH_TOKEN] == "old-token"


async def test_reauth_of_legacy_entry(hass: HomeAssistant, api_state: FakeApiState) -> None:
    """Entries of versions before 1.2.0 use country and language as unique ID."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="DE_de",
        data={CONF_COUNTRY: "DE", CONF_LANGUAGE: "de", CONF_REFRESH_TOKEN: "old-token"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_REFRESH_TOKEN: "new-token"})
    assert result["reason"] == "reauth_successful"
    assert entry.unique_id == LOYALTY_ID


async def test_reauth_of_legacy_entry_of_configured_account(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    """The account was added again while its old entry could not be set up."""
    legacy = MockConfigEntry(
        domain=DOMAIN,
        unique_id="DE_de",
        data={CONF_COUNTRY: "DE", CONF_LANGUAGE: "de", CONF_REFRESH_TOKEN: "old-token"},
    )
    legacy.add_to_hass(hass)
    result = await legacy.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_REFRESH_TOKEN: "new-token"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert legacy.unique_id == "DE_de"


async def test_reconfigure(hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry) -> None:
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    # Without a new token the stored one is used
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COUNTRY: "at", CONF_LANGUAGE: "DE"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data == {CONF_COUNTRY: "AT", CONF_LANGUAGE: "de", CONF_REFRESH_TOKEN: "rotated-token"}
    assert api_state.initial_tokens == ["old-token"]


async def test_reconfigure_keeps_replaced_token_after_error(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    api_state.tickets_error = http_error(404)
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COUNTRY: "XX", CONF_LANGUAGE: "de"}
    )
    assert result["errors"] == {"base": "invalid_country"}
    # The stored token was replaced during the validation, the old one is invalid now
    assert config_entry.data[CONF_REFRESH_TOKEN] == "rotated-token"


async def test_reconfigure_with_other_account(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    api_state.loyalty_id = "4000000999999"
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COUNTRY: "DE", CONF_LANGUAGE: "de", CONF_REFRESH_TOKEN: "token-of-someone-else"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"


async def test_options_search_stores(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    # The stores found are added to the list to choose from
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_OFFER_STORES: [], "search": " Musterstadt "}
    )
    assert result["type"] is FlowResultType.FORM
    assert not result["errors"]
    assert result["data_schema"].schema[CONF_OFFER_STORES].config["options"] == [
        {"value": "DE3000", "label": "DE3000 · Lidl Süd, Südstraße 3, 12347 Musterstadt"}
    ]

    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_OFFER_STORES: ["DE3000"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_OFFER_STORES: ["DE3000"]}

    # The chosen stores are shown again next time, also without receipts
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["data_schema"].schema[CONF_OFFER_STORES].config["options"] == [{"value": "DE3000", "label": "DE3000"}]


async def test_options_search_errors(
    hass: HomeAssistant, api_state: FakeApiState, config_entry: MockConfigEntry
) -> None:
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    api_state.found_stores = []
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_OFFER_STORES: [], "search": "Nirgendwo"}
    )
    assert result["errors"] == {"search": "no_stores"}

    api_state.public_error = requests.ConnectionError("offline")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_OFFER_STORES: [], "search": "Musterstadt"}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    # Saving without a store: the most visited store of the receipts is used
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_OFFER_STORES: []})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_OFFER_STORES: []}


async def test_flows_without_loyalty_id(hass: HomeAssistant, api_state: FakeApiState) -> None:
    """The loyalty endpoint fails for some accounts, a token must still be accepted"""
    api_state.loyalty_error = http_error(404)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # Identified by country and language like the entries of older versions
    entry = result["result"]
    assert entry.unique_id == "DE_de"

    # Adding it again only replaces the token
    api_state.rotated_token = "rotated-2"
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_REFRESH_TOKEN] == "rotated-2"

    api_state.rotated_token = "rotated-3"
    context = {"entry_id": entry.entry_id, "unique_id": entry.unique_id}
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_REAUTH, **context}, data=entry.data
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_REFRESH_TOKEN: "new-token"})
    assert result["reason"] == "reauth_successful"
    assert (entry.unique_id, entry.data[CONF_REFRESH_TOKEN]) == ("DE_de", "rotated-3")

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_RECONFIGURE, **context})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COUNTRY: "DE", CONF_LANGUAGE: "de", CONF_REFRESH_TOKEN: "another-token"}
    )
    assert result["reason"] == "reconfigure_successful"
    assert entry.unique_id == "DE_de"
