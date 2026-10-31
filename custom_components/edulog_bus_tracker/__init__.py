"""Edulog Bus Tracker integration."""

from __future__ import annotations

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EdulogApi, EdulogHttpError
from .const import DOMAIN
from .coordinator import EdulogCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.DEVICE_TRACKER, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Edulog from a config entry."""
    api = EdulogApi(
        async_get_clientsession(hass),
        entry.data.get("auth_token"),
        user_id=entry.unique_id,
    )
    coordinator = EdulogCoordinator(hass, api, entry.entry_id)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        raise
    except EdulogHttpError as err:
        raise ConfigEntryNotReady("Edulog API is temporarily unavailable") from err
    except (aiohttp.ClientError, TimeoutError) as err:
        raise ConfigEntryNotReady("Could not connect to Edulog") from err
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an Edulog config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate older entries and remove credentials and account IDs."""
    if entry.version == 1:
        hass.config_entries.async_update_entry(
            entry,
            data={},
            version=2,
        )
        return True
    if entry.version == 2:
        hass.config_entries.async_update_entry(
            entry,
            data={"auth_token": entry.data["auth_token"]}
            if entry.data.get("auth_token")
            else {},
            version=3,
        )
        return True

    return False
