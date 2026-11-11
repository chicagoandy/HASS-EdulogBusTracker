"""Tests for the Edulog Bus Tracker config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.edulog_bus_tracker.const import DOMAIN

from .conftest import LOGIN_URL, USER_ID, make_token

CREDENTIALS = {CONF_EMAIL: "parent@example.com", CONF_PASSWORD: "secret"}


def mock_login(aioclient_mock: AiohttpClientMocker, subject: str = USER_ID) -> None:
    aioclient_mock.post(
        LOGIN_URL,
        json={
            "accessToken": make_token(subject),
            "refreshToken": "refresh",
            "personId": subject,
        },
    )


async def test_user_flow_creates_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Signing in stores only the tokens and uses the email as the title."""
    mock_login(aioclient_mock)
    with patch(
        "custom_components.edulog_bus_tracker.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "parent@example.com"
    assert result["result"].unique_id == USER_ID
    assert set(result["data"]) == {"auth_token", "refresh_token"}


async def test_user_flow_invalid_auth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Rejected credentials show invalid_auth."""
    aioclient_mock.post(LOGIN_URL, status=401)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_unexpected_error(hass: HomeAssistant) -> None:
    """An unexpected exception shows unknown instead of crashing the flow."""
    with patch(
        "custom_components.edulog_bus_tracker.config_flow._get_auth_token",
        side_effect=RuntimeError,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )
    assert result["errors"] == {"base": "unknown"}


async def test_reauth_updates_tokens(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Reauthenticating with the same account replaces the tokens."""
    config_entry.add_to_hass(hass)
    mock_login(aioclient_mock)
    with patch(
        "custom_components.edulog_bus_tracker.async_setup_entry", return_value=True
    ):
        result = await config_entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["auth_token"] == make_token()


async def test_reauth_rejects_other_account(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Signing in to a different account during reauth is refused."""
    config_entry.add_to_hass(hass)
    mock_login(aioclient_mock, subject="someone-else")
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )
    assert result["errors"] == {"base": "wrong_account"}
