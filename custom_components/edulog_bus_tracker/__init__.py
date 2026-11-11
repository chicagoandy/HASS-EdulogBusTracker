"""Edulog Bus Tracker integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EdulogApi
from .const import DOMAIN, REAUTH_ISSUE_ID_PREFIX
from .coordinator import EdulogCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.DEVICE_TRACKER, Platform.SENSOR]

type EdulogConfigEntry = ConfigEntry[EdulogCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: EdulogConfigEntry) -> bool:
    """Set up Edulog from a config entry."""

    def update_tokens(access_token: str, refresh_token: str) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                "auth_token": access_token,
                "refresh_token": refresh_token,
            },
        )

    # Earlier releases raised a custom repair issue next to the reauth flow.
    # Home Assistant's own reauth prompt replaces it, so clear any leftover.
    ir.async_delete_issue(hass, DOMAIN, f"{REAUTH_ISSUE_ID_PREFIX}_{entry.entry_id}")

    api = EdulogApi(
        async_get_clientsession(hass),
        entry.data.get("auth_token"),
        user_id=entry.unique_id,
        refresh_token=entry.data.get("refresh_token"),
        token_update_callback=update_tokens,
    )
    coordinator = EdulogCoordinator(hass, entry, api)
    # The coordinator turns API failures into ConfigEntryNotReady and auth
    # failures into ConfigEntryAuthFailed during the first refresh.
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EdulogConfigEntry) -> bool:
    """Unload an Edulog config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate older config-entry data formats, one version at a time."""
    if entry.version == 1:
        hass.config_entries.async_update_entry(entry, data={}, version=2)
    if entry.version == 2:
        hass.config_entries.async_update_entry(
            entry,
            data={"auth_token": entry.data["auth_token"]}
            if entry.data.get("auth_token")
            else {},
            version=3,
        )
    if entry.version == 3:
        hass.config_entries.async_update_entry(
            entry, data=dict(entry.data), version=4
        )
    return entry.version == 4
