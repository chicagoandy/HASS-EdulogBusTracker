"""Config flow for the Edulog Bus Tracker integration."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EdulogApi, EdulogAuthError, EdulogHttpError
from .const import DOMAIN, REAUTH_ISSUE_ID_PREFIX


async def _get_auth_token(
    hass: HomeAssistant, email: str, password: str
) -> tuple[str, str]:
    api = EdulogApi(async_get_clientsession(hass))
    await api.async_login(email, password)
    if api.access_token is None or api.user_id is None:
        raise EdulogAuthError("The login response was incomplete.")
    return api.access_token, api.user_id


def _login_error(err: Exception) -> str:
    if isinstance(err, EdulogHttpError):
        return "invalid_auth" if err.status in (400, 401, 403) else "cannot_connect"
    if isinstance(err, EdulogAuthError):
        return "invalid_auth"
    return "cannot_connect"


class EdulogConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Edulog Bus Tracker."""

    VERSION = 3

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Exchange transient credentials for an access token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                access_token, user_id = await _get_auth_token(
                    self.hass,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
            except (EdulogAuthError, EdulogHttpError) as err:
                errors["base"] = _login_error(err)
            except (aiohttp.ClientError, TimeoutError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Edulog Parent Portal",
                    data={"auth_token": access_token},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_credentials_schema(),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> FlowResult:
        """Reauthenticate an expired access token."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Obtain a replacement token without saving credentials."""
        if self._reauth_entry is None:
            return self.async_abort(reason="reauth_entry_not_found")

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                access_token, user_id = await _get_auth_token(
                    self.hass,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
            except (EdulogAuthError, EdulogHttpError) as err:
                errors["base"] = _login_error(err)
            except (aiohttp.ClientError, TimeoutError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                if user_id != self._reauth_entry.unique_id:
                    errors["base"] = "wrong_account"
                else:
                    self.hass.config_entries.async_update_entry(
                        self._reauth_entry,
                        data={"auth_token": access_token},
                    )
                    ir.async_delete_issue(
                        self.hass,
                        DOMAIN,
                        f"{REAUTH_ISSUE_ID_PREFIX}_{self._reauth_entry.entry_id}",
                    )
                    await self.hass.config_entries.async_reload(
                        self._reauth_entry.entry_id
                    )
                    return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_credentials_schema(),
            errors=errors,
        )


def _credentials_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
        }
    )
