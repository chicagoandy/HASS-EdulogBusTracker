"""Config flow for the Edulog Bus Tracker integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EdulogApi, EdulogApiError, EdulogAuthError, EdulogHttpError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def _get_auth_token(
    hass: HomeAssistant, email: str, password: str
) -> tuple[str, str, str]:
    api = EdulogApi(async_get_clientsession(hass))
    await api.async_login(email, password)
    if (
        api.access_token is None
        or api.refresh_token is None
        or api.user_id is None
    ):
        raise EdulogAuthError("The login response was incomplete.")
    return api.access_token, api.refresh_token, api.user_id


async def _async_try_login(
    hass: HomeAssistant, user_input: Mapping[str, Any]
) -> tuple[tuple[str, str, str] | None, str | None]:
    """Return the tokens and account ID, or the form error key."""
    try:
        return (
            await _get_auth_token(
                hass, user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
            ),
            None,
        )
    except EdulogHttpError as err:
        return None, (
            "invalid_auth" if err.status in (400, 401, 403) else "cannot_connect"
        )
    except EdulogAuthError:
        return None, "invalid_auth"
    except (EdulogApiError, aiohttp.ClientError, TimeoutError):
        return None, "cannot_connect"
    except Exception:
        _LOGGER.exception("Unexpected error while signing in to Edulog")
        return None, "unknown"


class EdulogConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Edulog Bus Tracker."""

    VERSION = 4

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Exchange transient credentials for an access token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result, error = await _async_try_login(self.hass, user_input)
            if result is None:
                errors["base"] = error or "unknown"
            else:
                access_token, refresh_token, user_id = result
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_EMAIL],
                    data={
                        "auth_token": access_token,
                        "refresh_token": refresh_token,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=CREDENTIALS_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Reauthenticate an expired access token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Obtain a replacement token without saving credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result, error = await _async_try_login(self.hass, user_input)
            if result is None:
                errors["base"] = error or "unknown"
            else:
                access_token, refresh_token, user_id = result
                reauth_entry = self._get_reauth_entry()
                if user_id != reauth_entry.unique_id:
                    errors["base"] = "wrong_account"
                else:
                    return self.async_update_reload_and_abort(
                        reauth_entry,
                        data={
                            "auth_token": access_token,
                            "refresh_token": refresh_token,
                        },
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=CREDENTIALS_SCHEMA,
            errors=errors,
        )
